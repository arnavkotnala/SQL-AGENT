// Developer checks for ../index.html. Runs the page's own engine (and, at the end, the whole page in jsdom)
// against ../school_student_marks.xlsx and compares every answer with ground truth computed independently
// from the raw workbook cells.  Usage:  npm test
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import ExcelJS from "exceljs";
import initSqlJs from "sql.js";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const HTML_PATH = path.join(here, "..", "index.html");
const XLSX_PATH = path.join(here, "..", "school_student_marks.xlsx");
const WASM_BASE = path.dirname(require.resolve("sql.js/dist/sql-wasm.wasm")) + path.sep;
const html = fs.readFileSync(HTML_PATH, "utf8");
const block = (id) => {
  const start = html.indexOf(`<script id="${id}">`);
  const end = html.indexOf("</script>", start);
  if (start < 0 || end < 0) throw new Error(`script block ${id} not found`);
  return html.slice(start + `<script id="${id}">`.length, end);
};

// ---- load the engine exactly as the page defines it --------------------------------------------
globalThis.initSqlJs = initSqlJs;
globalThis.ExcelJS = ExcelJS;
globalThis.LOOPSQL_WASM_BASE = WASM_BASE;
vm.runInThisContext(block("loopsql-core"), { filename: "index.html#loopsql-core" });
const L = globalThis.LoopSQL;
await L.initEngine();

// ---- tiny test runner ----------------------------------------------------------------------------
const results = [];
async function test(name, fn) {
  try { await fn(); results.push([true, name]); console.log("  ✓ " + name); }
  catch (e) { results.push([false, name, e]); console.log("  ✗ " + name + "\n      " + (e && e.stack ? e.stack.split("\n").slice(0, 3).join("\n      ") : e)); }
}
function eq(a, b, msg) { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(`${msg || "not equal"}\n      got:      ${x.slice(0, 400)}\n      expected: ${y.slice(0, 400)}`); }
function ok(c, msg) { if (!c) throw new Error(msg || "assertion failed"); }
const round2 = (x) => Math.round(x * 100) / 100;
const bufOf = (p) => { const b = fs.readFileSync(p); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); };

// ---- ground truth straight from the workbook's cells (independent of the app's import code) -------
const truthWb = new ExcelJS.Workbook();
await truthWb.xlsx.readFile(XLSX_PATH);
const students = []; truthWb.getWorksheet("Students").eachRow((r, n) => { if (n > 2) { const v = r.values; students.push({ id: v[1], roll: v[2], name: v[3], cls: v[4], section: v[5], att: v[8] }); } });
const subjects = {}; truthWb.getWorksheet("Subjects").eachRow((r, n) => { if (n > 1) subjects[r.values[1]] = r.values[2]; });
const marks = []; truthWb.getWorksheet("Marks").eachRow((r, n) => { if (n > 1) { const v = r.values; marks.push({ sid: v[2], sub: v[3], exam: v[4], m: v[5] }); } });
const byId = Object.fromEntries(students.map((s) => [s.id, s]));
console.log(`Ground truth: ${students.length} students, ${Object.keys(subjects).length} subjects, ${marks.length} marks rows\n`);

// ---- helpers ------------------------------------------------------------------------------------
async function school() { return L.sourceFromFile("school_student_marks.xlsx", bufOf(XLSX_PATH)); }
function scripted(script) {
  // A stand-in for Gemini that returns scripted, already-validated objects per task.
  const calls = [];
  return { name: "scripted", calls, async generate(task, ctx) {
    calls.push({ task, ctx });
    const q = script[task]; if (!q || !q.length) {
      if (task.startsWith("evaluate")) return { satisfied: true, issues: [], refinement: null, summary: "ok", formats: [] };
      throw new Error("no script for " + task);
    }
    const item = q.length > 1 ? q.shift() : q[0];
    if (item instanceof Error) throw item;
    return typeof item === "function" ? item(ctx) : item;
  } };
}
const goal = (operation = "read", extra = {}) => ({ interpreted_goal: "test goal", operation, feasible: true, infeasible_reason: null,
  needs_clarification: false, clarification_question: null, success_criteria: ["x"], requested_columns: [], relevant_tables: [], ...extra });
