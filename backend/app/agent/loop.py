"""The goal-driven agent loop.

OBSERVE (source + schema) -> DEFINE GOAL -> [PLAN -> GENERATE SQL -> VALIDATE -> (APPROVE) -> EXECUTE ->
EVALUATE -> REFINE]* -> EXPORT / APPLY -> VERIFY -> COMPLETE

The language model only proposes JSON (goal, SQL, evaluation). All execution and file I/O is done here,
and only after validation.
"""
from __future__ import annotations

import copy
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import sqlglot
from sqlglot import exp

from ..config import Settings
from ..datasources.base import ColumnInfo, DataSourceError, QueryExecutionError, SchemaInfo
from ..datasources.registry import SourceEntry, SourceRegistry
from ..datasources.spreadsheet import SpreadsheetSource, StaleSourceError
from ..export.exporter import (CSV_MEDIA, XLSM_MEDIA, XLSX_MEDIA, ExportMetadata, ExportStore, slugify,
                               verify_xlsx, write_csv, write_xlsx)
from ..llm.provider import LLMError, LLMNotConfiguredError, LLMProvider, LLMResponseError
from ..llm.schemas import GoalInterpretation, ResultEvaluation, SqlProposal
from ..security.sql_validator import (ValidationResult, normalize_for_dedupe, validate_read_query,
                                      validate_write_statement)
from . import prompts
from .run import AgentRun, Phase, RunCancelled, RunStatus

log = logging.getLogger("loopsql.agent")

