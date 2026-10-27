import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, fetchBlob, setAccessKey } from "./api";
import RunView, { STATUS_LABEL } from "./components/RunView";
import SchemaExplorer from "./components/SchemaExplorer";
import SourcePanel from "./components/SourcePanel";
import { ensureWritePermission, restore, writeBack } from "./fileAccess";
import type { AgentEvent, Health, Run, RunBrief, Sample, Source, WriteMode } from "./types";
import { TERMINAL } from "./types";

const EXCEL_PROMPTS = [
  "Summarise this sheet: number of rows and the average of each numeric column.",
  "Show rows where attendance is below 50%.",
  "Add a column 'Status' that says 'Detained' when attendance is below 50%, otherwise 'Eligible'.",
];

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [needKey, setNeedKey] = useState(false);
  const [keyInput, setKeyInput] = useState("");
  const [samples, setSamples] = useState<Sample[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const handles = useRef(new Map<string, FileSystemFileHandle>());
  // lastModified/size of each opened file when its contents were sent to the server
  const stamps = useRef(new Map<string, { lastModified: number; size: number }>());
  const remember = async (id: string, handle: FileSystemFileHandle) => {
    const f = await handle.getFile();
    handles.current.set(id, handle);
    stamps.current.set(id, { lastModified: f.lastModified, size: f.size });
  };
  const [, forceRender] = useState(0);

  const [request, setRequest] = useState("");
  const [requireApproval, setRequireApproval] = useState(false);
  const [writePref, setWritePref] = useState<WriteMode>("copy");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [history, setHistory] = useState<RunBrief[]>([]);
  const [schemaKey, setSchemaKey] = useState(0);
  const [writeBackNote, setWriteBackNote] = useState<string | null>(null);
  const [undo, setUndo] = useState<(() => void) | null>(null);
  const handledRuns = useRef(new Set<string>());

  const active = sources.find((s) => s.id === activeId) ?? null;

  const loadAll = useCallback(async () => {
    try {
      const h = await api.health();
      setHealth(h);
      const [smp, src, hist] = await Promise.all([api.samples(), api.sources(), api.runs()]);
      setSamples(smp);
      setSources(src);
      setHistory(hist);
      setActiveId((cur) => cur ?? src[0]?.id ?? null);
      setNeedKey(false);
      setFatal(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setNeedKey(true);
      else setFatal(`Cannot reach the LoopSQL backend: ${e instanceof Error ? e.message : e}`);
    }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  // ---- live polling of the real backend run state --------------------------------------
  useEffect(() => {
    if (!runId) return;
    let stop = false;
    let since = 0;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const r = await api.run(runId, since);
        if (stop) return;
        if (r.events.length) {
          since = r.events[r.events.length - 1].seq;
          setEvents((prev) => [...prev, ...r.events]);
        }
        setRun(r);
        if (TERMINAL.includes(r.status)) {
          api.runs().then(setHistory).catch(() => undefined);
          return;
        }
        timer = setTimeout(tick, r.pending ? 1500 : 600);
      } catch {
        if (!stop) timer = setTimeout(tick, 2000);
      }
    };
    tick();
    return () => { stop = true; clearTimeout(timer); };
  }, [runId]);

  // ---- after completion: refresh schema / save back into the user's file ----------------
  useEffect(() => {
    if (!run || run.status !== "completed" || handledRuns.current.has(run.id)) return;
    handledRuns.current.add(run.id);
    const ar = run.apply_result;
    if (!ar) return;
    if (ar.write_back === "in_place") { setSchemaKey((k) => k + 1); return; }
    const srcId = run.options.source_id;
    const handle = srcId ? handles.current.get(srcId) : undefined;
    if (ar.write_back !== "browser" || !handle || !run.exports[0]) return;
    (async () => {
      try {
        // Never overwrite edits made in Excel after the file was opened here.
        const stamp = srcId ? stamps.current.get(srcId) : undefined;
        const current = await handle.getFile();
        if (stamp && (current.lastModified !== stamp.lastModified || current.size !== stamp.size)) {
          setWriteBackNote(`“${handle.name}” changed after you opened it, so it was NOT overwritten. ` +
            "Download the edited workbook, or re-open the file and run the request again.");
          return;
        }
        const blob = await fetchBlob(run.exports[0].url);
        const previous = await writeBack(handle, blob);
        // re-sync the server's copy so follow-up requests see the edited data
        const file = await handle.getFile();
        const fresh = await api.upload(file, file.name);
        await remember(fresh.id, handle);
        if (srcId) { handles.current.delete(srcId); await api.removeSource(srcId).catch(() => undefined); }
        setSources(await api.sources());
        setActiveId(fresh.id);
        setWriteBackNote(`Saved into your file “${handle.name}”.`);
        setUndo(() => async () => {
          await restore(handle, previous);
          const f = await handle.getFile();
          const back = await api.upload(f, f.name);
          await remember(back.id, handle);
          handles.current.delete(fresh.id);
          await api.removeSource(fresh.id).catch(() => undefined);
          setSources(await api.sources());
          setActiveId(back.id);
          setWriteBackNote(`Restored the original “${handle.name}”.`);
          setUndo(null);
        });
      } catch (e) {
        setWriteBackNote(`Could not save into your file (${e instanceof Error ? e.message : e}). Use “Download edited workbook” instead.`);
      }
    })();
  }, [run]);

  const startRun = async (text?: string) => {
    const req = (text ?? request).trim();
    if (req.length < 3) return;
    setStarting(true);
    setStartError(null);
    setWriteBackNote(null);
    setUndo(null);
    try {
      const mode: WriteMode = activeId && handles.current.has(activeId) ? "browser" : writePref;
      const r = await api.startRun({ request: req, source_id: activeId, require_approval: requireApproval, write_mode: mode });
      setEvents([]);
      setRun(r);
      setRunId(r.id);
    } catch (e) {
      setStartError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const onConnected = async (s: Source, handle?: FileSystemFileHandle) => {
    if (handle) await remember(s.id, handle);
    setSources(await api.sources());
    setActiveId(s.id);
    forceRender((n) => n + 1);
  };

  const onRemove = async (id: string) => {
    await api.removeSource(id).catch(() => undefined);
    handles.current.delete(id);
    const list = await api.sources();
    setSources(list);
    if (activeId === id) setActiveId(list[0]?.id ?? null);
  };

  const runSourceHandle = run?.options.source_id ? handles.current.get(run.options.source_id) : undefined;
  const prompts = active?.kind === "sample"
    ? samples.find((s) => active.name === s.title)?.prompts ?? []
    : active?.kind === "excel" || active?.kind === "csv" ? EXCEL_PROMPTS : samples[0]?.prompts.slice(0, 3) ?? [];
  const running = !!run && !TERMINAL.includes(run.status);

  if (needKey) {
    return (
      <div className="center">
        <form className="panel keybox" onSubmit={(e) => { e.preventDefault(); setAccessKey(keyInput.trim()); loadAll(); }}>
          <h1 className="brand">LoopSQL <span>AI</span></h1>
          <p>This deployment is protected. Enter the access key provided by its administrator.</p>
          <input className="input" type="password" value={keyInput} onChange={(e) => setKeyInput(e.target.value)} autoFocus aria-label="Access key" />
          <button className="btn primary block">Continue</button>
        </form>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="topbar">
        <h1 className="brand">LoopSQL <span>AI</span></h1>
        <p className="tagline">Ask in plain language → the agent plans, writes safe SQL, checks itself, and exports to Excel.</p>
        <div className="health">
          {health && (
            health.gemini_configured
              ? <span className="pill ok" title="Gemini model used for planning">● {health.model}</span>
              : <span className="pill bad" title="Add GEMINI_API_KEY to backend/.env and restart">● Gemini key missing</span>
          )}
        </div>
      </header>
      {fatal && <div className="alert error page">{fatal}</div>}

      <div className="layout">
        <aside className="sidebar">
          <SourcePanel
            health={health}
            samples={samples}
            sources={sources}
            activeId={activeId}
            onSelect={setActiveId}
            onConnected={onConnected}
            onRemove={onRemove}
            hasHandle={(id) => handles.current.has(id)}
          />
          <SchemaExplorer sourceId={activeId} refreshKey={schemaKey} />
        </aside>

        <main className="main">
          <section className="panel">
            <h2>Request</h2>
            <form onSubmit={(e) => { e.preventDefault(); startRun(); }}>
              <textarea
                className="input request-box"
                rows={3}
                value={request}
                onChange={(e) => setRequest(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); startRun(); } }}
                placeholder={active
                  ? `Ask about ${active.name}… e.g. “Find students whose attendance is below 50% and export their names and roll numbers”`
                  : "Describe what you need. You can include a file path, or the agent will ask which file to use."}
                aria-label="Natural-language request"
              />
              {prompts.length > 0 && (
                <div className="chips">
                  {prompts.map((p) => <button type="button" key={p} className="chip" onClick={() => setRequest(p)}>{p}</button>)}
                </div>
              )}
              <div className="row between wrap">
                <div className="row gap wrap">
                  <label className="check">
                    <input type="checkbox" checked={requireApproval} onChange={(e) => setRequireApproval(e.target.checked)} />
                    Review SQL before it runs
                  </label>
                  {active?.kind === "excel" && !handles.current.has(active.id) && (
                    <label className="check">
                      Excel changes:
                      <select className="input inline" value={writePref} onChange={(e) => setWritePref(e.target.value as WriteMode)}>
                        <option value="copy">save as new copy</option>
                        {active.origin === "path" && <option value="in_place">overwrite original (with backup)</option>}
                      </select>
                    </label>
                  )}
                  <span className="muted small">Using: {active ? active.name : "— agent will ask"}</span>
                </div>
                <button className="btn primary run-btn" disabled={starting || running || request.trim().length < 3}>
                  {running ? "Agent running…" : "▶ Run Agent"}
                </button>
              </div>
              {startError && <div className="alert error">{startError}</div>}
            </form>
          </section>

          {run && (
            <RunView
              run={run}
              events={events}
              sources={sources}
              canBrowserWrite={!!runSourceHandle}
              writeBackNote={writeBackNote}
              onUndo={undo}
              onRespond={async (body) => { await api.respond(run.id, body); }}
              onBeforeBrowserApprove={async () => (runSourceHandle ? ensureWritePermission(runSourceHandle) : false)}
              onCancel={() => api.cancel(run.id)}
            />
          )}

          {history.length > 0 && (
            <section className="panel">
              <h2>Session history</h2>
              <ul className="history">
                {history.map((h) => (
                  <li key={h.id}>
                    <button className={h.id === runId ? "hist active" : "hist"} onClick={() => { setEvents([]); setRunId(h.id); }}>
                      <span className={`status status-${h.status}`}>{STATUS_LABEL[h.status] ?? h.status}</span>
                      <span className="hist-req">{h.request}</span>
                      <span className="muted small">{h.source ?? ""}{h.row_count != null ? ` · ${h.row_count} rows` : ""} · {h.created_at.slice(11, 16)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </main>
      </div>
    </div>
  );
}