const prop = (sql, params = {}, newColumns = []) => ({ diagnosis: null, plan: ["p"], sql, params, newColumns, rationale: "r" });
async function run(source, request, provider, opts = {}, onPending) {
  const r = new L.AgentRun(request, { sourceId: source ? source.id : null, reviewSql: !!opts.reviewSql });
  if (onPending) r.listeners.add(() => { if (r.pending && !r._handled) { r._handled = r.pending; queueMicrotask(() => onPending(r)); } else if (!r.pending) r._handled = null; });
  const sources = opts.sources || (source ? [source] : []);
  await L.runAgent(r, { provider, getSource: (id) => sources.find((s) => s.id === id) || null, saveBack: opts.saveBack, today: () => "2026-10-27" });
  return r;
}
const offline = () => new L.OfflineProvider();
const col = (r, name) => r.columns.indexOf(name);
async function loadBuf(buf) { const wb = new ExcelJS.Workbook(); await wb.xlsx.load(buf); return wb; }

// =================================================================================================
console.log("Import");
await test("workbook imports: 3 sheets, header below a merged title row, types, percent + formula notes", async () => {
  const s = await school();
  const sc = L.inspectSchema(s);
  eq(sc.tables.map((t) => t.name), ["students", "subjects", "marks"]);
  const st = sc.tables[0];
  eq(st.columns.map((c) => c.name), ["student_id", "roll_no", "student_name", "class", "section", "gender", "date_of_birth", "attendance_pct"]);
  eq(st.rowCount, 60);
  ok(/header on row 2/.test(st.note), st.note);
  eq(st.columns.find((c) => c.name === "date_of_birth").type, "DATE");
  ok(/0\.45 means 45%/.test(st.columns.find((c) => c.name === "attendance_pct").note), "percent note");
  ok(/formula/.test(sc.tables[2].columns.find((c) => c.name === "percentage").note), "formula note");
  eq(sc.tables[2].rowCount, 600);
  const dob = s.db.exec("SELECT date_of_birth FROM students LIMIT 1")[0].values[0][0];
  ok(/^\d{4}-\d{2}-\d{2}$/.test(dob), "ISO date " + dob);
});

console.log("\nRetrieval on school_student_marks.xlsx (test mode, no API key)");
await test("attendance below 50% matches ground truth (fraction scale handled)", async () => {
  const s = await school();
  const r = await run(s, "Find students whose attendance is below 50% and export their names, roll numbers and attendance.", offline());
  eq(r.status, "completed", r.errors.join(" | "));
  const expected = students.filter((x) => x.att < 0.5).sort((a, b) => a.att - b.att);
  ok(expected.length >= 5, "test data should contain low-attendance students");
  eq(r.rows.map((x) => x[col(r, "roll_no")]), expected.map((x) => x.roll));
  eq(r.rows.map((x) => x[col(r, "attendance_percentage")]), expected.map((x) => x.att));
  ok(r.sql.includes(":threshold") && r.params.threshold === 0.5, "parameterised threshold 0.5");
});
await test("scored below 40 in any subject (3-sheet join) matches ground truth", async () => {
  const s = await school();
  const r = await run(s, "Find students who scored below 40 in any subject.", offline());
  eq(r.status, "completed", r.errors.join(" | "));
  const exp = [...new Set(marks.filter((m) => m.m < 40).map((m) => JSON.stringify([byId[m.sid].roll, subjects[m.sub], m.m])))].sort();
  const got = r.rows.map((x) => JSON.stringify([x[col(r, "roll_no")], x[col(r, "subject")], x[col(r, "marks_obtained")]])).sort();
  eq(got, exp);
});
await test("average marks for each subject matches ground truth", async () => {
  const s = await school();
  const r = await run(s, "Calculate average marks for each subject.", offline());
  eq(r.status, "completed", r.errors.join(" | "));
  const exp = Object.values(subjects).sort().map((name) => { const xs = marks.filter((m) => subjects[m.sub] === name).map((m) => m.m); return [name, round2(xs.reduce((a, b) => a + b, 0) / xs.length)]; });
  eq(r.rows.map((x) => [x[0], x[1]]), exp);
});
await test("top 5 students by average marks matches ground truth", async () => {
  const s = await school();
  const r = await run(s, "Show the top 5 students by average marks.", offline());
  eq(r.status, "completed", r.errors.join(" | "));
  const avg = students.map((x) => { const xs = marks.filter((m) => m.sid === x.id).map((m) => m.m); return [x.roll, round2(xs.reduce((a, b) => a + b, 0) / xs.length)]; })
    .sort((a, b) => b[1] - a[1]).slice(0, 5);
  eq(r.rows.map((x) => [x[col(r, "roll_no")], x[col(r, "average_marks")]]), avg);
});
await test("how many students are in each class", async () => {
  const s = await school();
  const r = await run(s, "How many students are in each class?", offline());
  eq(r.status, "completed", r.errors.join(" | "));
  eq(r.rows, [[9, 30], [10, 30]]);
});
await test("empty result is completed and exported with a 'no records' row", async () => {
  const s = await school();
  const r = await run(s, "Find students whose attendance is below 1%.", offline());
  eq(r.status, "completed");
  eq(r.rows.length, 0);
  const wb = await loadBuf(r.exports[0].data);
  ok(/No matching records/.test(wb.getWorksheet("Results").getCell("A2").value), "message row");
});
await test("Excel export: real percentages/dates, frozen header, Query Info sheet, CSV", async () => {
  const s = await school();
  const r = await run(s, "Find students whose attendance is below 50%.", offline());
  const wb = await loadBuf(r.exports.find((x) => x.format === "xlsx").data);
  const ws = wb.getWorksheet("Results");
  eq(ws.getRow(1).values.slice(1), ["Student Name", "Roll No", "Attendance Percentage"]);
  eq(ws.rowCount - 1, r.rows.length);
  for (let i = 0; i < r.rows.length; i++) {
    eq(ws.getRow(i + 2).values.slice(1), r.rows[i], `row ${i + 2}`);
    eq(ws.getRow(i + 2).getCell(3).numFmt, "0.00%");
  }
  eq(ws.views[0].state, "frozen");
  const info = Object.fromEntries(wb.getWorksheet("Query Info").getSheetValues().filter(Boolean).map((v) => [v[1], v[2]]));
  eq(info["Record count"], r.rows.length);
  ok(info["Executed SQL"].includes("attendance_pct"), "SQL recorded");
  const csv = r.exports.find((x) => x.format === "csv").data;
  ok(csv.startsWith("\uFEFFstudent_name,roll_no,attendance_percentage"), "csv header");
  eq(csv.trim().split("\r\n").length, r.rows.length + 1);
});
await test("you can type SQL directly (still validated)", async () => {
  const s = await school();
  const r = await run(s, "SELECT class, section, COUNT(*) AS n FROM students GROUP BY class, section", offline());
  eq(r.status, "completed");
  eq(r.rows, [[9, "A", 15], [9, "B", 15], [10, "A", 15], [10, "B", 15]]);
});
await test("unsupported request in test mode explains itself (needs_input)", async () => {
  const s = await school();
  const r = await run(s, "Write me a poem about exams", offline());
  eq(r.status, "needs_input");
  ok(/Test mode only understands/.test(r.errors.at(-1)));
});

