import { useRef, useState } from "react";
import { api } from "../api";
import { canWriteBack, pickSpreadsheet } from "../fileAccess";
import type { Health, Sample, Source } from "../types";

interface Props {
  health: Health | null;
  samples: Sample[];
  sources: Source[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onConnected: (s: Source, handle?: FileSystemFileHandle) => void;
  onRemove: (id: string) => void;
  hasHandle: (id: string) => boolean;
}

type Tab = "file" | "samples" | "database";

const KIND_LABEL: Record<Source["kind"], string> = {
  sample: "Sample DB",
  sqlite: "SQLite",
  excel: "Excel",
  csv: "CSV",
  server: "Server DB",
};

export default function SourcePanel(p: Props) {
  const [tab, setTab] = useState<Tab>("file");
  const [path, setPath] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const guard = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const openFile = () =>
    guard(async () => {
      if (canWriteBack()) {
        const handle = await pickSpreadsheet();
        if (!handle) return;
        const file = await handle.getFile();
        p.onConnected(await api.upload(file, file.name), handle);
      } else {
        fileInput.current?.click();
      }
    });

  return (
    <section className="panel">
      <h2>Data source</h2>
      <div className="tabs" role="tablist">
        {(["file", "samples", "database"] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "tab active" : "tab"} onClick={() => setTab(t)}>
            {t === "file" ? "Excel / file" : t === "samples" ? "Samples" : "Database"}
          </button>
        ))}
      </div>

      {tab === "file" && (
        <div className="stack">
          <button className="btn primary block" disabled={busy} onClick={openFile}>
            Open Excel / CSV / SQLite file…
          </button>
          <p className="hint">
            {canWriteBack()
              ? "Your browser can save approved changes straight back into the file you open."
              : "Approved changes will be offered as a download (use Chrome or Edge to save back into the file)."}
          </p>
          <input
            ref={fileInput}
            type="file"
            hidden
            accept=".xlsx,.xlsm,.csv,.db,.sqlite,.sqlite3"
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) guard(async () => p.onConnected(await api.upload(f, f.name)));
            }}
          />
          {p.health?.local_paths_enabled && (
            <form
              className="stack"
              onSubmit={(e) => {
                e.preventDefault();
                if (path.trim()) guard(async () => { p.onConnected(await api.connect("path", path)); setPath(""); });
              }}
            >
              <label className="label" htmlFor="path">…or paste a file path on this computer</label>
              <div className="row">
                <input id="path" className="input" placeholder="C:\Users\you\Documents\marks.xlsx" value={path} onChange={(e) => setPath(e.target.value)} />
                <button className="btn" disabled={busy || !path.trim()}>Connect</button>
              </div>
              <p className="hint">Files opened by path can be edited in place (a backup is made first).</p>
            </form>
          )}
        </div>
      )}

      {tab === "samples" && (
        <div className="stack">
          {p.samples.map((s) => (
            <button key={s.id} className="card-btn" disabled={busy} onClick={() => guard(async () => p.onConnected(await api.connect("sample", s.id)))}>
              <strong>{s.title}</strong>
              <span>{s.description}</span>
            </button>
          ))}
        </div>
      )}

      {tab === "database" && (
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            if (url.trim()) guard(async () => { p.onConnected(await api.connect("url", url)); setUrl(""); });
          }}
        >
          <label className="label" htmlFor="dburl">PostgreSQL / MySQL connection URL</label>
          <input id="dburl" className="input" type="password" autoComplete="off" placeholder="postgresql+psycopg://user:password@host:5432/db" value={url} onChange={(e) => setUrl(e.target.value)} />
          <button className="btn" disabled={busy || !url.trim()}>Connect read-only</button>
          <p className="hint">Sent once to your LoopSQL server; never shown again or logged. Prefer a read-only database user.</p>
        </form>
      )}

      {error && <div className="alert error">{error}</div>}

      {p.sources.length > 0 && (
        <>
          <h3>Connected</h3>
          <ul className="source-list">
            {p.sources.map((s) => (
              <li key={s.id} className={s.id === p.activeId ? "active" : ""}>
                <button className="source-pick" onClick={() => p.onSelect(s.id)} title={s.target}>
                  <span className={`badge kind-${s.kind}`}>{KIND_LABEL[s.kind]}</span>
                  <span className="source-name">{s.name}</span>
                  {s.editable && <span className="tag">{p.hasHandle(s.id) ? "editable · save-back" : "editable"}</span>}
                </button>
                <button className="icon-btn" aria-label={`Disconnect ${s.name}`} onClick={() => p.onRemove(s.id)}>×</button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
