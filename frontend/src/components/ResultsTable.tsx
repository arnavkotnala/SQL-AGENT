import { useEffect, useState } from "react";
import { api } from "../api";
import type { RowsPage } from "../types";

const PAGE_SIZE = 25;

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return String(v);
}

export default function ResultsTable({ runId, version }: { runId: string; version: number }) {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [data, setData] = useState<RowsPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => { setQuery(search); setPage(1); }, 250);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    let live = true;
    api.rows(runId, page, PAGE_SIZE, query)
      .then((d) => { if (live) { setData(d); setError(null); } })
      .catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [runId, page, query, version]);

  if (error) return <div className="alert error">{error}</div>;
  if (!data) return <p className="hint">Loading results…</p>;
  const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
  return (
    <div className="results">
      <div className="row between">
        <input className="input search" placeholder="Search results…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search results" />
        <span className="muted small">
          {query ? `${data.total.toLocaleString()} of ${data.all_rows.toLocaleString()} rows match` : `${data.all_rows.toLocaleString()} rows`}
        </span>
      </div>
      {data.columns.length === 0 ? (
        <p className="hint">No columns.</p>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>{data.columns.map((c) => <th key={c}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {data.rows.length === 0 && (
                <tr><td colSpan={data.columns.length} className="empty">No matching records.</td></tr>
              )}
              {data.rows.map((r, i) => (
                <tr key={i}>{r.map((v, j) => <td key={j} className={typeof v === "number" ? "num" : ""}>{fmt(v)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pages > 1 && (
        <div className="pager">
          <button className="btn small" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ Prev</button>
          <span className="muted small">Page {page} / {pages}</span>
          <button className="btn small" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next ›</button>
        </div>
      )}
    </div>
  );
}