console.log("\nChanging the Excel file (approve, apply, verify)");
await test("add Status column (Detained < 75% attendance) -> approve -> workbook verified cell by cell", async () => {
  const s = await school();
  let pendingSeen = null;
  const r = await run(s, "Add a column 'Status' with 'Detained' for students whose attendance is below 75%, otherwise 'Eligible'.", offline(), {},
    (run) => { pendingSeen = run.pending; run.respond("change_approval", { approved: true, mode: "download" }); });
  eq(r.status, "completed", r.errors.join(" | "));
  eq(pendingSeen.type, "change_approval");
  eq(pendingSeen.change.newColumns[0].header, "Status");
  eq(pendingSeen.change.affectedRows, 60);
  const wb = await loadBuf(r.exports[0].data);
  const ws = wb.getWorksheet("Students");
  eq(ws.getCell("I2").value, "Status");
  eq(ws.getCell("I2").font && ws.getCell("I2").font.bold, true, "header style copied");
  for (let i = 0; i < students.length; i++) eq(ws.getCell(`I${i + 3}`).value, students[i].att < 0.75 ? "Detained" : "Eligible", `row ${i + 3}`);
  eq(ws.getCell("A1").value, "Greenfield Public School - Student Register 2026-27");
  eq(ws.getCell("H3").value, students[0].att);
  const mk = wb.getWorksheet("Marks");
  eq(mk.getCell("G2").formula, "E2/F2"); eq(mk.getCell("G601").formula, "E601/F601");
  eq(r.exports[0].filename, "school_student_marks_edited.xlsx");
  // the original file on disk is untouched
  const orig = await loadBuf(bufOf(XLSX_PATH)); eq(orig.getWorksheet("Students").getCell("I2").value, null);
});
await test("nothing is written when the change is rejected", async () => {
  const s = await school();
  const r = await run(s, "Delete students whose attendance is below 40%", offline(), {}, (run) => run.respond("change_approval", { approved: false }));
  eq(r.status, "cancelled");
  eq(r.exports.length, 0);
  eq(s.db.exec("SELECT COUNT(*) FROM students")[0].values[0][0], 60);
});
await test("delete rows: removed from the workbook, other rows intact", async () => {
  const s = await school();
  const r = await run(s, "Delete students whose attendance is below 40%", offline(), {}, (run) => run.respond("change_approval", { approved: true, mode: "download" }));
  eq(r.status, "completed", r.errors.join(" | "));
  const wb = await loadBuf(r.exports[0].data);
  const kept = []; wb.getWorksheet("Students").eachRow((row, n) => { if (n > 2) kept.push(row.values[2]); });
  eq(kept, students.filter((x) => x.att >= 0.4).map((x) => x.roll));
});
await test("deleting Marks rows keeps every remaining formula pointing at its own row", async () => {
  const s = await school();
  const p = scripted({ interpret: [goal("modify")], generate_sql: [prop("DELETE FROM marks WHERE marks_obtained < :m", { m: 30 })] });
  const r = await run(s, "delete very low marks", p, {}, (run) => run.respond("change_approval", { approved: true, mode: "download" }));
  eq(r.status, "completed", r.errors.join(" | "));
  const ws = (await loadBuf(r.exports[0].data)).getWorksheet("Marks");
  const expectedRows = marks.filter((m) => m.m >= 30).length;
  eq(ws.rowCount - 1, expectedRows);
  for (let n = 2; n <= ws.rowCount; n++) eq(ws.getCell(`G${n}`).formula, `E${n}/F${n}`, `formula row ${n}`);
});
await test("inserted Marks row continues the formula column", async () => {
  const s = await school();
  const p = scripted({ interpret: [goal("modify")], generate_sql: [prop("INSERT INTO marks (mark_id, student_id, subject_id, exam, marks_obtained, max_marks) VALUES (601, 'S001', 'SUB01', 'Retest', 77, 100)")] });
  const r = await run(s, "add a retest mark", p, {}, (run) => run.respond("change_approval", { approved: true, mode: "download" }));
  eq(r.status, "completed", r.errors.join(" | "));
  const ws = (await loadBuf(r.exports[0].data)).getWorksheet("Marks");
  eq(ws.getRow(602).values.slice(1, 7), [601, "S001", "SUB01", "Retest", 77, 100]);
  eq(ws.getCell("G602").formula, "E602/F602");
});
await test("save back into the opened file (simulated handle), then the mirror is refreshed", async () => {
  const s = await school();
  let written = null;
  const saveBack = async (src, buf) => { written = buf; return { ok: true, previous: bufOf(XLSX_PATH) }; };
  s.handle = {}; // stands in for a File System Access handle
  const r = await run(s, "Add a column 'Status' with 'Detained' for students whose attendance is below 75%, otherwise 'Eligible'.", offline(), { saveBack },
    (run) => { ok(run.pending.canSaveBack, "save-back offered"); run.respond("change_approval", { approved: true, mode: "saveback" }); });
  eq(r.status, "completed"); ok(written, "bytes handed to the browser writer"); eq(r.applyResult.savedBack, true);
  eq(s.db.exec("SELECT COUNT(*) FROM students WHERE status = 'Detained'")[0].values[0][0], students.filter((x) => x.att < 0.75).length);
});
await test("save-back refused (file changed) falls back to download", async () => {
  const s = await school(); s.handle = {};
  const r = await run(s, "Delete students whose attendance is below 40%", offline(), { saveBack: async () => ({ ok: false, reason: "file changed on disk" }) },
    (run) => run.respond("change_approval", { approved: true, mode: "saveback" }));
  eq(r.status, "completed"); eq(r.applyResult.savedBack, false); ok(r.events.some((e) => /file changed on disk/.test(e.message)));
  eq(s.db.exec("SELECT COUNT(*) FROM students")[0].values[0][0], 60, "source untouched");
});

