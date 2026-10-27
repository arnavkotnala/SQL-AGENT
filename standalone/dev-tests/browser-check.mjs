// Real-browser check: opens ../index.html from disk (file://) in headless Edge/Chrome, so the libraries and the
// SQLite WebAssembly are fetched from the CDN exactly as when a user double-clicks the file. Drives the page
// through the DevTools protocol.  Usage: node browser-check.mjs [screenshot-dir]
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const shotDir = process.argv[2] || here;
const browser = ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe"].find((p) => fs.existsSync(p));
if (!browser) { console.error("Edge/Chrome not found"); process.exit(2); }
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "loopsql-browser-"));
const port = 9300 + Math.floor(Math.random() * 500);
const pageUrl = pathToFileURL(path.join(here, "..", "index.html")).href;
const proc = spawn(browser, ["--headless=new", "--disable-gpu", "--no-first-run", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "--window-size=1400,1000", pageUrl], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let ws; let id = 0; const waiting = new Map();
const results = [];
function check(name, cond, info = "") { results.push(!!cond); console.log(`  ${cond ? "✓" : "✗"} ${name}${info ? "  — " + info : ""}`); }
function send(method, params = {}) {
  return new Promise((resolve, reject) => { const i = ++id; waiting.set(i, { resolve, reject }); ws.send(JSON.stringify({ id: i, method, params })); });
}
async function evaluate(expr) {
  const r = await send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}
async function shot(name, height = 1400) {
  await send("Emulation.setDeviceMetricsOverride", { width: 1400, height, deviceScaleFactor: 1, mobile: false });
  const { data } = await send("Page.captureScreenshot", { format: "png" });
  const p = path.join(shotDir, name); fs.writeFileSync(p, Buffer.from(data, "base64")); return p;
}
const WAIT = `const until = async (f, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { const v = f(); if (v) return v; await new Promise((r) => setTimeout(r, 50)); } throw new Error("timeout"); };`;

try {
  let target;
  for (let i = 0; i < 100 && !target; i++) {
    try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === "page" && t.url.startsWith("file:")); } catch { /* not up yet */ }
    if (!target) await sleep(200);
  }
  if (!target) throw new Error("browser did not expose the page");
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  ws.onmessage = (m) => { const msg = JSON.parse(m.data); const w = waiting.get(msg.id); if (w) { waiting.delete(msg.id); msg.error ? w.reject(new Error(msg.error.message)) : w.resolve(msg.result); } };
  await send("Runtime.enable");
  console.log(`Browser: ${path.basename(browser)}  page: ${pageUrl}`);

  const libs = await evaluate(`${WAIT} await until(() => window.__loopsqlUI); await LoopSQL.initEngine();
    return { sqljs: typeof initSqlJs, exceljs: typeof ExcelJS, fatal: document.getElementById("fatal").classList.contains("hidden") ? "" : document.getElementById("fatal").textContent,
      sqlite: (await LoopSQL.createSampleSource()).db.exec("select sqlite_version()")[0].values[0][0], protocol: location.protocol };`);
  check("libraries load from the CDN on a file:// page (sql.js + ExcelJS)", libs.sqljs === "function" && libs.exceljs === "object" && !libs.fatal, JSON.stringify(libs));
  check("SQLite WebAssembly runs in the browser", /^3\./.test(libs.sqlite), "SQLite " + libs.sqlite);

  const b64 = fs.readFileSync(path.join(here, "..", "school_student_marks.xlsx")).toString("base64");
  const opened = await evaluate(`const bin = atob("${b64}"); const u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    await __loopsqlUI.openBuffer("school_student_marks.xlsx", u.buffer, null, null);
    return { sources: document.querySelectorAll("#sources li").length, tables: [...document.querySelectorAll("#schema .mono")].map((e) => e.textContent) };`);
  check("school workbook opens; schema shows its 3 sheets", opened.sources === 1 && opened.tables.join(",") === "students,subjects,marks", JSON.stringify(opened));

  const q = await evaluate(`${WAIT}
    document.getElementById("request").value = "Find students whose attendance is below 50% and export their names, roll numbers and attendance.";
    document.getElementById("reqForm").requestSubmit();
    await until(() => /Completed|Failed|Blocked/.test(document.querySelector("#runPanel .status")?.textContent || ""));
    await new Promise((r) => setTimeout(r, 100));
    const run = __loopsqlUI.state.current;
    return { status: run.status, rows: run.rows.length, tableRows: document.querySelectorAll("#runPanel table.data tbody tr").length,
      first: run.rows[0], downloads: [...document.querySelectorAll("#runPanel .downloads button")].map((b) => b.textContent), xlsxBytes: run.exports[0].data.byteLength };`);
  check("query from the form: 7 students below 50% attendance, table + downloads shown", q.status === "completed" && q.rows === 7 && q.tableRows === 7 && q.downloads.length === 2, JSON.stringify(q));
  console.log("    screenshot:", await shot("browser-query.png", 1700));

  const m = await evaluate(`${WAIT}
    document.getElementById("request").value = "Add a column 'Status' with 'Detained' for students whose attendance is below 75%, otherwise 'Eligible'.";
    document.getElementById("reqForm").requestSubmit();
    await until(() => document.querySelector("#runPanel .pending"));
    const preview = { title: document.querySelector("#runPanel .pending h3").textContent, diffRows: document.querySelectorAll("#runPanel .pending table.data tbody tr").length };
    [...document.querySelectorAll("#runPanel .pending button")].find((b) => b.textContent === "Approve & apply").click();
    await until(() => /Completed|Failed/.test(document.querySelector("#runPanel .status")?.textContent || ""));
    const run = __loopsqlUI.state.current;
    const wb = new ExcelJS.Workbook(); await wb.xlsx.load(run.exports[0].data);
    const ws = wb.getWorksheet("Students");
    return { status: run.status, preview, header: ws.getCell("I2").value, i3: ws.getCell("I3").value, h3: ws.getCell("H3").value,
      formula: wb.getWorksheet("Marks").getCell("G2").formula, file: run.exports[0].filename };`);
  check("change: preview card -> Approve -> edited workbook re-opened in the browser has the Status column",
    m.status === "completed" && m.header === "Status" && m.formula === "E2/F2" && m.preview.diffRows === 60, JSON.stringify(m));
  console.log("    screenshot:", await shot("browser-change.png", 1500));

  const g = await evaluate(`document.getElementById("engGemini").click(); await new Promise((r) => setTimeout(r, 50));
    document.getElementById("request").value = "anything"; document.getElementById("reqForm").requestSubmit(); await new Promise((r) => setTimeout(r, 100));
    const out = { pill: document.getElementById("modePill").textContent, err: document.getElementById("startError").textContent };
    document.getElementById("engOffline").click(); return out;`);
  check("Gemini without a key gives a clear message", /key missing/.test(g.pill) && /Enter your Gemini API key/.test(g.err), JSON.stringify(g));

  const cors = await evaluate(`try { const r = await fetch("https://generativelanguage.googleapis.com/v1beta/models", { headers: { "x-goog-api-key": "invalid-key-for-cors-test" } });
    return { status: r.status, body: (await r.text()).slice(0, 120) }; } catch (e) { return { error: e.message }; }`);
  check("Gemini API reachable from a file:// page (CORS ok; fake key rejected by Google)", cors.status === 400 || cors.status === 403, JSON.stringify(cors));
} catch (e) {
  check("browser run", false, e.message);
} finally {
  try { ws && ws.close(); } catch { /* ignore */ }
  proc.kill();
  await sleep(500);
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* browser may hold files briefly */ }
}
const failed = results.filter((r) => !r).length;
console.log(`\n${results.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
