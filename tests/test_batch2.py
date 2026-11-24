"""Batch 2 (Admin Advanced): duplicate finder + add warning, Data
Safai, session master dropdowns + sessions page, admin import via the
intake review flow, recycle-bin purge warning + export.

    /tmp/dasvenv3/bin/python batch2_tests.py
"""
import io
import os
import sys
from datetime import datetime, timedelta

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, REPO)
os.chdir(REPO)
if os.path.exists("students.db"):
    os.remove("students.db")

import seed  # noqa: E402
seed.main()
from app import app, get_db, init_db, IMPORT_DEPT_EMAIL, dept_names  # noqa: E402
init_db()
app.app_context().push()
app.config["TESTING"] = True
N = [0]
db = get_db()

from openpyxl import Workbook  # noqa: E402

HEADERS = ["CNIC", "Name", "Father Name", "Registration No.", "Gender",
           "Degree", "Session", "Folder/File (With GS-Forms)", "Synopsis",
           "Semi-Final Thesis", "Final Thesis", "Thesis Forward (COE)",
           "Status", "Comments (Missing)"]


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(f"batch2 failed at: {name}")


def count():
    return db.fetchone("SELECT COUNT(*) AS n FROM students")["n"]


def wb_bytes(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def xrow(cnic, name, father, reg, degree="MPhil Biochemistry",
         session="2025", gender="Female"):
    return [cnic, name, father, reg, gender, degree, session,
            "Not Completed", "Not Received", "Not Received",
            "Not Received", "", "Continue", "N/A"]


admin = app.test_client()
admin.post("/admin", data={"password": "admin123"})

# ---- pseudo import department ----
row = db.fetchone("SELECT * FROM departments WHERE email=?",
                  (IMPORT_DEPT_EMAIL,))
check("DAS Office pseudo-department exists", row is not None)
check("pseudo-department hidden from form department lists",
      "DAS Office (Admin Import)" not in dept_names())

# ---- duplicate warning on Add Student ----
before = count()
r = admin.post("/admin/student/new", data={
    "name": "Ayesha Clone", "reg_no": "25-DUP-001",
    "cnic": "3520212345671", "father_name": "Muhammad Raza",
    "gender": "Female", "degree": "MPhil Biochemistry",
    "session": "2025", "status": "Continue"})
h = r.data.decode()
check("duplicate CNIC blocks the add with a warning",
      "Possible duplicate" in h and count() == before)
r = admin.post("/admin/student/new", data={
    "name": "Ayesha Clone", "reg_no": "25-DUP-001",
    "cnic": "3520212345671", "father_name": "Muhammad Raza",
    "gender": "Female", "degree": "MPhil Biochemistry",
    "session": "2025", "status": "Continue", "force": "1"})
check("force add succeeds", count() == before + 1)

# ---- Duplicate Finder page ----
h = admin.get("/admin/duplicates").data.decode()
check("duplicates page lists the shared CNIC",
      "Same CNIC" in h and "3520212345671" in h)

# ---- Data Safai ----
db.execute(
    "INSERT INTO students (cnic, name, father_name, reg_no, degree,"
    " session, department, status, created_at, updated_at) VALUES"
    " ('', 'Safai Case', '', '25-SAF-001', '', '', '', 'Continue',"
    " '2026-11-24 10:00', '2026-11-24 10:00')")
db.commit()
h = admin.get("/admin/data-quality").data.decode()
check("data safai lists the incomplete record",
      "Safai Case" in h and "Missing father name" in h
      and "Missing CNIC" in h)

# ---- sessions: close retires the dropdown option ----
sess = db.fetchone("SELECT * FROM sessions ORDER BY name LIMIT 1")
h = admin.get("/admin/sessions").data.decode()
check("sessions page lists sessions with counts", sess["name"] in h)
admin.post(f"/admin/sessions/{sess['id']}/toggle")
row = db.fetchone("SELECT status FROM sessions WHERE id=?",
                  (sess["id"],))
check("session closed", row["status"] == "Closed")
h = admin.get("/admin/student/new").data.decode()
check("closed session leaves the Add form dropdown",
      f'value="{sess["name"]}"' not in h)
admin.post(f"/admin/sessions/{sess['id']}/toggle")
h = admin.get("/admin/student/new").data.decode()
check("reopened session returns to the dropdown",
      f'value="{sess["name"]}"' in h)

# ---- admin import travels the review flow ----
before = count()
data = wb_bytes([
    xrow("3520277777777", "Import New", "Father I", "25-IMP-001"),
    xrow("3520212345671", "Ayesha Again", "Muhammad Raza", "25-IMP-002"),
])
r = admin.post("/admin/import/upload",
               data={"file": (io.BytesIO(data), "imp.xlsx")},
               content_type="multipart/form-data")
check("import redirects to the review page",
      r.status_code == 302 and "/review" in r.headers["Location"])
check("nothing imported before approval", count() == before)
uid = int(r.headers["Location"].rstrip("/").split("/")[-2])
up = db.fetchone("SELECT * FROM dept_uploads WHERE id=?", (uid,))
dept = db.fetchone("SELECT email FROM departments WHERE id=?",
                   (up["department_id"],))
check("batch owned by the DAS Office pseudo-department",
      dept["email"] == IMPORT_DEPT_EMAIL)
r = admin.post(f"/admin/uploads/{uid}/approve", follow_redirects=True)
check("approve blocked while the duplicate decision is pending",
      count() == before)
rows = db.execute("SELECT * FROM intake_rows WHERE upload_id=?"
                  " ORDER BY row_no", (uid,)).fetchall()
admin.post(f"/admin/uploads/{uid}/decisions",
           data={f"decision_{rows[0]['id']}": "import",
                 f"decision_{rows[1]['id']}": "skip"})
admin.post(f"/admin/uploads/{uid}/approve")
check("approve imports only the clean row", count() == before + 1)
check("imported student present",
      db.fetchone("SELECT * FROM students WHERE reg_no='25-IMP-001'")
      is not None)

# ---- recycle bin: purge warning + export ----
old = (datetime.now() - timedelta(days=24)).strftime("%Y-%m-%d %H:%M")
db.execute(
    "INSERT INTO deleted_students (orig_id, name, reg_no, cnic,"
    " degree, department, status, data_json, deleted_at, deleted_by)"
    " VALUES (999, 'Old Deleted', '20-OLD-001', '3520200000000',"
    " 'MPhil Biochemistry', 'Department of Biochemistry', 'Continue',"
    " '{}', ?, 'admin')", (old,))
db.commit()
h = admin.get("/admin/trash").data.decode()
check("purge warning shows for the expiring record",
      "permanently deleted within 7 days" in h)
r = admin.get("/admin/trash/export.xlsx")
check("trash export downloads an xlsx",
      r.status_code == 200 and r.data[:2] == b"PK")

print(f"\nBATCH 2 OK — {N[0]}/{N[0]} checks passed")
os.remove("students.db")