console.log("\nSafety");
for (const [label, sql] of [["DROP TABLE", "DROP TABLE students"], ["stacked DELETE", "SELECT 1; DELETE FROM students"],
  ["UPDATE in read mode", "UPDATE students SET class = 1"], ["CTE hiding a DELETE", "WITH x AS (SELECT 1) DELETE FROM students"],
  ["ATTACH", "ATTACH DATABASE ':memory:' AS x"], ["PRAGMA", "PRAGMA writable_schema = 1"]]) {
  await test(`blocked: ${label}`, async () => {
    const s = await school();
    const r = await run(s, "x", scripted({ interpret: [goal()], generate_sql: [prop(sql)] }));
    eq(r.status, "blocked", r.errors.join(" | "));
    eq(s.db.exec("SELECT COUNT(*) FROM students")[0].values[0][0], 60);
  });
}
await test("SQL typed by the user is checked too (DROP blocked in test mode)", async () => {
  const s = await school();
  const r = await run(s, "SELECT * FROM students; DROP TABLE students", offline());
  eq(r.status, "blocked");
});
await test("injection text is bound as a parameter, never executed", async () => {
  const s = await school();
  const evil = "x'; DROP TABLE students; --";
  const r = await run(s, "find " + evil, scripted({ interpret: [goal()], generate_sql: [prop("SELECT roll_no FROM students WHERE student_name = :n", { n: evil })] }));
  eq(r.status, "completed"); eq(r.rows.length, 0); eq(s.db.exec("SELECT COUNT(*) FROM students")[0].values[0][0], 60);
});
await test("formula columns cannot be overwritten; rowid tampering blocked", async () => {
  const s = await school();
  const d = L.dryRunModify(s, "UPDATE marks SET percentage = 1", {}, []);
  ok(/formulas/.test(d.errors[0]), d.errors[0]);
  const d2 = L.dryRunModify(s, "UPDATE students SET rowid = 5", {}, []);
  ok(d2.security, "rowid is a security violation");
  const d3 = L.dryRunModify(s, "INSERT INTO students VALUES ('S999','x','y',9,'A','M','2010-01-01',0.5)", {}, []);
  ok(/list target columns/.test(d3.errors[0]));
});
await test("databases and CSV files are read-only", async () => {
  const sample = await L.createSampleSource();
  const r = await run(sample, "x", scripted({ interpret: [goal("modify")] }));
  eq(r.status, "blocked");
  const csv = await L.sourceFromFile("grades.csv", new TextEncoder().encode("Name,Score\nA,10\nB,20\n").buffer);
  const r2 = await run(csv, "x", scripted({ interpret: [goal("modify")] }));
  eq(r2.status, "blocked");
  const r3 = await run(csv, "SELECT name, score FROM grades WHERE score > 15", offline());
  eq(r3.rows, [["B", 20]]);
});
await test("a query can never leave changes behind (savepoint rollback)", async () => {
  const s = await school();
  L.executeRead(s, "SELECT * FROM students", {});
  eq(s.db.exec("SELECT COUNT(*) FROM students")[0].values[0][0], 60);
});
await test("result-size limit is enforced and disclosed", async () => {
  const s = await school();
  const before = L.CONFIG.maxResultRows; L.CONFIG.maxResultRows = 10;
  try {
    const r = await run(s, "SELECT * FROM marks", offline());
    eq(r.rows.length, 10); eq(r.result.truncated, true);
    const info = Object.fromEntries((await loadBuf(r.exports[0].data)).getWorksheet("Query Info").getSheetValues().filter(Boolean).map((v) => [v[1], v[2]]));
    ok(/first 10 rows/.test(info.Notes), info.Notes);
  } finally { L.CONFIG.maxResultRows = before; }
});

