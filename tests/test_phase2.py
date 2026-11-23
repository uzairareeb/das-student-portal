"""Phase 2 (Department Intake) tests for the DAS Central System build.

Runs against a throwaway local SQLite DB with seeded demo data.
    /tmp/dasvenv3/bin/python phase2_tests.py
"""
import io
import os
import sys

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, REPO)
os.chdir(REPO)
if os.path.exists("students.db"):
    os.remove("students.db")

import seed  # noqa: E402
seed.main()
from app import app, get_db, init_db  # noqa: E402
init_db()
from openpyxl import Workbook  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

app.app_context().push()
app.config["TESTING"] = True
N = [0]
db = get_db()

HEADERS = ["CNIC", "Name", "Father Name", "Registration No.", "Gender",
           "Degree", "Session", "Folder/File (With GS-Forms)", "Synopsis",
           "Semi-Final Thesis", "Final Thesis", "Thesis Forward (COE)",
           "Status", "Comments (Missing)"]


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(f"phase2 failed at: {name}")


def wb_bytes(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def row(cnic, name, father, reg, degree="MPhil Biochemistry",
        session="F. 23-25", gender="Female"):
    return [cnic, name, father, reg, gender, degree, session,
            "Not Completed", "Not Received", "Not Received",
            "Not Received", "", "Continue", "N/A"]


def students_count():
    return db.fetchone("SELECT COUNT(*) c FROM students")["c"]


def latest_upload():
    return dict(db.fetchone("SELECT * FROM dept_uploads ORDER BY id DESC"))


# ---- setup: department + logins ----
db.execute("INSERT INTO departments (name, email, password_hash,"
           " created_at) VALUES ('Department of Biochemistry',"
           " 'biochem@test.pk', ?, '2026-01-01')",
           (generate_password_hash("dept123"),))
db.execute("INSERT INTO departments (name, email, password_hash,"
           " created_at) VALUES ('Department of Mathematics',"
           " 'math@test.pk', ?, '2026-01-01')",
           (generate_password_hash("math123"),))
db.commit()

admin = app.test_client()
admin.post("/admin", data={"password": "admin123"})
dept = app.test_client()
dept.post("/dept", data={"email": "biochem@test.pk", "password": "dept123"})


def upload(client, data, name="batch.xlsx"):
    return client.post("/dept/upload",
                       data={"file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data",
                       follow_redirects=False)


# ---- 1. clean batch ----
before = students_count()
r = upload(dept, wb_bytes([
    row("3520211111111", "New One", "Father One", "24-DAS-9001"),
    row("3520222222222", "New Two", "Father Two", "24-DAS-9002"),
]))
u = latest_upload()
check("clean upload redirects to batch detail",
      r.status_code in (301, 302)
      and f"/dept/uploads/{u['id']}" in r.headers["Location"])
check("batch counts stored", u["valid_count"] == 2
      and u["error_count"] == 0 and u["dup_count"] == 0
      and u["template_version"] == "DAS-INTAKE v1.0")
rows = db.execute("SELECT * FROM intake_rows WHERE upload_id=?",
                  (u["id"],)).fetchall()
check("intake rows created, default import",
      len(rows) == 2 and all(x["decision"] == "import" for x in rows))
h = dept.get(f"/dept/uploads/{u['id']}").data.decode()
check("dept detail shows rows + valid", "New One" in h and "Valid" in h)
h = admin.get(f"/admin/uploads/{u['id']}/review").data.decode()
check("admin review page renders", "New Two" in h and "Approve" in h)
r = admin.post(f"/admin/uploads/{u['id']}/approve")
check("approve imports both students", students_count() == before + 2)
u = latest_upload()
check("batch locked as approved", u["status"] == "approved"
      and u["imported_count"] == 2 and u["reviewed_by"])
admin.post(f"/admin/uploads/{u['id']}/approve")
check("second approve does not duplicate", students_count() == before + 2)

# ---- 2. error batch ----
before = students_count()
upload(dept, wb_bytes([
    row("", "No Cnic", "Father X", "24-DAS-9010"),
    row("12345", "Short Cnic", "Father Y", "24-DAS-9011"),
    row("3520233333333", "Good Row", "Father Z", "24-DAS-9012"),
]))
u = latest_upload()
check("error counts stored", u["error_count"] == 2
      and u["valid_count"] == 1)
rows = db.execute("SELECT * FROM intake_rows WHERE upload_id=?"
                  " ORDER BY row_no", (u["id"],)).fetchall()
check("error rows forced to skip",
      rows[0]["decision"] == "skip" and rows[1]["decision"] == "skip")
import json as _j
errs = _j.loads(rows[0]["errors_json"])
check("missing CNIC error text", any("CNIC is missing" in e for e in errs))
errs = _j.loads(rows[1]["errors_json"])
check("short CNIC error text", any("13 digits" in e for e in errs))
r = admin.get(f"/admin/uploads/{u['id']}/errors.xlsx")
check("error report downloads", r.status_code == 200
      and r.data[:2] == b"PK")
admin.post(f"/admin/uploads/{u['id']}/approve")
check("approve imports only the valid row",
      students_count() == before + 1)

# ---- 3. duplicate batch: decision gate, link + import ----
before = students_count()
ayesha = db.fetchone("SELECT id FROM students WHERE reg_no='24-DAS-0101'")
upload(dept, wb_bytes([
    row("3520212345671", "Ayesha Raza", "Muhammad Raza", "24-DAS-7777"),
    row("3520299999999", "Ayesha Raza", "Muhammad Raza", "24-DAS-7778"),
]))
u = latest_upload()
check("dup counts stored", u["dup_count"] == 2)
r = admin.post(f"/admin/uploads/{u['id']}/approve", follow_redirects=True)
check("approve blocked while decisions pending",
      students_count() == before and b"need your decision" in r.data)
rows = db.execute("SELECT * FROM intake_rows WHERE upload_id=?"
                  " ORDER BY row_no", (u["id"],)).fetchall()
admin.post(f"/admin/uploads/{u['id']}/decisions",
           data={f"decision_{rows[0]['id']}": "link",
                 f"decision_{rows[1]['id']}": "import"})
admin.post(f"/admin/uploads/{u['id']}/approve")
check("link creates no student, import creates one",
      students_count() == before + 1)
r0 = db.fetchone("SELECT * FROM intake_rows WHERE id=?", (rows[0]["id"],))
check("linked row points at the existing student",
      r0["student_id"] == ayesha["id"])

# ---- 4. in-file duplicate: warned, DAS can skip one ----
before = students_count()
upload(dept, wb_bytes([
    row("3520244444444", "Twin One", "Father T", "24-DAS-9020"),
    row("3520255555555", "Twin Two", "Father T", "24-DAS-9020"),
]))
u = latest_upload()
rows = db.execute("SELECT * FROM intake_rows WHERE upload_id=?",
                  (u["id"],)).fetchall()
warn = _j.loads(rows[0]["warnings_json"])
check("in-file duplicate warned",
      any("Same Registration No." in w for w in warn))
admin.post(f"/admin/uploads/{u['id']}/decisions",
           data={f"decision_{rows[1]['id']}": "skip"})
admin.post(f"/admin/uploads/{u['id']}/approve")
check("DAS skip keeps only one of the twins",
      students_count() == before + 1)

# ---- 5. return flow ----
upload(dept, wb_bytes([row("3520266666666", "Return Me", "F R",
                            "24-DAS-9030")]))
u = latest_upload()
admin.post(f"/admin/uploads/{u['id']}/reject",
           data={"comment": "Session column is wrong — fix and resend."})
u = latest_upload()
check("batch returned with comment", u["status"] == "returned"
      and "Session column" in (u["admin_comment"] or ""))
h = dept.get(f"/dept/uploads/{u['id']}").data.decode()
check("dept sees the return comment", "Session column is wrong" in h)
note = db.fetchone("SELECT * FROM dept_notifications"
                   " WHERE title LIKE 'Upload returned%'")
check("dept notified of return", note is not None)

# ---- 6. dept isolation ----
dept2 = app.test_client()
dept2.post("/dept", data={"email": "math@test.pk", "password": "math123"})
r = dept2.get(f"/dept/uploads/{u['id']}", follow_redirects=False)
check("other dept cannot open the batch detail",
      r.status_code in (301, 302))
r = dept2.get(f"/dept/uploads/{u['id']}/errors.xlsx",
              follow_redirects=False)
check("other dept cannot download the error report",
      r.status_code in (301, 302))

# ---- 7. corrupt + structurally wrong files create no batch ----
n_uploads = db.fetchone("SELECT COUNT(*) c FROM dept_uploads")["c"]
r = upload(dept, b"this is not a workbook", "bad.xlsx")
check("corrupt file rejected, no batch",
      db.fetchone("SELECT COUNT(*) c FROM dept_uploads")["c"] == n_uploads)
wb = Workbook(); ws = wb.active
ws.append(["Name", "Father Name"]); ws.append(["X", "Y"])
buf = io.BytesIO(); wb.save(buf)
upload(dept, buf.getvalue(), "nocols.xlsx")
check("missing Reg No column rejected, no batch",
      db.fetchone("SELECT COUNT(*) c FROM dept_uploads")["c"] == n_uploads)

# ---- 8. legacy batch (no intake rows) parses on approve ----
raw = wb_bytes([row("3520277777777", "Legacy One", "F L", "24-DAS-9040")])
db.execute("INSERT INTO dept_uploads (department_id, file_name, file_data,"
           " row_count, status, created_at) VALUES (1, 'legacy.xlsx', ?,"
           " 1, 'pending', '2026-11-01 09:00')", (raw,))
db.commit()
u = latest_upload()
before = students_count()
r = admin.post(f"/admin/uploads/{u['id']}/approve", follow_redirects=False)
check("legacy approve redirects to review first",
      r.status_code in (301, 302)
      and f"/review" in r.headers["Location"]
      and students_count() == before)
n = db.fetchone("SELECT COUNT(*) c FROM intake_rows WHERE upload_id=?",
                (u["id"],))
check("legacy batch parsed into intake rows", n["c"] == 1)
admin.post(f"/admin/uploads/{u['id']}/approve")
check("legacy batch imports after review", students_count() == before + 1)

# ---- 9. admin direct import still works (parse refactor regression) ----
before = students_count()
data = wb_bytes([row("3520288888888", "Direct One", "F D", "24-DAS-9050")])
admin.post("/admin/import/upload",
           data={"file": (io.BytesIO(data), "direct.xlsx")},
           content_type="multipart/form-data")
check("admin import adds the student", students_count() == before + 1)
s = db.fetchone("SELECT * FROM students WHERE reg_no='24-DAS-9050'")
check("admin-imported student has dimensions",
      s is not None and s["academic_status"] == "Active")

print(f"\nPHASE 2 OK — {N[0]}/{N[0]} checks passed")
os.remove("students.db")
