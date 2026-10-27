import { useEffect, useState } from "react";
import { api } from "../api";
import type { Schema } from "../types";

export default function SchemaExplorer({ sourceId, refreshKey }: { sourceId: string | null; refreshKey: number }) {
  const [schema, setSchema] = useState<Schema | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!sourceId) {
      setSchema(null);
      return;
    }
    let live = true;
    setError(null);
    api.schema(sourceId)
      .then((s) => live && setSchema(s))
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [sourceId, refreshKey]);

  if (!sourceId) return null;
  return (
    <section className="panel">
      <h2>Schema</h2>
      {error && <div className="alert error">{error}</div>}
      {!schema && !error && <p className="hint">Loading…</p>}
      {schema && (
        <ul className="schema">
          {schema.tables.map((t) => (
            <li key={t.name}>
              <button className="schema-table" aria-expanded={!!open[t.name]} onClick={() => setOpen({ ...open, [t.name]: !open[t.name] })}>
                <span className="caret">{open[t.name] ? "▾" : "▸"}</span>
                <span className="mono">{t.name}</span>
                <span className="muted">{t.kind}{t.row_count != null ? ` · ${t.row_count.toLocaleString()} rows` : ""}</span>
              </button>
              {open[t.name] && (
                <div className="schema-cols">
                  {t.note && <p className="hint">{t.note}</p>}
                  <table>
                    <tbody>
                      {t.columns.map((c) => (
                        <tr key={c.name}>
                          <td className="mono">{c.primary_key ? "🔑 " : ""}{c.name}</td>
                          <td className="muted mono">{c.type}</td>
                          <td className="muted small">{c.note}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {t.foreign_keys.map((fk, i) => (
                    <p key={i} className="hint mono">
                      {fk.columns.join(", ")} → {fk.referred_table}({fk.referred_columns.join(", ")})
                    </p>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