console.log("\nAgent loop with a model (scripted stand-in for Gemini)");
await test("self-correction: unknown column -> error fed back -> corrected on iteration 2", async () => {
  const s = await school();
  const p = scripted({ interpret: [goal()], generate_sql: [prop("SELECT roll_number FROM students"), (ctx) => {
    ok(ctx.history.length === 1 && /no such column: roll_number/.test(ctx.history[0].error), "error passed to the model");
    return prop("SELECT roll_no FROM students WHERE class = :c", { c: 10 });
  }] });
  const r = await run(s, "class 10 roll numbers", p);
  eq(r.status, "completed"); eq(r.iteration, 2); eq(r.rows.length, 30);
  eq(r.attempts.map((a) => a.outcome), ["validation_error", "success"]);
});
await test("iteration limit: 5 failures then stop", async () => {
  const s = await school();
  let i = 0;
  const p = scripted({ interpret: [goal()], generate_sql: [() => prop(`SELECT nope_${i++} FROM students`)] });
  const r = await run(s, "x", p);
  eq(r.status, "failed"); eq(r.iteration, 5); ok(/within 5 iterations/.test(r.errors.at(-1)));
});
await test("identical query is not re-run; repeated twice -> stop", async () => {
  const s = await school();
  const p = scripted({ interpret: [goal()], generate_sql: [() => prop("SELECT roll_no FROM students")],
    evaluate: [() => ({ satisfied: false, issues: ["no"], refinement: "add names", summary: "", formats: [] })] });
  const r = await run(s, "x", p);
  eq(r.attempts.map((a) => a.outcome), ["unsatisfied", "duplicate", "duplicate"]); eq(r.status, "failed");
});
await test("clarifying question and asking for a data source", async () => {
  const s = await school();
  const p = scripted({ interpret: [goal("read", { needs_clarification: true, clarification_question: "Overall or per subject?" }), goal()],
    generate_sql: [prop("SELECT COUNT(*) AS n FROM students")] });
  const r = await run(null, "low attendance", p, { sources: [s] }, (run) => {
    if (run.pending.type === "source") run.respond("source", { sourceId: s.id });
    else run.respond("clarification", { answer: "Overall" });
  });
  eq(r.status, "completed"); eq(r.clarifications[0].answer, "Overall");
});
await test("SQL review: edited SQL re-validated; unsafe edit rejected", async () => {
  const s = await school();
  let step = 0;
  const r = await run(s, "x", scripted({ interpret: [goal()], generate_sql: [prop("SELECT roll_no FROM students")] }), { reviewSql: true }, (run) => {
    if (step++ === 0) run.respond("sql_approval", { approved: true, sql: "DELETE FROM students" });
    else { ok(/rejected/.test(run.pending.error)); run.respond("sql_approval", { approved: true, sql: "SELECT roll_no FROM students WHERE section = 'A'" }); }
  });
  eq(r.status, "completed"); eq(r.rows.length, 30);
});