# Paths/links users paste into a request, e.g. C:\Users\me\marks.xlsx, "D:\data\x.csv", file:///C:/a.xlsx
_PATH_RE = re.compile(
    r"""(?P<q>["'])?(?P<path>(?:file:///?)?(?:[A-Za-z]:[\\/]|\\\\|/)[^"'<>|\r\n]*?\.(?:xlsx|xlsm|csv|db|sqlite3?|db3))(?(q)(?P=q))""",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

MAX_CLARIFICATIONS = 2


class Agent:
    def __init__(self, settings: Settings, registry: SourceRegistry, exports: ExportStore,
                 llm_factory: Callable[[], LLMProvider], today: Callable[[], date] = date.today):
        self.settings = settings
        self.registry = registry
        self.exports = exports
        self.llm_factory = llm_factory
        self.today = today

    # =====================================================================================
    def run(self, run: AgentRun) -> None:
        run.status = RunStatus.RUNNING
        try:
            self._run(run)
        except RunCancelled:
            run.status = RunStatus.CANCELLED
            run.emit(Phase.FAILED, "Run cancelled by user.", "warning")
        except TimeoutError as e:
            self._fail(run, str(e))
        except Exception as e:  # last-resort guard: report, never crash the worker
            log.exception("Agent run %s crashed", run.id)
            self._fail(run, f"Unexpected error: {type(e).__name__}: {e}")
        finally:
            run.finished_at = datetime.now().isoformat(timespec="seconds")
            run.pending = None

    def _fail(self, run: AgentRun, message: str, status: str = RunStatus.FAILED) -> None:
        run.errors.append(message)
        run.status = status
        run.emit(Phase.FAILED, message, "error")

    # =====================================================================================
    def _run(self, run: AgentRun) -> None:
        # ---------------- OBSERVE ----------------
        run.emit(Phase.OBSERVING, "Reading the request and locating the data source.")
        entry = self._resolve_source(run)
        adapter = entry.adapter
        if isinstance(adapter, SpreadsheetSource):
            try:
                if adapter.refresh_if_changed():
                    run.emit(Phase.OBSERVING, "The file changed on disk since it was connected; re-imported it.")
            except DataSourceError as e:
                return self._fail(run, str(e))
        run.source = entry.to_dict()

        run.emit(Phase.INSPECTING, f"Inspecting schema of {run.source.get('name')}.")
        try:
            adapter.schema_sample_rows = self.settings.schema_sample_rows  # type: ignore[attr-defined]
            schema = adapter.inspect_schema()
        except DataSourceError as e:
            return self._fail(run, str(e))
        if not schema.tables:
            return self._fail(run, "The data source has no tables or sheets to query.", RunStatus.NEEDS_INPUT)
        run.schema_summary = [{"name": t.name, "kind": t.kind, "columns": len(t.columns), "rows": t.row_count}
                              for t in schema.tables]
        run.emit(Phase.INSPECTING, f"Found {len(schema.tables)} table(s): "
                 + ", ".join(t.name for t in schema.tables[:12]) + ("..." if len(schema.tables) > 12 else ""),
                 "success")
        schema_text = schema.to_prompt(self.settings.schema_sample_rows)

        # ---------------- DEFINE GOAL ----------------
        try:
            llm = self.llm_factory()
        except LLMNotConfiguredError as e:
            return self._fail(run, str(e))
        run.llm = getattr(llm, "name", "llm")
        goal = self._define_goal(run, llm, schema_text)
        if goal is None:
            return
        mode = goal.operation
        if mode == "modify" and not getattr(adapter, "editable", False):
            kind = run.source.get("kind")
            msg = ("Modifying data is only supported for Excel workbooks (.xlsx/.xlsm). "
                   + ("This CSV file is read-only; save it as .xlsx to edit it."
                      if kind == "csv" else "Databases are connected read-only for safety."))
            return self._fail(run, msg, RunStatus.BLOCKED)

        # ---------------- ITERATE ----------------
        system = prompts.system_prompt(schema.dialect, self.today().isoformat(), mode)
        seen: dict[str, int] = {}
        consecutive_dupes = 0
        for iteration in range(1, run.max_iterations + 1):
            run.check_cancelled()
            run.iteration = iteration
            phase = Phase.PLANNING if iteration == 1 else Phase.REFINING
            run.emit(phase, "Planning the query." if iteration == 1 else
                     f"Refining the query (attempt {iteration} of {run.max_iterations}).")
            try:
                proposal = llm.generate_structured(
                    task="generate_sql", system=system,
                    prompt=prompts.sql_prompt(run.goal or {}, run.request, schema_text, run.attempts),
                    schema=SqlProposal)
            except LLMResponseError as e:
                self._record_attempt(run, iteration, None, {}, "model_error", str(e),
                                     "Respond with valid JSON matching the schema.")
                continue
            except LLMError as e:
                return self._fail(run, str(e))

            params = proposal.params_dict()
            run.plan, run.sql, run.params = proposal.plan, proposal.sql, params
            run.emit(phase, f"Generated SQL (attempt {iteration}).", detail={
                "sql": proposal.sql, "params": params, "plan": proposal.plan, "diagnosis": proposal.diagnosis})

            # New-column declarations are part of the attempt: the same UPDATE with a newly declared column
            # is a genuine correction, not a repeat.
            key = normalize_for_dedupe(proposal.sql, schema.dialect, params) + "|" + repr(
                sorted((c.name.lower(), c.type) for c in proposal.new_columns))
            if key in seen:
                consecutive_dupes += 1
                prev = seen[key]
                run.emit(Phase.VALIDATING, f"Attempt {iteration} repeats attempt {prev}; not executing it again.",
                         "warning")
                self._record_attempt(run, iteration, proposal.sql, params, "duplicate",
                                     f"Identical to attempt {prev}.",
                                     "Write a materially different query that addresses the earlier feedback.")
                if consecutive_dupes >= 2:
                    return self._fail(run, "Stopped: the model kept proposing the same query.")
                continue
            seen[key] = iteration
            consecutive_dupes = 0

            if mode == "read":
                done = self._read_iteration(run, llm, adapter, schema, proposal, params, iteration)
            else:
                done = self._modify_iteration(run, llm, adapter, schema, proposal, params, iteration)  # type: ignore[arg-type]
            if done:
                return
            if run.status in RunStatus.TERMINAL:
                return

        self._fail(run, f"Could not reach a satisfactory result within {run.max_iterations} iterations. "
                        "See the attempts for details; you can rephrase the request and try again.")

    # =====================================================================================
    def _resolve_source(self, run: AgentRun) -> SourceEntry:
        if run.options.source_id:
            try:
                return self.registry.get(run.options.source_id)
            except KeyError:
                run.emit(Phase.OBSERVING, "The selected data source is no longer connected.", "warning")
        # A file path inside the request text?
        m = _PATH_RE.search(run.request)
        problem = None
        if m:
            raw = m.group("path")
            try:
                entry = self.registry.add_local_path(raw)
                run.options.source_id = entry.id
                run.emit(Phase.OBSERVING, f"Connected to the file mentioned in your request: {entry.adapter.describe().get('name')}.",
                         "success")
                return entry
            except DataSourceError as e:
                problem = str(e)
        elif _URL_RE.search(run.request):
            from ..datasources.registry import normalize_user_path
            try:
                normalize_user_path(_URL_RE.search(run.request).group(0))
            except DataSourceError as e:
                problem = str(e)
        # Ask the user.
        question = "Which Excel file or database should I use? Paste the file's local path, upload the file, " \
                   "or pick a connected source."
        while True:
            run.emit(Phase.OBSERVING, (problem + " " if problem else "") + question, "warning" if problem else "info")
            resp = run.wait_for_user({"type": "source", "message": question, "problem": problem},
                                     RunStatus.AWAITING_INPUT, self.settings.approval_timeout_seconds)
            try:
                entry = self.registry.get(str(resp.get("source_id")))
                run.options.source_id = entry.id
                run.emit(Phase.OBSERVING, f"Using {entry.adapter.describe().get('name')}.", "success")
                return entry
            except KeyError:
                problem = "That source is not connected."

    def _define_goal(self, run: AgentRun, llm: LLMProvider, schema_text: str) -> GoalInterpretation | None:
        while True:
            run.check_cancelled()
            run.emit(Phase.DEFINING_GOAL, "Interpreting the request and defining success criteria.")
            try:
                goal = llm.generate_structured(
                    task="interpret", system=prompts.system_prompt("sql", self.today().isoformat(), "read"),
                    prompt=prompts.interpret_prompt(run.request, schema_text, run.source or {}, run.clarifications),
                    schema=GoalInterpretation)
            except LLMError as e:
                self._fail(run, str(e))
                return None
            run.goal = goal.model_dump()
            if goal.needs_clarification and goal.clarification_question and len(run.clarifications) < MAX_CLARIFICATIONS:
                q = goal.clarification_question
                run.emit(Phase.DEFINING_GOAL, f"Question for you: {q}", "warning")
                resp = run.wait_for_user({"type": "clarification", "question": q}, RunStatus.AWAITING_INPUT,
                                         self.settings.approval_timeout_seconds)
                answer = str(resp.get("answer") or "").strip()[:2000]
                if not answer:
                    answer = "No preference - use a sensible default."
                run.clarifications.append({"question": q, "answer": answer})
                continue
            if not goal.feasible:
                reason = goal.infeasible_reason or "The connected data does not contain what this request needs."
                run.summary = reason
                self._fail(run, reason, RunStatus.NEEDS_INPUT)
                return None
            run.emit(Phase.DEFINING_GOAL, f"Goal: {goal.interpreted_goal}", "success",
                     detail={"operation": goal.operation, "success_criteria": goal.success_criteria,
                             "requested_columns": goal.requested_columns})
            return goal

    def _record_attempt(self, run: AgentRun, iteration: int, sql: str | None, params: dict[str, Any],
                        outcome: str, error: str, feedback: str) -> None:
        if error:
            run.errors.append(f"Attempt {iteration}: {error}")
        run.attempts.append({"iteration": iteration, "sql": sql, "params": params, "outcome": outcome,
                             "error": error, "feedback": feedback})

    def _validation_failed(self, run: AgentRun, v: ValidationResult, iteration: int, sql: str,
                           params: dict[str, Any], mode: str = "read") -> None:
        msg = "; ".join(v.errors)
        if v.security_violation:
            self._record_attempt(run, iteration, sql, params, "blocked", msg, "")
            self._fail(run, f"Blocked by SQL security policy: {msg}", RunStatus.BLOCKED)
            return
        run.emit(Phase.VALIDATING, f"Validation failed: {msg}", "error", detail={"errors": v.errors})
        feedback = "Fix these validation errors using only identifiers from the schema."
        if mode == "modify":
            feedback = ("Fix these validation errors. Use existing columns from the schema, or - if the user asked "
                        "for a new column - declare it in new_columns and then set it.")
        self._record_attempt(run, iteration, sql, params, "validation_error", msg, feedback)

    # =====================================================================================
    def _read_iteration(self, run: AgentRun, llm: LLMProvider, adapter: Any, schema: SchemaInfo,
                        proposal: SqlProposal, params: dict[str, Any], iteration: int) -> bool:
        run.emit(Phase.VALIDATING, "Validating SQL (single read-only statement, known tables/columns, parameters).")
        v = validate_read_query(proposal.sql, schema, params)
        run.validation = v.to_dict()
        if not v.ok:
            self._validation_failed(run, v, iteration, proposal.sql, params)
            return False
        run.emit(Phase.VALIDATING, "SQL passed validation." + (f" Notes: {'; '.join(v.warnings)}" if v.warnings else ""),
                 "success", detail={"tables": v.tables})
        sql = v.sql or proposal.sql
        bound = {k: params[k] for k in v.placeholders}

        if run.options.require_approval:
            approved = self._await_sql_approval(run, schema, sql, bound, proposal.plan)
            if approved is None:
                return True
            sql, bound = approved
            run.sql, run.params = sql, bound

        run.check_cancelled()
        run.emit(Phase.EXECUTING, "Executing the approved query (read-only connection).")
        try:
            result = adapter.execute_read(sql, bound, self.settings.max_result_rows, self.settings.query_timeout_seconds)
        except QueryExecutionError as e:
            if not e.recoverable:
                self._record_attempt(run, iteration, sql, bound, "blocked", str(e), "")
                self._fail(run, str(e), RunStatus.BLOCKED)
                return True
            run.emit(Phase.EXECUTING, f"Database error: {e}", "error")
            self._record_attempt(run, iteration, sql, bound, "execution_error", str(e),
                                 "Diagnose this database error and correct the query.")
            return False
        run.columns, run.rows = result.columns, result.rows
        run.result_meta = {"columns": result.columns, "row_count": len(result.rows), "truncated": result.truncated,
                           "elapsed_ms": result.elapsed_ms, "row_limit": self.settings.max_result_rows}
        note = f" (limited to the first {self.settings.max_result_rows:,} rows)" if result.truncated else ""
        run.emit(Phase.EXECUTING, f"Query returned {len(result.rows):,} row(s){note} in {result.elapsed_ms:.0f} ms.",
                 "success")

        # ---------------- EVALUATE ----------------
        run.emit(Phase.EVALUATING, "Checking the result against the goal.")
        preview = [dict(zip(result.columns, r)) for r in result.rows[: self.settings.llm_result_preview_rows]]
        try:
            ev = llm.generate_structured(
                task="evaluate", system=prompts.system_prompt(schema.dialect, self.today().isoformat(), "read"),
                prompt=prompts.evaluate_prompt(run.goal or {}, run.request, sql, bound, result.columns,
                                               len(result.rows), result.truncated, preview),
                schema=ResultEvaluation)
        except LLMError as e:
            run.emit(Phase.EVALUATING, f"Model evaluation unavailable ({e}); accepting the validated result.",
                     "warning")
            ev = ResultEvaluation(satisfied=True, summary=f"Returned {len(result.rows)} row(s).",
                                  issues=["Automatic evaluation was skipped because the model was unavailable."])
        run.evaluation = ev.model_dump()
        if not ev.satisfied:
            feedback = ev.refinement_instructions or "; ".join(ev.issues) or "Result does not satisfy the goal."
            run.emit(Phase.EVALUATING, f"Result does not meet the goal yet: {feedback}", "warning")
            self._record_attempt(run, iteration, sql, bound, "unsatisfied", "; ".join(ev.issues), feedback)
            return False
        run.summary = ev.summary
        run.column_hints = {cf.column: (cf.label, cf.kind) for cf in ev.column_formats}
        run.emit(Phase.EVALUATING, "Result satisfies the goal.", "success")
        self._record_attempt(run, iteration, sql, bound, "success", "", "")

        # ---------------- EXPORT + VERIFY ----------------
        if not self.export_results(run):
            return True
        run.status = RunStatus.COMPLETED
        run.emit(Phase.COMPLETED, f"Done. {len(run.rows):,} record(s) exported to Excel and CSV.", "success")
        return True

    def _await_sql_approval(self, run: AgentRun, schema: SchemaInfo, sql: str, params: dict[str, Any],
                            plan: list[str]) -> tuple[str, dict[str, Any]] | None:
        error = None
        while True:
            run.emit(Phase.AWAITING_APPROVAL, "Waiting for you to review and approve the SQL.", "warning")
            resp = run.wait_for_user({"type": "sql_approval", "sql": sql, "params": params, "plan": plan, "error": error},
                                     RunStatus.AWAITING_APPROVAL, self.settings.approval_timeout_seconds)
            if not resp.get("approved"):
                run.status = RunStatus.CANCELLED
                run.emit(Phase.FAILED, "SQL rejected by user. Nothing was executed.", "warning")
                return None
            edited = (resp.get("sql") or "").strip()
            if edited and edited != sql:
                v = validate_read_query(edited, schema, params)
                if not v.ok:
                    error = "Your edited SQL was rejected: " + "; ".join(v.errors)
                    run.emit(Phase.VALIDATING, error, "error")
                    continue
                run.emit(Phase.VALIDATING, "Your edited SQL passed validation.", "success")
                return v.sql or edited, {k: params[k] for k in v.placeholders}
            run.emit(Phase.AWAITING_APPROVAL, "SQL approved.", "success")
            return sql, params

    def export_results(self, run: AgentRun) -> bool:
        run.export_status = "exporting"
        run.emit(Phase.EXPORTING, "Writing Excel workbook and CSV file.")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"loopsql_{slugify((run.goal or {}).get('interpreted_goal') or run.request)}_{stamp}"
        meta = ExportMetadata(request=run.request, goal=(run.goal or {}).get("interpreted_goal", ""),
                              source=f"{(run.source or {}).get('name')} ({(run.source or {}).get('target')})",
                              sql=run.sql or "", params=run.params, truncated=bool((run.result_meta or {}).get("truncated")),
                              row_limit=self.settings.max_result_rows, summary=run.summary)
        try:
            xid, xpath = self.exports.new_path(base + ".xlsx")
            n = write_xlsx(xpath, run.columns, run.rows, meta, run.column_hints)
            run.emit(Phase.VERIFYING, "Re-opening the workbook to verify its contents.")
            verify_xlsx(xpath, run.columns, n)
            xf = self.exports.register(xid, xpath, XLSX_MEDIA, n)
            cid, cpath = self.exports.new_path(base + ".csv")
            cn = write_csv(cpath, run.columns, run.rows)
            cf = self.exports.register(cid, cpath, CSV_MEDIA, cn)
        except Exception as e:
            run.export_status = "failed"
            self._fail(run, f"Export failed: {type(e).__name__}: {e}")
            return False
        run.exports = [xf.to_dict() | {"format": "xlsx"}, cf.to_dict() | {"format": "csv"}]
        run.export_status = "done"
        run.emit(Phase.VERIFYING, f"Verified workbook: {n:,} data row(s), header matches.", "success")
        return True

    # =====================================================================================
    def _modify_iteration(self, run: AgentRun, llm: LLMProvider, adapter: SpreadsheetSource, schema: SchemaInfo,
                          proposal: SqlProposal, params: dict[str, Any], iteration: int) -> bool:
        run.emit(Phase.VALIDATING, "Validating the modification (one UPDATE/INSERT/DELETE on one sheet).")
        target = _write_target(proposal.sql, schema.dialect)
        new_cols = []
        aug_schema = schema
        if target and proposal.new_columns:
            try:
                new_cols = adapter.plan_new_columns(target, [c.model_dump() for c in proposal.new_columns])
            except (QueryExecutionError, KeyError) as e:
                self._record_attempt(run, iteration, proposal.sql, params, "validation_error", str(e),
                                     "Fix the new column definitions.")
                return False
            aug_schema = copy.deepcopy(schema)
            t = aug_schema.table_map().get(target.lower())
            if t is not None:
                t.columns.extend(ColumnInfo(c.name, c.sql_type) for c in new_cols)
        v = validate_write_statement(proposal.sql, aug_schema, {t.table for t in adapter.tables},
                                     adapter.read_only_columns(), params)
        run.validation = v.to_dict()
        if not v.ok:
            self._validation_failed(run, v, iteration, proposal.sql, params, mode="modify")
            return False
        run.emit(Phase.VALIDATING, "Modification passed validation.", "success")
        bound = {k: params[k] for k in v.placeholders}

        run.emit(Phase.EXECUTING, "Dry-running the change on a private copy (nothing is written yet).")
        try:
            cs = adapter.dry_run(v.sql or proposal.sql, bound, v.target_table or target or "",
                                 self.settings.max_modified_rows, self.settings.query_timeout_seconds, new_cols)
        except QueryExecutionError as e:
            if not e.recoverable:
                self._fail(run, str(e), RunStatus.BLOCKED)
                return True
            run.emit(Phase.EXECUTING, f"Dry run failed: {e}", "error")
            self._record_attempt(run, iteration, proposal.sql, bound, "execution_error", str(e),
                                 "Diagnose this error and correct the statement.")
            return False
        change = cs.to_dict()
        run.change_preview = change
        run.result_meta = {"columns": [], "row_count": cs.affected_rows, "truncated": False, "elapsed_ms": 0,
                           "row_limit": self.settings.max_modified_rows}
        run.emit(Phase.EXECUTING, f"Dry run: {cs.affected_rows} row(s) / {len(cs.updates)} cell(s) would change"
                 + (f", {len(cs.inserted_rows)} added" if cs.inserted_rows else "")
                 + (f", {len(cs.deleted_rows)} deleted" if cs.deleted_rows else "") + ".", "success")

        run.emit(Phase.EVALUATING, "Checking the proposed change against the goal.")
        try:
            ev = llm.generate_structured(
                task="evaluate_change", system=prompts.system_prompt(schema.dialect, self.today().isoformat(), "modify"),
                prompt=prompts.evaluate_change_prompt(run.goal or {}, run.request, cs.sql, change),
                schema=ResultEvaluation)
        except LLMError as e:
            run.emit(Phase.EVALUATING, f"Model evaluation unavailable ({e}); you will review the change yourself.",
                     "warning")
            ev = ResultEvaluation(satisfied=True, summary=f"{cs.affected_rows} row(s) would change.")
        run.evaluation = ev.model_dump()
        if not ev.satisfied:
            feedback = ev.refinement_instructions or "; ".join(ev.issues) or "The change does not match the goal."
            run.emit(Phase.EVALUATING, f"Change does not match the goal yet: {feedback}", "warning")
            self._record_attempt(run, iteration, cs.sql, bound, "unsatisfied", "; ".join(ev.issues), feedback)
            return False
        run.summary = ev.summary
        self._record_attempt(run, iteration, cs.sql, bound, "success", "", "")
        if cs.affected_rows == 0 and not cs.new_columns:
            run.status = RunStatus.COMPLETED
            run.export_status = "not_applicable"
            run.emit(Phase.COMPLETED, "No rows match, so nothing needs to change. The file was not modified.", "success")
            return True

        # ---------------- APPROVAL (always required for changes) ----------------
        can_in_place = adapter.origin == "path"
        default_mode = run.options.write_mode if run.options.write_mode in ("copy", "in_place", "browser") else "copy"
        if default_mode == "in_place" and not can_in_place:
            default_mode = "copy"
        run._change_set = cs
        run.emit(Phase.AWAITING_APPROVAL, "Review the proposed change. Nothing is written until you approve.", "warning")
        resp = run.wait_for_user({"type": "change_approval", "change": change, "default_mode": default_mode,
                                  "can_in_place": can_in_place, "file": adapter.file_path.name},
                                 RunStatus.AWAITING_APPROVAL, self.settings.approval_timeout_seconds)
        if not resp.get("approved"):
            run.status = RunStatus.CANCELLED
            run.emit(Phase.FAILED, "Change rejected by user. The file was not modified.", "warning")
            return True
        mode = resp.get("mode") or default_mode
        if mode == "in_place" and not can_in_place:
            mode = "copy"
        server_mode = "in_place" if mode == "in_place" else "copy"

        run.emit(Phase.APPLYING, "Backing up and writing the change to your file." if server_mode == "in_place"
                 else "Writing the change to a new copy of the workbook.")
        try:
            res = adapter.apply(cs, server_mode, self.settings.exports_dir, self.settings.backups_dir)
        except StaleSourceError as e:
            self._fail(run, str(e))
            return True
        except PermissionError as e:
            self._fail(run, str(e))
            return True
        except Exception as e:
            self._fail(run, f"Writing the workbook failed: {type(e).__name__}: {e}")
            return True
        run.emit(Phase.VERIFYING, f"Re-opened the saved workbook and verified {res['verified_cells']} written cell(s).",
                 "success")
        res["write_back"] = mode
        if server_mode == "copy":
            media = XLSM_MEDIA if res["output_name"].lower().endswith(".xlsm") else XLSX_MEDIA
            ef = self.exports.register_existing(Path(res["output_path"]), media, cs.affected_rows)
            run.exports = [ef.to_dict() | {"format": "xlsx", "kind": "edited_workbook"}]
            res.pop("output_path", None)  # server path is not useful to remote users
        else:
            run.exports = []
        run.apply_result = res
        run.export_status = "done"
        run.status = RunStatus.COMPLETED
        if mode == "in_place":
            msg = f"Done. Updated {res['output_name']} ({cs.affected_rows} row(s)); backup saved first."
        elif mode == "browser":
            msg = "Done. The edited workbook is ready; your browser will now save it over the file you opened."
        else:
            msg = f"Done. Saved the edited copy {res['output_name']} ({cs.affected_rows} row(s) changed)."
        run.emit(Phase.COMPLETED, msg, "success")
        return True


def _write_target(sql: str, dialect: str) -> str | None:
    try:
        root = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return None
    if isinstance(root, (exp.Update, exp.Delete, exp.Insert)):
        t = root.this.this if isinstance(root.this, exp.Schema) else root.this
        if isinstance(t, exp.Table):
            return t.name
    return None
