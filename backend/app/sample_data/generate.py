"""Deterministic synthetic sample databases (no real people). Run: python -m app.sample_data.generate"""
from __future__ import annotations

import random
import sqlite3
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

FIRST = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Ayaan", "Krishna", "Ishaan",
         "Ananya", "Diya", "Aadhya", "Saanvi", "Pari", "Anika", "Navya", "Myra", "Sara", "Kiara",
         "Rohan", "Kabir", "Dev", "Nikhil", "Tanvi", "Meera", "Riya", "Neha", "Kavya", "Aryan"]
LAST = ["Sharma", "Verma", "Negi", "Rawat", "Bisht", "Joshi", "Thapa", "Gupta", "Singh", "Chauhan",
        "Pant", "Bhatt", "Kumar", "Mehta", "Nautiyal", "Semwal", "Dobhal", "Kapoor", "Agarwal", "Rana"]

EDUCATION_SCHEMA = """
CREATE TABLE courses (
    course_id     INTEGER PRIMARY KEY,
    course_code   TEXT NOT NULL UNIQUE,
    course_name   TEXT NOT NULL,
    department    TEXT NOT NULL,
    duration_years INTEGER NOT NULL
);
CREATE TABLE students (
    student_id      INTEGER PRIMARY KEY,
    roll_number     TEXT NOT NULL UNIQUE,
    first_name      TEXT NOT NULL,
    last_name       TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    gender          TEXT NOT NULL CHECK (gender IN ('Male', 'Female')),
    date_of_birth   DATE NOT NULL,
    course_id       INTEGER NOT NULL REFERENCES courses(course_id),
    enrollment_year INTEGER NOT NULL,
    current_semester INTEGER NOT NULL
);
CREATE TABLE subjects (
    subject_id   INTEGER PRIMARY KEY,
    subject_code TEXT NOT NULL UNIQUE,
    subject_name TEXT NOT NULL,
    course_id    INTEGER NOT NULL REFERENCES courses(course_id),
    semester     INTEGER NOT NULL,
    credits      INTEGER NOT NULL
);
CREATE TABLE attendance (
    attendance_id    INTEGER PRIMARY KEY,
    student_id       INTEGER NOT NULL REFERENCES students(student_id),
    subject_id       INTEGER NOT NULL REFERENCES subjects(subject_id),
    classes_held     INTEGER NOT NULL,
    classes_attended INTEGER NOT NULL,
    attendance_percentage REAL NOT NULL,  -- 0 to 100
    UNIQUE (student_id, subject_id)
);
CREATE TABLE marks (
    mark_id        INTEGER PRIMARY KEY,
    student_id     INTEGER NOT NULL REFERENCES students(student_id),
    subject_id     INTEGER NOT NULL REFERENCES subjects(subject_id),
    exam_type      TEXT NOT NULL CHECK (exam_type IN ('Internal', 'Final')),
    marks_obtained REAL NOT NULL,
    max_marks      REAL NOT NULL,
    exam_date      DATE NOT NULL
);
"""

COURSES = [
    (1, "BTCSE", "B.Tech Computer Science", "Computer Science", 4,
     ["Data Structures", "Operating Systems", "Database Systems", "Machine Learning"]),
    (2, "BTECE", "B.Tech Electronics", "Electronics", 4,
     ["Digital Electronics", "Signals and Systems", "Microprocessors", "Communication Systems"]),
    (3, "BCA", "Bachelor of Computer Applications", "Computer Applications", 3,
     ["Programming in C", "Web Technologies", "Computer Networks", "Software Engineering"]),
    (4, "MBA", "Master of Business Administration", "Management", 2,
     ["Marketing Management", "Financial Accounting", "Organisational Behaviour", "Business Analytics"]),
    (5, "BSCPH", "B.Sc Physics", "Physics", 3,
     ["Classical Mechanics", "Electromagnetism", "Quantum Physics", "Mathematical Physics"]),
]


def _name(rng: random.Random) -> tuple[str, str]:
    return rng.choice(FIRST), rng.choice(LAST)


def generate_education(path: Path, n_students: int = 150, seed: int = 42) -> None:
    rng = random.Random(seed)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("PRAGMA foreign_keys = ON;" + EDUCATION_SCHEMA)
    subject_id = 1
    subjects_by_course: dict[int, list[int]] = {}
    for cid, code, name, dept, years, subs in COURSES:
        conn.execute("INSERT INTO courses VALUES (?,?,?,?,?)", (cid, code, name, dept, years))
        for i, sname in enumerate(subs):
            conn.execute("INSERT INTO subjects VALUES (?,?,?,?,?,?)",
                         (subject_id, f"{code}{101 + i}", sname, cid, 3 + (i % 2), rng.choice([3, 4])))
            subjects_by_course.setdefault(cid, []).append(subject_id)
            subject_id += 1

    used_emails: set[str] = set()
    att_id = mark_id = 1
    for sid in range(1, n_students + 1):
        cid = COURSES[(sid - 1) % len(COURSES)][0]
        code = COURSES[cid - 1][1]
        first, last = _name(rng)
        year = rng.choice([2022, 2023, 2024, 2025])
        roll = f"{code}{year}{sid:03d}"
        email = f"{first}.{last}.{sid}@example.edu".lower()
        used_emails.add(email)
        dob = date(year - 18, 1, 1) + timedelta(days=rng.randint(0, 900))
        conn.execute("INSERT INTO students VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (sid, roll, first, last, email, rng.choice(["Male", "Female"]), dob.isoformat(), cid, year,
                      min(8, (2026 - year) * 2 - 1) if code != "MBA" else min(4, (2026 - year) * 2 - 1)))
        # ~12% chronically absent students, ~15% weak performers
        absent_profile = sid % 8 == 3
        weak_profile = sid % 7 == 2
        for subj in subjects_by_course[cid]:
            held = rng.randint(38, 48)
            rate = rng.uniform(0.25, 0.49) if absent_profile else rng.uniform(0.55, 0.98)
            if not absent_profile and rng.random() < 0.06:
                rate = rng.uniform(0.35, 0.55)  # occasional single weak subject
            attended = int(round(held * rate))
            conn.execute("INSERT INTO attendance VALUES (?,?,?,?,?,?)",
                         (att_id, sid, subj, held, attended, round(attended * 100.0 / held, 2)))
            att_id += 1
            for exam_type, exam_date in (("Internal", date(2026, 3, 10)), ("Final", date(2026, 5, 20))):
                base = rng.uniform(22, 48) if weak_profile else rng.uniform(45, 96)
                if not weak_profile and rng.random() < 0.04:
                    base = rng.uniform(25, 39)
                conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                             (mark_id, sid, subj, exam_type, round(base, 1), 100.0,
                              (exam_date + timedelta(days=rng.randint(0, 5))).isoformat()))
                mark_id += 1
    conn.commit()
    conn.close()


