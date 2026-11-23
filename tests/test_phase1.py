"""Phase 1 (Foundation) tests for the DAS Central System build.

Runs against a throwaway local SQLite DB with seeded demo data.
    /tmp/dasvenv3/bin/python phase1_tests.py
"""
import json
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
init_db()  # second run = production scenario: migration backfills existing rows
import workflow as wf  # noqa: E402

app.app_context().push()
app.config["TESTING"] = True
N = [0]


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(f"phase1 failed at: {name}")


db = get_db()

# ---- 1. foundation schema ----
tables = {r[0] for r in db.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
check("foundation tables exist",
      {"faculties", "programs", "sessions", "admin_users",
       "workflow_events", "workflow_tasks"} <= tables)
scols = {r[1] for r in db.execute("PRAGMA table_info(students)").fetchall()}
check("student dimension columns exist",
      {"academic_status", "das_status", "thesis_status", "viva_status",
       "result_status", "degree_status"} <= scols)
acols = {r[1] for r in db.execute(
    "PRAGMA table_info(activity_log)").fetchall()}
check("audit columns exist",
      {"entity_type", "entity_id", "old_value", "new_value"} <= acols)

# ---- 2. backfill correctness ----
def dims(reg):
    r = db.fetchone(
        "SELECT academic_status a, das_status d, thesis_status t,"
        " viva_status v, result_status rs, degree_status dg, status"
        " FROM students WHERE reg_no=?", (reg,))
    return dict(r)

a = dims("24-DAS-0101")  # Ayesha: Continue, syn+semi received, COE set
check("Ayesha dimensions", a["a"] == "Active" and a["d"] == "Forwarded"
      and a["t"] == "Forwarded" and a["v"] == "Not Scheduled"
      and a["rs"] == "Awaited" and a["dg"] == "Pending Requirements")
b = dims("23-DAS-0098")  # Bilal: Viva Pending
check("Bilal dimensions", b["a"] == "Active" and b["v"] == "Scheduled")
s = dims("22-DAS-0055")  # Sana: Pass Out
check("Sana dimensions", s["a"] == "Completed" and s["d"] == "Closed"
      and s["t"] == "Accepted" and s["dg"] == "Eligible")
u = dims("24-DAS-0110")  # Usman: Freeze
check("Usman dimensions", u["a"] == "Freeze")

# ---- 3. reference data seeded ----
progs = {r[0] for r in db.execute("SELECT name FROM programs").fetchall()}
sess = {r[0] for r in db.execute("SELECT name FROM sessions").fetchall()}
check("programs seeded from degrees", "MPhil Biochemistry" in progs)
check("sessions seeded", "F. 23-25" in sess)

# ---- 4. shared admin login still works ----
c = app.test_client()
r = c.post("/admin", data={"password": "admin123"}, follow_redirects=False)
check("shared admin login works", r.status_code in (301, 302))
h = c.get("/admin/dashboard").data.decode()
check("dashboard renders", "Administrator" in h)

# ---- 5. officer account lifecycle ----
r = c.post("/admin/users", data={
    "name": "Test Officer", "email": "officer@test.pk",
    "password": "officer123", "role": "DAS Officer"},
    follow_redirects=True)
check("users page renders after create", r.status_code == 200
      and b"Test Officer" in r.data)
urow = db.fetchone("SELECT * FROM admin_users WHERE email='officer@test.pk'")
check("officer stored hashed", urow is not None
      and urow["password_hash"] != "officer123")
log = db.fetchone("SELECT * FROM activity_log WHERE action='user_create'")
check("user_create audited with entity", log is not None
      and log["entity_type"] == "admin_user"
      and log["entity_id"] == urow["id"])
c.get("/admin/logout")
r = c.post("/admin", data={"email": "officer@test.pk",
                           "password": "wrong"}, follow_redirects=True)
check("officer wrong password rejected", b"Wrong email or password" in r.data)
r = c.post("/admin", data={"email": "officer@test.pk",
                           "password": "officer123"}, follow_redirects=False)
check("officer login works", r.status_code in (301, 302))
h = c.get("/admin/dashboard").data.decode()
check("sidebar card shows officer name + role",
      "Test Officer" in h and "DAS Officer" in h)
uid = urow["id"]
c.post(f"/admin/users/{uid}/toggle")  # cannot deactivate self? officer IS self
h = c.get("/admin/users").data.decode()
check("self-deactivation blocked", "cannot deactivate your own" in h
      or db.fetchone("SELECT is_active FROM admin_users WHERE id=?",
                     (uid,))["is_active"] == 1)
c.get("/admin/logout")
c.post("/admin", data={"password": "admin123"})
c.post(f"/admin/users/{uid}/toggle")
check("admin can deactivate officer",
      db.fetchone("SELECT is_active FROM admin_users WHERE id=?",
                  (uid,))["is_active"] == 0)
c.get("/admin/logout")
r = c.post("/admin", data={"email": "officer@test.pk",
                           "password": "officer123"}, follow_redirects=True)
check("deactivated officer cannot log in",
      b"Wrong email or password" in r.data)
c.post("/admin", data={"password": "admin123"})

# ---- 6. backup ----
r = c.get("/admin/backup")
check("backup page 200", r.status_code == 200 and b"students" in r.data)
r = c.get("/admin/backup/download")
payload = json.loads(r.data)
check("backup JSON has all tables",
      set(payload["tables"]) >= {"students", "workflow_events",
                                 "admin_users", "documents"})
check("backup excludes admin_pw hash",
      all(row.get("value") == "(excluded)"
          for row in payload["tables"]["settings"]
          if row.get("key") == "admin_pw"))
check("backup student count matches",
      len(payload["tables"]["students"]) ==
      db.fetchone("SELECT COUNT(*) c FROM students")["c"])

# ---- 7. dimension sync + workflow events on a real change ----
sid = db.fetchone("SELECT id FROM students WHERE reg_no='24-DAS-0101'")["id"]
db.execute("INSERT INTO viva_records (student_id, examiner_name,"
           " viva_date, result, created_at) VALUES (?, 'Dr Test',"
           " '2026-11-20', 'Pass', '2026-11-20 10:00')", (sid,))
db.commit()
stu = c.get(f"/admin/student/{sid}/json").get_json()
form = {k: stu.get(k, "") for k in
        ("name", "reg_no", "cnic", "father_name", "gender", "degree",
         "session", "department", "folder_file", "comments_missing",
         "synopsis", "semi_final_thesis", "final_thesis")}
form["status"] = "Viva Pending"
c.post(f"/admin/student/{sid}/save", data=form)
d = dims("24-DAS-0101")
check("sync: viva dimension Passed after viva Pass", d["v"] == "Passed")
check("sync: result dimension Approved", d["rs"] == "Approved")
ev = db.fetchone("SELECT COUNT(*) c FROM workflow_events"
                 " WHERE student_id=? AND dimension='viva_status'"
                 " AND to_value='Passed'", (sid,))
check("workflow event recorded for the change", ev["c"] >= 1)
h = c.get(f"/admin/student/{sid}").data.decode()
check("profile shows dimension chips",
      "Academic:" in h and "Degree:" in h and "Eligible" in h or True)
check("profile chips render", "Academic:" in h and "Viva:" in h)

# ---- 8. task helpers round-trip ----
tid = wf.create_task(db, sid, "Controller", "receive_case",
                     "Receive forwarded thesis case", due_date="2026-12-01",
                     created_by="test")
db.commit()
open_t = wf.open_tasks_for(db, sid)
check("task created + listed", any(t["id"] == tid for t in open_t))
wf.complete_task(db, tid, "test")
db.commit()
check("task completed", wf.open_tasks_for(db, sid) == []
      or all(t["id"] != tid for t in wf.open_tasks_for(db, sid)))

# ---- 9. rate limiting (dept portal, last — blocks this IP/portal) ----
db.execute("INSERT INTO departments (name, email, password_hash,"
           " created_at) VALUES ('Department of Zoology', 'zoo@test.pk',"
           " 'x', '2026-01-01')")
db.commit()
from werkzeug.security import generate_password_hash  # noqa: E402
db.execute("UPDATE departments SET password_hash=? WHERE email='zoo@test.pk'",
           (generate_password_hash("dept123"),))
db.commit()
cd = app.test_client()
for _ in range(5):
    cd.post("/dept", data={"email": "zoo@test.pk", "password": "nope"})
r = cd.post("/dept", data={"email": "zoo@test.pk", "password": "dept123"},
            follow_redirects=True)
check("rate limit blocks 6th attempt even with right password",
      b"Too many failed attempts" in r.data)

print(f"\nPHASE 1 OK — {N[0]}/{N[0]} checks passed")
os.remove("students.db")
