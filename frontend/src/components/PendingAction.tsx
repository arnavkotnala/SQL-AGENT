import { useEffect, useState } from "react";
import type { Pending, Source, WriteMode } from "../types";
import ChangePreviewView from "./ChangePreview";

interface Props {
  pending: Pending;
  sources: Source[];
  canBrowserWrite: boolean;
  onRespond: (body: Record<string, unknown>) => Promise<void>;
  onBeforeBrowserApprove: () => Promise<boolean>;
}

const MODE_TEXT: Record<WriteMode, { title: string; desc: string }> = {
  browser: { title: "Save back into the file I opened", desc: "Your browser writes the edited workbook over your file (undo available)." },
  in_place: { title: "Overwrite the original file", desc: "A timestamped backup is saved first." },
  copy: { title: "Save as a new copy", desc: "Your original stays untouched; download the edited workbook." },
};

export default function PendingAction({ pending, sources, canBrowserWrite, onRespond, onBeforeBrowserApprove }: Props) {
  const [answer, setAnswer] = useState("");
  const [sql, setSql] = useState(pending.sql ?? "");
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);
  const modes: WriteMode[] = [
    ...(canBrowserWrite ? (["browser"] as WriteMode[]) : []),
    ...(pending.can_in_place ? (["in_place"] as WriteMode[]) : []),
    "copy",
  ];
  const initialMode: WriteMode = canBrowserWrite ? "browser" : pending.default_mode && modes.includes(pending.default_mode) ? pending.default_mode : "copy";
  const [mode, setMode] = useState<WriteMode>(initialMode);

  useEffect(() => setSql(pending.sql ?? ""), [pending.sql]);

  const send = async (body: Record<string, unknown>) => {
    setBusy(true);
    try {
      await onRespond({ type: pending.type, ...body });
    } finally {
      setBusy(false);
    }
  };

  if (pending.type === "source") {
    return (
      <div className="pending">
        <h3>The agent needs a data source</h3>
        {pending.problem && <div className="alert warn">{pending.problem}</div>}
        <p>{pending.message}</p>
        <p className="hint">Open or connect a file in the <strong>Data source</strong> panel, then choose it here.</p>
        <div className="row">
          <select className="input" value={pick} onChange={(e) => setPick(e.target.value)} aria-label="Choose data source">
            <option value="">Choose a connected source…</option>
            {sources.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.kind})</option>)}
          </select>
          <button className="btn primary" disabled={!pick || busy} onClick={() => send({ source_id: pick })}>Use this source</button>
        </div>
      </div>
    );
  }

  if (pending.type === "clarification") {
    return (
      <form className="pending" onSubmit={(e) => { e.preventDefault(); send({ answer }); }}>
        <h3>Question from the agent</h3>
        <p className="question">{pending.question}</p>
        <div className="row">
          <input className="input" autoFocus value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="Your answer" />
          <button className="btn primary" disabled={busy}>Answer</button>
        </div>
      </form>
    );
  }

  if (pending.type === "sql_approval") {
    return (
      <div className="pending">
        <h3>Review SQL before it runs</h3>
        {pending.plan && pending.plan.length > 0 && (
          <ol className="plan">{pending.plan.map((s, i) => <li key={i}>{s}</li>)}</ol>
        )}
        <textarea className="input mono sql-edit" value={sql} onChange={(e) => setSql(e.target.value)} rows={Math.min(14, sql.split("\n").length + 2)} spellCheck={false} aria-label="SQL to approve" />
        {pending.params && Object.keys(pending.params).length > 0 && (
          <p className="hint mono">params: {JSON.stringify(pending.params)}</p>
        )}
        {pending.error && <div className="alert error">{pending.error}</div>}
        <div className="row">
          <button className="btn primary" disabled={busy} onClick={() => send({ approved: true, sql })}>Approve &amp; run</button>
          <button className="btn danger" disabled={busy} onClick={() => send({ approved: false })}>Reject</button>
        </div>
      </div>
    );
  }

  // change_approval
  const change = pending.change!;
  return (
    <div className="pending">
      <h3>Approve changes to {pending.file}</h3>
      <ChangePreviewView change={change} />
      <fieldset className="modes">
        <legend>How should the change be saved?</legend>
        {modes.map((m) => (
          <label key={m} className={mode === m ? "mode active" : "mode"}>
            <input type="radio" name="mode" checked={mode === m} onChange={() => setMode(m)} />
            <span><strong>{MODE_TEXT[m].title}</strong><small>{MODE_TEXT[m].desc}</small></span>
          </label>
        ))}
      </fieldset>
      <div className="row">
        <button
          className="btn primary"
          disabled={busy}
          onClick={async () => {
            if (mode === "browser" && !(await onBeforeBrowserApprove())) return;
            await send({ approved: true, mode });
          }}
        >
          Approve &amp; apply
        </button>
        <button className="btn danger" disabled={busy} onClick={() => send({ approved: false })}>Reject</button>
      </div>
    </div>
  );
}
