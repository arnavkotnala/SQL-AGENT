"""Run state: everything the UI shows comes from here (no simulated progress)."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class RunStatus:
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"  # needs a data source or an answer to a clarifying question
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"  # stopped by a security restriction
    NEEDS_INPUT = "needs_input"  # terminal: the request cannot be answered with the connected data
    CANCELLED = "cancelled"

    TERMINAL = {COMPLETED, FAILED, BLOCKED, NEEDS_INPUT, CANCELLED}


class Phase:
    OBSERVING = "Observing"
    INSPECTING = "Inspecting schema"
    DEFINING_GOAL = "Defining goal"
    PLANNING = "Planning"
    REFINING = "Refining"
    VALIDATING = "Validating"
    AWAITING_APPROVAL = "Awaiting approval"
    EXECUTING = "Executing"
    EVALUATING = "Evaluating"
    EXPORTING = "Exporting"
    APPLYING = "Applying changes"
    VERIFYING = "Verifying"
    COMPLETED = "Completed"
    FAILED = "Failed"


class RunCancelled(Exception):
    pass


@dataclass
class RunOptions:
    source_id: str | None = None
    require_approval: bool = False  # review SQL before it executes (read mode)
    write_mode: str = "copy"  # default for approved spreadsheet changes: copy | in_place | browser


@dataclass
class AgentRun:
    request: str
    options: RunOptions
    max_iterations: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    status: str = RunStatus.QUEUED
    phase: str = Phase.OBSERVING
    iteration: int = 0
    message: str = "Queued"
    source: dict[str, Any] | None = None
    schema_summary: list[dict[str, Any]] = field(default_factory=list)
    goal: dict[str, Any] | None = None
    clarifications: list[dict[str, str]] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)
    sql: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    result_meta: dict[str, Any] | None = None  # columns, row_count, truncated, elapsed_ms
    evaluation: dict[str, Any] | None = None
    change_preview: dict[str, Any] | None = None
    apply_result: dict[str, Any] | None = None
    exports: list[dict[str, Any]] = field(default_factory=list)
    export_status: str = "not_started"  # not_started | exporting | done | failed | not_applicable
    summary: str | None = None
    llm: str | None = None
    finished_at: str | None = None

    # internals (not serialized)
    rows: list[tuple[Any, ...]] = field(default_factory=list, repr=False)
    columns: list[str] = field(default_factory=list, repr=False)
    column_hints: dict[str, tuple[str, str]] = field(default_factory=dict, repr=False)
    pending: dict[str, Any] | None = None
    _response: dict[str, Any] | None = field(default=None, repr=False)
    _events: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _cond: threading.Condition = field(default_factory=threading.Condition, repr=False)
    _cancelled: bool = field(default=False, repr=False)
    _change_set: Any = field(default=None, repr=False)

    # ---- events --------------------------------------------------------------------------
    def emit(self, phase: str, message: str, level: str = "info", detail: dict[str, Any] | None = None) -> None:
        with self._cond:
            self.phase = phase
            self.message = message
            self._events.append({
                "seq": len(self._events) + 1,
                "ts": datetime.now().isoformat(timespec="milliseconds"),
                "iteration": self.iteration,
                "phase": phase,
                "level": level,  # info | success | warning | error
                "message": message,
                "detail": detail or None,
            })
            self._cond.notify_all()

    def events_since(self, seq: int) -> list[dict[str, Any]]:
        with self._cond:
            return [e for e in self._events if e["seq"] > seq]

    # ---- pausing for the user ------------------------------------------------------------
    def wait_for_user(self, pending: dict[str, Any], status: str, timeout_s: float) -> dict[str, Any]:
        with self._cond:
            self.pending = pending
            self._response = None
            self.status = status
            self._cond.notify_all()
            deadline = time.monotonic() + timeout_s
            while self._response is None and not self._cancelled:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.pending = None
                    raise TimeoutError("Timed out waiting for your response.")
                self._cond.wait(timeout=min(remaining, 1.0))
            if self._cancelled:
                self.pending = None
                raise RunCancelled()
            response, self._response = self._response, None
            self.pending = None
            self.status = RunStatus.RUNNING
            return response

    def respond(self, kind: str, payload: dict[str, Any]) -> None:
        with self._cond:
            if not self.pending or self.pending.get("type") != kind:
                raise ValueError(f"This run is not waiting for '{kind}'.")
            self._response = payload
            self._cond.notify_all()

    def cancel(self) -> None:
        with self._cond:
            self._cancelled = True
            self._cond.notify_all()

    def check_cancelled(self) -> None:
        if self._cancelled:
            raise RunCancelled()

    # ---- serialization -------------------------------------------------------------------
    def to_dict(self, since_seq: int = 0) -> dict[str, Any]:
        with self._cond:
            return {
                "id": self.id,
                "request": self.request,
                "created_at": self.created_at,
                "finished_at": self.finished_at,
                "status": self.status,
                "phase": self.phase,
                "message": self.message,
                "iteration": self.iteration,
                "max_iterations": self.max_iterations,
                "options": self.options.__dict__,
                "source": self.source,
                "schema_summary": self.schema_summary,
                "goal": self.goal,
                "clarifications": self.clarifications,
                "plan": self.plan,
                "sql": self.sql,
                "params": self.params,
                "validation": self.validation,
                "errors": self.errors[-10:],
                "attempts": self.attempts,
                "result": self.result_meta,
                "evaluation": self.evaluation,
                "change_preview": self.change_preview,
                "apply_result": self.apply_result,
                "exports": self.exports,
                "export_status": self.export_status,
                "summary": self.summary,
                "pending": self.pending,
                "llm": self.llm,
                "events": [e for e in self._events if e["seq"] > since_seq],
                "event_count": len(self._events),
            }

    def brief(self) -> dict[str, Any]:
        return {"id": self.id, "request": self.request, "status": self.status, "phase": self.phase,
                "created_at": self.created_at, "finished_at": self.finished_at, "iteration": self.iteration,
                "row_count": (self.result_meta or {}).get("row_count"),
                "source": (self.source or {}).get("name")}
