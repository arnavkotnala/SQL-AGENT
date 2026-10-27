import type { ChangePreview } from "../types";

const show = (v: unknown) => (v === null || v === undefined || v === "" ? <span className="muted">empty</span> : String(v));

export default function ChangePreviewView({ change }: { change: ChangePreview }) {
  const cols = (rows: Record<string, unknown>[]) => Array.from(new Set(rows.flatMap((r) => Object.keys(r))));
  return (
    <div className="change">
      <div className="stats">
        <span><strong>{change.affected_rows}</strong> row(s)</span>
        <span><strong>{change.updated_cells}</strong> cell(s) updated</span>
        {change.inserted_rows.length > 0 && <span><strong>{change.inserted_rows.length}</strong> added</span>}
        {change.deleted_rows.length > 0 && <span><strong>{change.deleted_rows.length}</strong> deleted</span>}
        <span className="muted">sheet “{change.sheet}”</span>
      </div>
      <pre className="sql small">{change.sql}</pre>
      {change.new_columns.length > 0 && (
        <p>New column(s): {change.new_columns.map((c) => <span key={c.name} className="tag">{c.header} (column {c.col_letter})</span>)}</p>
      )}
      {change.warnings.map((w, i) => <div key={i} className="alert warn">{w}</div>)}
      {change.updates.length > 0 && (
        <div className="table-wrap short">
          <table className="data">
            <thead><tr><th>Cell</th><th>Column</th><th>Before</th><th>After</th></tr></thead>
            <tbody>
              {change.updates.map((u, i) => (
                <tr key={i}>
                  <td className="mono">{u.col_letter}{u.row}</td>
                  <td>{u.header}</td>
                  <td className="old">{show(u.old)}</td>
                  <td className="new">{show(u.new)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {change.inserted_rows.length > 0 && (
        <>
          <h4>Rows to add</h4>
          <div className="table-wrap short">
            <table className="data">
              <thead><tr>{cols(change.inserted_rows).map((c) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>
                {change.inserted_rows.map((r, i) => (
                  <tr key={i} className="new">{cols(change.inserted_rows).map((c) => <td key={c}>{show(r[c])}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {change.deleted_rows.length > 0 && (
        <>
          <h4>Rows to delete</h4>
          <div className="table-wrap short">
            <table className="data">
              <thead><tr>{cols(change.deleted_rows).map((c) => <th key={c}>{c === "_excel_row" ? "Excel row" : c}</th>)}</tr></thead>
              <tbody>
                {change.deleted_rows.map((r, i) => (
                  <tr key={i} className="old">{cols(change.deleted_rows).map((c) => <td key={c}>{show(r[c])}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {change.truncated_preview && <p className="hint">Preview shows the first 500 changes; all of them will be applied.</p>}
    </div>
  );
}