console.log("\nGemini REST client (mocked network)");
function fakeFetch(steps) {
  const calls = [];
  const f = async (url, init) => {
    calls.push({ url, init });
    const s = steps.length > 1 ? steps.shift() : steps[0];
    return { ok: s.status === 200, status: s.status, statusText: "", json: async () => s.body };
  };
  f.calls = calls; return f;
}
const geminiOk = (obj) => ({ status: 200, body: { candidates: [{ content: { parts: [{ text: JSON.stringify(obj) }] } }] } });
await test("request format: model URL, key header, JSON schema, system instruction", async () => {
  const f = fakeFetch([geminiOk(goal())]);
  const g = new L.GeminiProvider({ apiKey: " KEY ", model: "models/gemini-x", fetchImpl: f, sleepImpl: async () => {} });
  const s = await school();
  const out = await g.generate("interpret", { request: "hi", schemaText: "S", source: { name: "n", kind: "excel", editable: true }, today: "2026-10-27", clarifications: [] });
  eq(out.operation, "read");
  ok(f.calls[0].url.endsWith("/models/gemini-x:generateContent"), f.calls[0].url);
  eq(f.calls[0].init.headers["x-goog-api-key"], "KEY");
  const body = JSON.parse(f.calls[0].init.body);
  eq(body.generationConfig.responseMimeType, "application/json"); ok(body.generationConfig.responseJsonSchema.properties.operation);
  ok(body.systemInstruction.parts[0].text.includes("LoopSQL"));
  void s;
});
await test("429 with retryDelay waits as instructed, then succeeds", async () => {
  const waits = [];
  const f = fakeFetch([{ status: 429, body: { error: { message: "quota", details: [{ "@type": "type.googleapis.com/google.rpc.RetryInfo", retryDelay: "37s" }] } } }, geminiOk(goal())]);
  const g = new L.GeminiProvider({ apiKey: "k", model: "m", fetchImpl: f, sleepImpl: async (ms) => waits.push(ms) });
  await g.generate("interpret", { request: "x", schemaText: "", source: {}, today: "", clarifications: [] });
  eq(waits, [37000]);
});
await test("rejected key fails immediately with a clear message", async () => {
  const f = fakeFetch([{ status: 403, body: { error: { message: "API key not valid" } } }]);
  const g = new L.GeminiProvider({ apiKey: "bad", model: "m", fetchImpl: f, sleepImpl: async () => {} });
  let err; try { await g.generate("interpret", { request: "x", schemaText: "", source: {}, today: "", clarifications: [] }); } catch (e) { err = e; }
  ok(err && /rejected the API key/.test(err.message), err && err.message); eq(f.calls.length, 1);
});
await test("invalid JSON is retried; unknown model gives a 'Check key' hint", async () => {
  const f = fakeFetch([{ status: 200, body: { candidates: [{ content: { parts: [{ text: "not json" }] } }] } }, geminiOk(goal())]);
  const g = new L.GeminiProvider({ apiKey: "k", model: "m", fetchImpl: f, sleepImpl: async () => {} });
  await g.generate("interpret", { request: "x", schemaText: "", source: {}, today: "", clarifications: [] });
  const g2 = new L.GeminiProvider({ apiKey: "k", model: "nope", fetchImpl: fakeFetch([{ status: 404, body: { error: { message: "not found" } } }]), sleepImpl: async () => {} });
  let err; try { await g2.generate("interpret", { request: "x", schemaText: "", source: {}, today: "", clarifications: [] }); } catch (e) { err = e; }
  ok(/Check key/.test(err.message));
});
await test("schema rejected with a generic 400 -> retried once with the schema in the prompt", async () => {
  const f = fakeFetch([{ status: 400, body: { error: { message: "Invalid JSON payload received. Unknown name \"type\"", status: "INVALID_ARGUMENT" } } }, geminiOk(goal())]);
  const g = new L.GeminiProvider({ apiKey: "k", model: "m", fetchImpl: f, sleepImpl: async () => {} });
  const out = await g.generate("interpret", { request: "x", schemaText: "", source: {}, today: "", clarifications: [] });
  eq(out.operation, "read"); eq(f.calls.length, 2);
  const second = JSON.parse(f.calls[1].init.body);
  ok(!second.generationConfig.responseJsonSchema && /Reply with JSON matching this schema/.test(second.contents[0].parts[0].text), "fallback request");
});
await test("Google's real bad-key reply (400 'API key not valid') is reported as a key problem", async () => {
  const f = fakeFetch([{ status: 400, body: { error: { code: 400, message: "API key not valid. Please pass a valid API key.", status: "INVALID_ARGUMENT" } } }]);
  const g = new L.GeminiProvider({ apiKey: "bad", model: "m", fetchImpl: f, sleepImpl: async () => {} });
  let err; try { await g.generate("interpret", { request: "x", schemaText: "", source: {}, today: "", clarifications: [] }); } catch (e) { err = e; }
  ok(err && /rejected the API key/.test(err.message), err && err.message); eq(f.calls.length, 1);
});
await test("missing key is reported before any request", async () => {
  let err; try { new L.GeminiProvider({ apiKey: "  ", model: "m" }); } catch (e) { err = e; }
  ok(err && /API key/.test(err.message));
});
await test("full run through the Gemini client (mocked network) on the school workbook", async () => {
  const s = await school();
  const f = fakeFetch([geminiOk(goal()),
    geminiOk({ diagnosis: null, plan: ["filter"], sql: "SELECT roll_no, student_name FROM students WHERE attendance_pct < :t", params: [{ name: "t", value: 0.5 }], expected_columns: [], new_columns: [], rationale: "r" }),
    geminiOk({ satisfied: true, issues: [], refinement_instructions: null, summary: "Low attendance list.", column_formats: [{ column: "roll_no", label: "Roll No", kind: "text" }] })]);
  const r = await run(s, "low attendance", new L.GeminiProvider({ apiKey: "k", model: "m", fetchImpl: f, sleepImpl: async () => {} }));
  eq(r.status, "completed"); eq(r.rows.length, students.filter((x) => x.att < 0.5).length); eq(f.calls.length, 3);
  ok(JSON.parse(f.calls[1].init.body).contents[0].parts[0].text.includes("attendance_pct"), "schema was in the prompt");
});

