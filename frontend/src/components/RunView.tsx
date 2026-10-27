import { useState } from "react";
import { api, download } from "../api";
import type { AgentEvent, Run, Source } from "../types";
import { TERMINAL } from "../types";
import PendingAction from "./PendingAction";
import ResultsTable from "./ResultsTable";

interface Props {
  run: Run;
  events: AgentEvent[];
  sources: Source[];
  canBrowserWrite: boolean;
  writeBackNote: string | null;
  onUndo: (() => void) | null;
  onRespond: (body: Record<string, unknown>) => Promise<void>;
  onBeforeBrowserApprove: () => Promise<boolean>;
  onCancel: () => void;
}

export const STATUS_LABEL: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  awaiting_input: "Needs your input",
  awaiting_approval: "Awaiting approval",
  completed: "Completed",
  failed: "Failed",
  blocked: "Blocked",
  needs_input: "Needs input",
  cancelled: "Cancelled",
};

const OUTCOME_LABEL: Record<string, string> = {
  success: "✓ success",
  validation_error: "✗ validation error",
  execution_error: "✗ database error",
  unsatisfied: "↻ needs refinement",
  duplicate: "⟳ repeated query (skipped)",
  blocked: "⛔ blocked",
  model_error: "✗ model error",
};

export default function RunView(p: Props) {
  const { run } = p;
  const [showEvents, setShowEvents] = useState(true);
  const [dlError, setDlError] = useState<string | null>(null);
  const terminal = TERMINAL.includes(run.status);
  const working = !terminal && !run.pending;
  const hasRows = !!run.result && (run.result.columns?.length ?? 0) > 0;

  const dl = async (url: string, name: string) => {
    setDlError(null);
    try {
      await download(url, name);
    } catch (e) {
      setDlError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <section className="panel run">
      <div className="run-head">
        <div>
          <div className="row gap">
            <span className={`status status-${run.status}`}>{STATUS_LABEL[run.status] ?? run.status}</span>
            <span className="phase">{working && <span className="spinner" aria-hidden />}{run.phase}</span>
          </div>
          <p className="request">“{run.request}”</p>
        </div>
        <div className="iter" title="Reasoning / execution iterations used">
          <span className="iter-num">{run.iteration}</span>
          <span className="muted small">of {run.max_iterations} iterations</span>
          {!terminal && <button className="btn small" onClick={p.onCancel}>Cancel</button>}
        </div>
      </div>

      {run.goal && (
        <div className="goal">
          <div><span className="label">Goal</span> {run.goal.interpreted_goal}</div>
          {run.goal.success_criteria.length > 0 && (
            <ul className="criteria">{run.goal.success_criteria.map((c, i) => <li key={i}>{c}</li>)}</ul>
          )}
          <div className="muted small">
            Mode: {run.goal.operation === "modify" ? "modify spreadsheet (needs approval)" : "read-only query"}
            {run.source && <> · Source: {run.source.name}</>}
            {run.llm && <> · Model: {run.llm}</>}
          </div>
        </div>
      )}

      {run.pending && (
        <PendingAction
          key={`${run.pending.type}-${run.event_count}`}
          pending={run.pending}
          sources={p.sources}
          canBrowserWrite={p.canBrowserWrite}
          onRespond={p.onRespond}
          onBeforeBrowserApprove={p.onBeforeBrowserApprove}
        />
      )}

      {run.status === "completed" && (
        <div className="alert success">
          <strong>{run.events.length ? run.message : "Completed."}</strong>
          {run.summary && <p>{run.summary}</p>}
          {p.writeBackNote && <p>{p.writeBackNote} {p.onUndo && <button className="btn small" onClick={p.onUndo}>Undo</button>}</p>}
          {run.apply_result?.backup_path && <p className="small">Backup: <span className="mono">{run.apply_result.backup_path}</span></p>}
        </div>
      )}
      {(run.status === "failed" || run.status === "blocked" || run.status === "needs_input") && (
        <div className="alert error">
          <strong>{STATUS_LABEL[run.status]}:</strong> {run.errors[run.errors.length - 1] ?? run.message}
        </div>
      )}

      {(run.exports.length > 0 || (terminal && hasRows && run.status !== "completed")) && (
        <div className="downloads">
          {run.exports.map((e) => (
            <button key={e.id} className={`btn ${e.format === "xlsx" ? "primary" : ""}`} onClick={() => dl(e.url, e.filename)}>
              ⬇ {e.kind === "edited_workbook" ? "Download edited workbook" : e.format === "xlsx" ? "Download Excel" : "Download CSV"}
            </button>
          ))}
          {run.exports.length === 0 && hasRows && (
            <button className="btn" onClick={async () => {
              const e = await api.exportNow(run.id, "xlsx");
              await dl(e.url, e.filename);
            }}>⬇ Export last result anyway (unverified)</button>
          )}
          {run.result && <span className="muted small">{run.result.row_count.toLocaleString()} row(s){run.result.truncated ? ` — limited to ${run.result.row_limit.toLocaleString()}` : ""}</span>}
        </div>
      )}
      {dlError && <div className="alert error">{dlError}</div>}

      {run.sql && (
        <details className="block" open>
          <summary>Generated SQL {run.validation && (run.validation.ok ? <span className="tag ok">validated</span> : <span className="tag bad">rejected</span>)}</summary>
          {run.plan.length > 0 && <ol className="plan">{run.plan.map((s, i) => <li key={i}>{s}</li>)}</ol>}
          <pre className="sql">{run.sql}</pre>
          {Object.keys(run.params || {}).length > 0 && <p className="hint mono">params: {JSON.stringify(run.params)}</p>}
        </details>
      )}

      {run.attempts.length > 0 && (
        <details className="block" open={run.attempts.some((a) => a.outcome !== "success")}>
          <summary>Attempts &amp; retries ({run.attempts.length})</summary>
          <ol className="attempts">
            {run.attempts.map((a, i) => (
              <li key={i} className={`attempt ${a.outcome}`}>
                <div><strong>Iteration {a.iteration}</strong> — {OUTCOME_LABEL[a.outcome] ?? a.outcome}</div>
                {a.error && <div className="small err-text">{a.error}</div>}
                {a.feedback && a.outcome !== "success" && <div className="small muted">Next: {a.feedback}</div>}
              </li>
            ))}
          </ol>
        </details>
      )}

      {hasRows && run.status !== "awaiting_approval" && (
        <details className="block" open>
          <summary>Results</summary>
          <ResultsTable runId={run.id} version={run.event_count} />
        </details>
      )}

      <details className="block" open={showEvents} onToggle={(e) => setShowEvents((e.target as HTMLDetailsElement).open)}>
        <summary>Agent timeline ({p.events.length})</summary>
        <ol className="timeline">
          {p.events.map((e) => (
            <li key={e.seq} className={`ev ${e.level}`}>
              <span className="ev-phase">{e.phase}</span>
              {e.iteration > 0 && <span className="ev-iter">#{e.iteration}</span>}
              <span className="ev-msg">{e.message}</span>
              <time className="ev-time">{e.ts.slice(11, 19)}</time>
            </li>
          ))}
        </ol>
      </details>
    </section>
  );
}
