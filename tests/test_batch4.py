"""Batch 4 (Admin Advanced): Reports card + custom export (#13),
dashboard deadlines + trends (#10/#16), backup light option (#7).

    /tmp/dasvenv3/bin/python batch4_tests.py
"""
import json
import os
import sys
from datetime import date, timedelta

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, REPO)
os.chdir(REPO)
if os.path.exists("students.db"):
    os.remove("students.db")

import seed  # noqa: E402
seed.main()
from app import app, count_students, get_db, init_db  # noqa: E402
from academic_schedule import build_schedule, serialize_rows  # noqa: E402
init_db()
app.app_context().push()
app.config["TESTING"] = True
N = [0]
db = get_db()


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(f"batch4 failed at: {name}")


admin = app.test_client()
admin.post("/admin", data={"password": "admin123"})
bilal = db.fetchone("SELECT * FROM students WHERE reg_no='23-DAS-0098'")

# ---- #13a: Viva Passed — Status Not Updated ----
check("no stale viva-pass students initially",
      count_students({"viva_passed_stale": "1"}) == 0)
db.execute(
    "INSERT INTO viva_records (student_id, examiner_name, viva_date,"
    " result, created_at) VALUES (?, 'Dr T', ?, 'Pass', ?)",
    (bilal["id"], date.today().isoformat(),
     date.today().isoformat()))
db.commit()
check("a Pass viva with a stale status is counted",
      count_students({"viva_passed_stale": "1"}) == 1)
h = admin.get("/admin/reports").data.decode()
check("Reports page carries the new card",
      "Viva Passed — Status Not Updated" in h
      and "Custom Export Builder" in h)
r = admin.get("/admin/export?viva_passed_stale=1")
check("the card's Excel export downloads",
      r.status_code == 200 and r.data[:2] == b"PK")

# ---- #13b: custom export builder ----
h = admin.get("/admin/reports/custom").data.decode()
check("builder page renders with column choices",
      "Custom Export Builder" in h and 'name="col_cnic"' in h)
r = admin.post("/admin/reports/custom", data={
    "col_name": "1", "col_reg_no": "1", "col_status": "1",
    "format": "excel"})
check("custom Excel export downloads",
      r.status_code == 200 and r.data[:2] == b"PK")
r = admin.post("/admin/reports/custom", data={
    "col_name": "1", "status": "Viva Pending", "format": "pdf"})
check("custom PDF export downloads",
      r.status_code == 200 and r.data[:4] == b"%PDF")

# ---- #10/#16: dashboard deadlines + trends ----
today = date.today()
comm = today - timedelta(days=4)
term = today + timedelta(days=120)
rows = build_schedule(comm, term)
db.execute(
    "INSERT INTO saved_schedules (session, commencement, termination,"
    " rows_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
    ("Batch4 Session", comm.isoformat(), term.isoformat(),
     json.dumps(serialize_rows(rows)), "2026-01-01", "2026-01-01"))
db.commit()
h = admin.get("/admin/dashboard").data.decode()
check("dashboard shows the deadlines card with this week's item",
      "Deadlines This Week" in h and "Batch4 Session" in h)
check("dashboard shows the trends charts",
      "Trends — Last 6 Months" in h and "chTrend" in h
      and "chVivaTrend" in h)

# ---- #7: backup storage stats + light download ----
h = admin.get("/admin/backup").data.decode()
check("backup page shows the document storage line",
      "Document storage inside the database" in h
      and "Download without document files" in h)
r = admin.get("/admin/backup/download?docs=0")
payload = json.loads(r.data)
docs = payload["tables"]["documents"]
check("light backup excludes the file bytes but keeps the record",
      len(docs) == 1
      and docs[0]["file_data"] == "(excluded in light backup)")
r = admin.get("/admin/backup/download")
payload = json.loads(r.data)
check("full backup still carries the file bytes",
      isinstance(payload["tables"]["documents"][0]["file_data"], dict)
      and "__b64__" in payload["tables"]["documents"][0]["file_data"])

print(f"\nBATCH 4 OK — {N[0]}/{N[0]} checks passed")
os.remove("students.db")