console.log("\nSample database & helpers");
await test("education sample: attendance (aggregated) and average per course (joins names)", async () => {
  const s = await L.createSampleSource();
  const r = await run(s, "Find all students whose attendance is below 50%.", offline());
  eq(r.status, "completed", r.errors.join(" | "));
  const truth = s.db.exec("SELECT COUNT(*) FROM (SELECT student_id FROM attendance GROUP BY student_id HAVING SUM(classes_attended)*100.0/SUM(classes_held) < 50)")[0].values[0][0];
  eq(r.rows.length, truth); ok(truth > 0);
  const r2 = await run(s, "Calculate average marks for each course.", offline());
  eq(r2.status, "completed", r2.errors.join(" | ")); eq(r2.columns[0], "course_name"); eq(r2.rows.length, 3);
});
await test("shiftFormula moves relative refs only", async () => {
  eq(L.shiftFormula("E5/F5", -1), "E4/F4");
  eq(L.shiftFormula("$E$5*F5+LOG10(2)", 2), "$E$5*F7+LOG10(2)");
  eq(L.shiftFormula('IF(A3>0,"B3",C3)', 1), 'IF(A4>0,"B3",C4)');
  eq(L.shiftFormula("SUM(Sheet2!B2:B9)", 1), "SUM(Sheet2!B3:B10)");
});

// =================================================================================================
console.log("\nWhole page in jsdom (UI wiring, test mode)");
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true, url: "file:///C:/loopsql/index.html",
  beforeParse(w) { w.initSqlJs = initSqlJs; w.ExcelJS = ExcelJS; w.LOOPSQL_WASM_BASE = WASM_BASE; w.TextDecoder = TextDecoder; w.TextEncoder = TextEncoder;
    w.URL.createObjectURL = () => "blob:test"; w.URL.revokeObjectURL = () => {}; } });