HOSPITAL_SCHEMA = """
CREATE TABLE departments (
    department_id INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    floor         INTEGER NOT NULL
);
CREATE TABLE doctors (
    doctor_id     INTEGER PRIMARY KEY,
    full_name     TEXT NOT NULL,
    specialization TEXT NOT NULL,
    department_id INTEGER NOT NULL REFERENCES departments(department_id),
    joined_on     DATE NOT NULL
);
CREATE TABLE patients (
    patient_id    INTEGER PRIMARY KEY,
    full_name     TEXT NOT NULL,
    gender        TEXT NOT NULL,
    date_of_birth DATE NOT NULL,
    city          TEXT NOT NULL,
    blood_group   TEXT NOT NULL
);
CREATE TABLE appointments (
    appointment_id   INTEGER PRIMARY KEY,
    patient_id       INTEGER NOT NULL REFERENCES patients(patient_id),
    doctor_id        INTEGER NOT NULL REFERENCES doctors(doctor_id),
    department_id    INTEGER NOT NULL REFERENCES departments(department_id),
    appointment_date DATE NOT NULL,
    appointment_time TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('Scheduled', 'Completed', 'Cancelled', 'No-show')),
    fee              REAL NOT NULL
);
"""

DEPARTMENTS = ["Cardiology", "Orthopedics", "Neurology", "Pediatrics", "Dermatology", "General Medicine"]


def generate_hospital(path: Path, today: date | None = None, seed: int = 7) -> None:
    rng = random.Random(seed)
    today = today or date.today()
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("PRAGMA foreign_keys = ON;" + HOSPITAL_SCHEMA)
    for i, d in enumerate(DEPARTMENTS, start=1):
        conn.execute("INSERT INTO departments VALUES (?,?,?)", (i, d, 1 + i // 2))
    doc_id = 1
    doctors_by_dept: dict[int, list[int]] = {}
    for dept in range(1, len(DEPARTMENTS) + 1):
        for _ in range(3):
            f, l = _name(rng)
            conn.execute("INSERT INTO doctors VALUES (?,?,?,?,?)",
                         (doc_id, f"Dr. {f} {l}", DEPARTMENTS[dept - 1], dept,
                          (date(2015, 1, 1) + timedelta(days=rng.randint(0, 3500))).isoformat()))
            doctors_by_dept.setdefault(dept, []).append(doc_id)
            doc_id += 1
    cities = ["Dehradun", "Haridwar", "Rishikesh", "Roorkee", "Haldwani", "Mussoorie"]
    for pid in range(1, 201):
        f, l = _name(rng)
        conn.execute("INSERT INTO patients VALUES (?,?,?,?,?,?)",
                     (pid, f"{f} {l}", rng.choice(["Male", "Female"]),
                      (date(1950, 1, 1) + timedelta(days=rng.randint(0, 25000))).isoformat(),
                      rng.choice(cities), rng.choice(["A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-"])))
    appt_id = 1
    for offset in range(-60, 31):
        day = today + timedelta(days=offset)
        n = rng.randint(4, 10) if offset != 1 else 12  # guarantee a busy "tomorrow"
        for _ in range(n):
            dept = rng.randint(1, len(DEPARTMENTS))
            if offset < 0:
                status = rng.choices(["Completed", "Cancelled", "No-show"], [0.8, 0.12, 0.08])[0]
            else:
                status = rng.choices(["Scheduled", "Cancelled"], [0.92, 0.08])[0]
            t = time(9 + rng.randint(0, 8), rng.choice([0, 15, 30, 45]))
            conn.execute("INSERT INTO appointments VALUES (?,?,?,?,?,?,?,?)",
                         (appt_id, rng.randint(1, 200), rng.choice(doctors_by_dept[dept]), dept,
                          day.isoformat(), t.strftime("%H:%M"), status, float(rng.choice([300, 500, 800, 1200]))))
            appt_id += 1
    conn.commit()
    conn.close()


def generate_all(target_dir: Path, today: date | None = None) -> dict[str, Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    edu, hosp = target_dir / "education.db", target_dir / "hospital.db"
    generate_education(edu)
    generate_hospital(hosp, today=today)
    return {"education": edu, "hospital": hosp}


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "data" / "samples"
    paths = generate_all(out)
    print({k: str(v) for k, v in paths.items()}, datetime.now().isoformat(timespec="seconds"))
