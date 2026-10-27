// Builds ../school_student_marks.xlsx: a synthetic school database (no real people), deterministic.
// Saved with cached formula results, like a file saved by Excel.
import ExcelJS from "exceljs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const OUT = process.argv[2] || path.join(here, "..", "school_student_marks.xlsx");

function mulberry32(seed) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rnd = mulberry32(2026);
const pick = (a) => a[Math.floor(rnd() * a.length)];
const between = (lo, hi) => lo + rnd() * (hi - lo);

const FIRST = ["Aarav", "Vivaan", "Aditya", "Arjun", "Sai", "Ishaan", "Kabir", "Rohan", "Dev", "Nikhil",
  "Ananya", "Diya", "Saanvi", "Pari", "Anika", "Navya", "Myra", "Kiara", "Meera", "Riya", "Tanvi", "Kavya"];
const LAST = ["Sharma", "Negi", "Rawat", "Bisht", "Joshi", "Thapa", "Gupta", "Chauhan", "Pant", "Bhatt",
  "Semwal", "Nautiyal", "Rana", "Kumar", "Mehta"];
const SUBJECTS = [["SUB01", "Mathematics", "Mr. R. Bhatt"], ["SUB02", "Science", "Ms. A. Joshi"],
  ["SUB03", "English", "Mrs. K. Rawat"], ["SUB04", "Hindi", "Mr. S. Pant"], ["SUB05", "Social Science", "Ms. P. Negi"]];

const wb = new ExcelJS.Workbook();
wb.creator = "LoopSQL AI sample generator";
const header = (ws, row) => {
  row.eachCell((c) => {
    c.font = { bold: true, color: { argb: "FFFFFFFF" } };
    c.fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FF1F3A5F" } };
  });
  ws.views = [{ state: "frozen", ySplit: row.number }];
};

// ---- Students (title row above the header, like many real registers) ----------------------
const st = wb.addWorksheet("Students");
st.mergeCells("A1:H1");
st.getCell("A1").value = "Greenfield Public School - Student Register 2026-27";
st.getCell("A1").font = { bold: true, size: 14 };
header(st, st.addRow(["Student ID", "Roll No", "Student Name", "Class", "Section", "Gender", "Date of Birth", "Attendance %"]));
st.columns.forEach((c, i) => (c.width = [11, 9, 22, 7, 8, 8, 13, 13][i]));
const students = [];
for (let i = 1; i <= 60; i++) {
  const cls = i <= 30 ? 9 : 10;
  const section = (i - 1) % 30 < 15 ? "A" : "B";
  const name = `${pick(FIRST)} ${pick(LAST)}`;
  const chronic = i % 9 === 4;          // ~7 students with very low attendance
  const borderline = i % 5 === 2;       // some between 50% and 75%
  const att = chronic ? between(0.28, 0.49) : borderline ? between(0.55, 0.74) : between(0.76, 0.99);
  const dob = new Date(Date.UTC(2026 - (cls + 5), Math.floor(rnd() * 12), 1 + Math.floor(rnd() * 28)));
  const s = { id: `S${String(i).padStart(3, "0")}`, roll: `${cls}${section}${String(((i - 1) % 15) + 1).padStart(2, "0")}`,
    name, cls, section, gender: pick(["Male", "Female"]), dob, att: Math.round(att * 1000) / 1000, weak: i % 7 === 3 };
  students.push(s);
  const r = st.addRow([s.id, s.roll, s.name, s.cls, s.section, s.gender, s.dob, s.att]);
  r.getCell(7).numFmt = "dd-mmm-yyyy";
  r.getCell(8).numFmt = "0.0%";
}

// ---- Subjects ----------------------------------------------------------------------------
const sb = wb.addWorksheet("Subjects");
header(sb, sb.addRow(["Subject ID", "Subject Name", "Teacher", "Max Marks"]));
sb.columns.forEach((c, i) => (c.width = [11, 18, 18, 10][i]));
for (const [id, name, teacher] of SUBJECTS) sb.addRow([id, name, teacher, 100]);

// ---- Marks (Percentage is a formula column with cached results) -------------------------------
const mk = wb.addWorksheet("Marks");
header(mk, mk.addRow(["Mark ID", "Student ID", "Subject ID", "Exam", "Marks Obtained", "Max Marks", "Percentage"]));
mk.columns.forEach((c, i) => (c.width = [9, 11, 11, 13, 15, 10, 12][i]));
let markId = 1;
for (const s of students) {
  for (const [subId] of SUBJECTS) {
    for (const exam of ["Half Yearly", "Final"]) {
      let m = s.weak ? between(18, 45) : between(42, 98);
      if (!s.weak && rnd() < 0.04) m = between(25, 39);
      m = Math.round(m);
      const r = mk.addRow([markId++, s.id, subId, exam, m, 100]);
      r.getCell(7).value = { formula: `E${r.number}/F${r.number}`, result: m / 100 };
      r.getCell(7).numFmt = "0.0%";
    }
  }
}
mk.autoFilter = "A1:G1";

await wb.xlsx.writeFile(OUT);
console.log(`Wrote ${OUT}: ${students.length} students, ${SUBJECTS.length} subjects, ${markId - 1} marks rows`);
