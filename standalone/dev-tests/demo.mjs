// Runs sample questions against ../school_student_marks.xlsx in test mode (no API key) and saves the
// generated Excel files to ./demo-output so they can be opened in Excel.  Usage: node demo.mjs
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import ExcelJS from "exceljs";
import initSqlJs from "sql.js";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const html = fs.readFileSync(path.join(here, "..", "index.html"), "utf8");
const a = html.indexOf('<script id="loopsql-core">'); const b = html.indexOf("</script>", a);
Object.assign(globalThis, { initSqlJs, ExcelJS, LOOPSQL_WASM_BASE: path.dirname(require.resolve("sql.js/dist/sql-wasm.wasm")) + path.sep });
vm.runInThisContext(html.slice(a + '<script id="loopsql-core">'.length, b));
const L = globalThis.LoopSQL;

const out = path.join(here, "demo-output");
fs.mkdirSync(out, { recursive: true });
const file = fs.readFileSync(path.join(here, "..", "school_student_marks.xlsx"));
const source = await L.sourceFromFile("school_student_marks.xlsx", file.buffer.slice(file.byteOffset, file.byteOffset + file.byteLength));

const questions = [
  "Find students whose attendance is below 50% and export their names, roll numbers and attendance.",
  "Find students who scored below 40 in any subject.",
  "Calculate average marks for each subject.",
  "Show the top 5 students by average marks.",
  "How many students are in each class?",
  "Add a column 'Status' with 'Detained' for students whose attendance is below 75%, otherwise 'Eligible'.",
];
for (const q of questions) {
  const run = new L.AgentRun(q, { sourceId: source.id });
  run.listeners.add((r) => { if (r.pending && r.pending.type === "change_approval" && !r._ok) { r._ok = true; queueMicrotask(() => r.respond("change_approval", { approved: true, mode: "download" })); } });
  await L.runAgent(run, { provider: new L.OfflineProvider(), getSource: () => source });
  console.log("\n" + "=".repeat(100) + `\nQ: ${q}\nStatus: ${run.status} | iterations: ${run.iteration} | ${run.message}`);
  console.log("SQL: " + run.sql.replace(/\s+/g, " ") + (Object.keys(run.params).length ? `   params ${JSON.stringify(run.params)}` : ""));
  if (run.columns.length) {
    console.log(run.columns.join(" | "));
    for (const row of run.rows.slice(0, 8)) console.log(row.map((v) => (typeof v === "number" && !Number.isInteger(v) ? v.toFixed(3) : v)).join(" | "));
    if (run.rows.length > 8) console.log(`... ${run.rows.length - 8} more row(s)`);
  }
  if (run.change) console.log(`Change preview: ${run.change.affectedRows} rows, e.g. ${run.change.updates.slice(0, 3).map((u) => `${u.colLetter}${u.row}: ${u.old ?? "(empty)"} -> ${u.new}`).join(", ")}`);
  for (const x of run.exports.filter((e) => e.format === "xlsx")) {
    fs.writeFileSync(path.join(out, x.filename), Buffer.from(x.data));
    console.log(`Saved: demo-output/${x.filename}`);
  }
}
