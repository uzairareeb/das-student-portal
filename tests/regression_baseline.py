"""Phase 0 regression baseline for the DAS portal (audit, 2026-11-23).

Runs the CURRENT application code against a throwaway local SQLite DB
(seeded demo data only) and exercises the key existing flows. Nothing
here touches production. Run from the repo root:

    /tmp/dasvenv3/bin/python regression_baseline.py
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
seed.main()  # creates + seeds the local DB (demo data only)
from app import app, get_db  # noqa: E402

app.app_context().push()
app.config["TESTING"] = True
PASS = []
PROBES = []


def check(name, cond):
    PASS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(f"baseline failed at: {name}")


def probe(name, cond):
    """Isolation/security probe: recorded as an audit finding, never halts."""
    PROBES.append((name, bool(cond)))
    print(("PROBE-OK   " if cond else "PROBE-FAIL ") + name)


db = get_db()
tables = {r[0] for r in db.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
expected = {"students", "deleted_students", "documents", "settings",
            "departments", "dept_uploads", "viva_records",
            "student_notifications", "admin_notifications",
            "dept_notifications", "activity_log", "saved_schedules"}
check("schema: all 12 tables created", expected <= tables)
n = db.fetchone("SELECT COUNT(*) c FROM students")["c"]
check("seed: 6 demo students", n == 6)

# ---- admin flow ----
c = app.test_client()
r = c.post("/admin", data={"password": "admin123"}, follow_redirects=False)
check("admin login redirects to dashboard", r.status_code in (301, 302))
check("admin dashboard 200", c.get("/admin/dashboard").status_code == 200)
check("admin student list 200", c.get("/admin/students").status_code == 200)
check("admin reports page 200", c.get("/admin/reports").status_code == 200)
check("admin schedule page 200", c.get("/admin/schedule").status_code == 200)
check("admin trash page 200", c.get("/admin/trash").status_code == 200)
check("admin archive page 200", c.get("/admin/archive").status_code == 200)
check("admin activity page 200", c.get("/admin/activity").status_code == 200)
sid = db.fetchone("SELECT id FROM students WHERE reg_no='24-DAS-0101'")["id"]
r = c.get(f"/admin/student/{sid}/json")
check("admin student JSON 200", r.status_code == 200 and
      r.get_json()["reg_no"] == "24-DAS-0101")
r = c.get("/admin/export")
check("admin Excel export 200", r.status_code == 200 and
      r.data[:2] == b"PK")

# ---- automation spot check: viva Pass -> auto Viva Done ----
db2 = get_db()
db2.execute("INSERT INTO viva_records (student_id, examiner_name, viva_date,"
            " result, created_at) VALUES (?, 'Dr Test', '2026-11-20',"
            " 'Pass', '2026-11-20 10:00')", (sid,))
db2.commit()
stu = c.get(f"/admin/student/{sid}/json").get_json()
form = {k: stu.get(k, "") for k in
        ("name", "reg_no", "cnic", "father_name", "gender", "degree",
         "session", "department", "folder_file", "comments_missing",
         "synopsis", "semi_final_thesis", "final_thesis")}
form["status"] = "Viva Pending"  # admin asks for Viva Pending...
c.post(f"/admin/student/{sid}/save", data=form)
row = db2.fetchone("SELECT status FROM students WHERE id=?", (sid,))
check("automation: viva Pass auto-advances status", row["status"] == "Viva Done")

# ---- student flow + isolation ----
cs = app.test_client()
r = cs.post("/student", data={"reg_no": "24-DAS-0101",
                              "password": "3520212346011"},
            follow_redirects=False)
if r.status_code not in (301, 302):
    # seed CNIC for Ayesha may differ; fetch it
    cnic = db.fetchone("SELECT cnic FROM students WHERE id=?",
                       (sid,))["cnic"]
    r = cs.post("/student", data={"reg_no": "24-DAS-0101", "password": cnic},
                follow_redirects=False)
check("student login works", r.status_code in (301, 302))
check("student dashboard 200", cs.get("/student/dashboard").status_code == 200)
r = cs.get("/student/profile-pdf")
check("student profile PDF 200 + real PDF",
      r.status_code == 200 and r.data[:4] == b"%PDF")

pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
r = cs.post("/student/upload",
            data={"doc_type": "Synopsis",
                  "file": (io.BytesIO(pdf), "synopsis.pdf")},
            content_type="multipart/form-data", follow_redirects=True)
check("student PDF upload accepted", r.status_code == 200)
doc = db.fetchone("SELECT id FROM documents WHERE student_id=?", (sid,))
check("document stored in DB", doc is not None)
other = db.fetchone("SELECT id FROM students WHERE id<>?", (sid,))["id"]
r = cs.get(f"/admin/doc/{doc['id']}/download")
probe("student cannot use admin doc route", r.status_code in (302, 404))
cs2 = app.test_client()
cnic2 = db.fetchone("SELECT cnic, reg_no FROM students WHERE id=?",
                    (other,))
cs2.post("/student", data={"reg_no": cnic2["reg_no"],
                           "password": cnic2["cnic"]})
r = cs2.get(f"/student/doc/{doc['id']}/download")
probe("student B cannot download student A's document", r.status_code == 404)

# ---- department flow + isolation ----
db.execute("INSERT INTO departments (name, email, password_hash, created_at)"
           " VALUES ('Department of Biochemistry', 'biochem@test.pk',"
           " 'x', '2026-01-01')")
db.execute("INSERT INTO departments (name, email, password_hash, created_at)"
           " VALUES ('Department of Mathematics', 'math@test.pk',"
           " 'x', '2026-01-01')")
db.commit()
from werkzeug.security import generate_password_hash  # noqa: E402
db.execute("UPDATE departments SET password_hash=? WHERE email LIKE 'biochem%'",
           (generate_password_hash("dept123"),))
db.commit()
cd = app.test_client()
r = cd.post("/dept", data={"email": "biochem@test.pk", "password": "dept123"},
            follow_redirects=False)
check("dept login works", r.status_code in (301, 302))
check("dept dashboard 200", cd.get("/dept/dashboard").status_code == 200)
check("dept my-students 200", cd.get("/dept/students").status_code == 200)
# Mathematics has no students: its profile access to a Biochemistry
# student must not succeed.
with cd.session_transaction() as sess:
    pass
bilal = db.fetchone("SELECT id, name FROM students WHERE reg_no='23-DAS-0098'")
r = cd.get(f"/dept/student/{bilal['id']}")
probe("dept isolation: Biochemistry dept cannot open a Zoology student",
      r.status_code in (302, 404) or bilal["name"].encode() not in r.data)

print(f"\nBASELINE OK — {len(PASS)}/{len(PASS)} flow checks passed")
bad = [n for n, ok in PROBES if not ok]
print(f"PROBES: {len(PROBES) - len(bad)}/{len(PROBES)} isolation probes OK")
for n in bad:
    print("  FINDING: " + n)
os.remove("students.db")