const w = dom.window; const d = w.document;
const until = async (pred, ms = 15000) => { const t = Date.now(); while (Date.now() - t < ms) { if (pred()) return; await new Promise((r) => setTimeout(r, 20)); } throw new Error("timeout waiting for UI"); };
await until(() => w.__loopsqlUI);
await test("page boots in test mode without errors", async () => {
  eq(d.getElementById("modePill").textContent, "Test mode (no AI)");
  ok(d.getElementById("fatal").classList.contains("hidden"), "no fatal banner: " + d.getElementById("fatal").textContent);
});
await test("open the school workbook -> sources + schema + school prompt chips", async () => {
  await w.__loopsqlUI.openBuffer("school_student_marks.xlsx", bufOf(XLSX_PATH), null, null);
  eq(d.querySelectorAll("#sources li").length, 1);
  eq(d.querySelectorAll("#schema > li").length, 3);
  ok([...d.querySelectorAll("#chips .chip")].some((c) => /Add a column 'Status'/.test(c.textContent)), "modify chip offered for Excel");
});
await test("Run Agent from the form -> results table, downloads, timeline", async () => {
  d.getElementById("request").value = "Find students whose attendance is below 50% and export their names, roll numbers and attendance.";
  d.getElementById("reqForm").dispatchEvent(new w.Event("submit", { cancelable: true }));
  await until(() => /Completed/.test(d.querySelector("#runPanel .status")?.textContent || ""));
  await new Promise((r) => setTimeout(r, 50));
  const expected = students.filter((x) => x.att < 0.5).length;
  eq(d.querySelectorAll("#runPanel table.data tbody tr").length, expected);
  ok(/^\d+\.\d%$/.test(d.querySelector("#runPanel table.data tbody tr td:last-child").textContent), "attendance shown as a percentage");
  const buttons = [...d.querySelectorAll("#runPanel .downloads button")].map((b) => b.textContent);
  eq(buttons, ["⬇ Download Excel", "⬇ Download CSV"]);
  ok(d.querySelectorAll("#runPanel .timeline li").length >= 10, "timeline rendered");
  ok(/attendance_pct/.test(d.querySelector("#runPanel pre.sql").textContent));
});
await test("results search filters rows", async () => {
  const target = students.filter((x) => x.att < 0.5)[0].roll;
  const input = d.querySelector('#runPanel input[aria-label="Search results"]');
  input.value = target; input.dispatchEvent(new w.Event("input"));
  await new Promise((r) => setTimeout(r, 30));
  eq(d.querySelectorAll("#runPanel table.data tbody tr").length, 1);
});
await test("modify via UI: approval card -> Approve -> edited workbook download offered", async () => {
  d.getElementById("request").value = "Add a column 'Status' with 'Detained' for students whose attendance is below 75%, otherwise 'Eligible'.";
  d.getElementById("reqForm").dispatchEvent(new w.Event("submit", { cancelable: true }));
  await until(() => d.querySelector("#runPanel .pending"));
  ok(/Approve changes to school_student_marks.xlsx/.test(d.querySelector("#runPanel .pending h3").textContent));
  ok(d.querySelectorAll("#runPanel .pending table.data tbody tr").length > 0, "diff table");
  [...d.querySelectorAll("#runPanel .pending button")].find((b) => b.textContent === "Approve & apply").click();
  await until(() => /Completed/.test(d.querySelector("#runPanel .status")?.textContent || ""));
  await new Promise((r) => setTimeout(r, 50));
  eq([...d.querySelectorAll("#runPanel .downloads button")].map((b) => b.textContent), ["⬇ Download edited workbook"]);
  const data = w.__loopsqlUI.state.current.exports[0].data;
  const ws = (await loadBuf(data)).getWorksheet("Students");
  eq(ws.getCell("I2").value, "Status");
  eq(d.querySelectorAll("#history li").length, 2);
});
await test("Gemini selected without a key -> clear message, no run", async () => {
  d.getElementById("engGemini").checked = true; d.getElementById("engGemini").dispatchEvent(new w.Event("change"));
  eq(d.getElementById("modePill").textContent, "Gemini key missing");
  d.getElementById("request").value = "anything";
  d.getElementById("reqForm").dispatchEvent(new w.Event("submit", { cancelable: true }));
  await new Promise((r) => setTimeout(r, 30));
  ok(/Enter your Gemini API key/.test(d.getElementById("startError").textContent), d.getElementById("startError").textContent);
  d.getElementById("engOffline").checked = true; d.getElementById("engOffline").dispatchEvent(new w.Event("change"));
});
await test("load the built-in sample from the button", async () => {
  d.getElementById("loadSample").click();
  await until(() => d.querySelectorAll("#sources li").length === 2);
  eq(d.querySelectorAll("#schema > li").length, 5);
});

// =================================================================================================
const failed = results.filter((r) => !r[0]);
console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
w.close();
process.exit(failed.length ? 1 : 0);
