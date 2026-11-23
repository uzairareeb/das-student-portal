"""Phase 5 (Thesis Case + Controller Portal) tests for the DAS system.

Runs against a throwaway local SQLite DB with seeded demo data.
    /tmp/dasvenv3/bin/python tests/test_phase5.py
"""
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
app.app_context().push()
app.config["TESTING"] = True
N = [0]
db = get_db()
SECRET_EXAMINER = "Dr. External Secretname"


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(f"phase5 failed at: {name}")


a = db.fetchone("SELECT * FROM students WHERE reg_no='24-DAS-0101'")
b = db.fetchone("SELECT * FROM students WHERE reg_no='23-DAS-0098'")
admin = app.test_client()
admin.post("/admin", data={"password": "admin123"})

# ---- controller account ----
admin.post("/admin/controller-users",
           data={"name": "Muhammad Rafiq", "email": "coe@cuvas.edu.pk",
                 "password": "ctrl1234", "designation": "Deputy Controller"})
cu = db.fetchone("SELECT * FROM controller_users"
                 " WHERE email='coe@cuvas.edu.pk'")
check("controller account created", cu is not None)
admin.post("/admin/controller-users",
           data={"name": "Dup", "email": "coe@cuvas.edu.pk",
                 "password": "ctrl1234"})
check("duplicate controller email rejected",
      db.fetchone("SELECT COUNT(*) c FROM controller_users")["c"] == 1)

# ---- DAS opens the case, submits, adds examiner, forwards ----
admin.post(f"/admin/student/{a['id']}/thesis/open")
case = db.fetchone("SELECT * FROM thesis_cases WHERE student_id=?",
                   (a["id"],))
check("case opened", case and case["case_status"] == "Not Started")
admin.post(f"/admin/student/{a['id']}/thesis/das",
           data={"action": "submitted", "date": "2026-11-01"})
admin.post(f"/admin/student/{a['id']}/thesis/examiner",
           data={"examiner_name": SECRET_EXAMINER,
                 "affiliation": "University of the Punjab",
                 "examiner_role": "External",
                 "appointed_date": "2026-11-05"})
ex = db.fetchone("SELECT * FROM thesis_examiners WHERE student_id=?",
                 (a["id"],))
check("examiner recorded as Awaited", ex and ex["report_status"] == "Awaited")
admin.post(f"/admin/student/{a['id']}/thesis/das",
           data={"action": "forward", "date": "2026-11-03",
                 "dispatch_no": "DSP-9"})
case = db.fetchone("SELECT * FROM thesis_cases WHERE student_id=?",
                   (a["id"],))
check("forwarded with dispatch no.",
      case["case_status"] == "Forwarded to Controller"
      and case["dispatch_no"] == "DSP-9")
check("forwarding did NOT mark it received",
      not case["received_date"])
note = db.fetchone("SELECT * FROM controller_notifications"
                   " WHERE user_id=?", (cu["id"],))
check("controller notified of forwarding", note is not None)

# ---- controller portal ----
ctl = app.test_client()
r = ctl.post("/controller", data={"email": "coe@cuvas.edu.pk",
                                  "password": "nope"})
check("controller wrong password rejected",
      b"Invalid email or password" in r.data)
ctl.post("/controller", data={"email": "coe@cuvas.edu.pk",
                              "password": "ctrl1234"})
h = ctl.get("/controller/dashboard").data.decode()
check("controller dashboard shows awaiting receipt",
      "Awaiting Your Receipt" in h and "Ayesha Raza" in h)
r = ctl.get(f"/controller/case/{b['id']}")
check("case page 404 when no case exists", r.status_code == 404)
h = ctl.get(f"/controller/case/{a['id']}").data.decode()
check("controller sees the examiner (confidential side)",
      SECRET_EXAMINER in h and "Record receipt" in h)

# return without objections is blocked
ctl.post(f"/controller/case/{a['id']}/action",
         data={"action": "return", "objections": ""})
case = db.fetchone("SELECT * FROM thesis_cases WHERE student_id=?",
                   (a["id"],))
check("return without objections blocked",
      case["case_status"] == "Forwarded to Controller")
ctl.post(f"/controller/case/{a['id']}/action",
         data={"action": "return",
               "objections": "Title page format is wrong"})
case = db.fetchone("SELECT * FROM thesis_cases WHERE student_id=?",
                   (a["id"],))
check("returned with objections",
      case["case_status"] == "Returned with Objections"
      and "Title page" in case["objections"])

# ---- student view: status + objections visible, examiner NEVER ----
stu = app.test_client()
stu.post("/student", data={"reg_no": "24-DAS-0101",
                           "password": "3520212345671"})
h = stu.get("/student/dashboard").data.decode()
check("student sees case status + objections",
      "Returned with Objections" in h and "Title page format" in h)
check("examiner name NEVER leaks to the student",
      SECRET_EXAMINER not in h)
h = stu.get("/student/upload").data.decode()
check("examiner name not on documents page either",
      SECRET_EXAMINER not in h)

# ---- corrections loop ----
admin.post(f"/admin/student/{a['id']}/thesis/das",
           data={"action": "resubmit", "date": "2026-11-12"})
case = db.fetchone("SELECT * FROM thesis_cases WHERE student_id=?",
                   (a["id"],))
check("corrections submitted recorded",
      case["case_status"] == "Corrections Submitted")
ctl.post(f"/controller/case/{a['id']}/action",
         data={"action": "evaluation_start"})
ctl.post(f"/controller/examiner/{ex['id']}/report",
         data={"report_status": "Received", "report_date": "2026-11-20"})
ex = db.fetchone("SELECT * FROM thesis_examiners WHERE id=?",
                 (ex["id"],))
check("examiner report received via controller",
      ex["report_status"] == "Received")
ctl.post(f"/controller/case/{a['id']}/action",
         data={"action": "evaluation_complete"})
case = db.fetchone("SELECT * FROM thesis_cases WHERE student_id=?",
                   (a["id"],))
check("evaluation complete",
      case["case_status"] == "Evaluation Complete"
      and case["evaluation_completed_date"])
ctl.post(f"/controller/case/{a['id']}/action",
         data={"action": "result", "result_status": "Declared — Pass",
               "date": "2026-11-25", "result_remarks": "Notif 88/2026"})
case = db.fetchone("SELECT * FROM thesis_cases WHERE student_id=?",
                   (a["id"],))
check("result declared", case["case_status"] == "Result Declared"
      and case["result_status"] == "Declared — Pass")
note = db.fetchone("SELECT * FROM student_notifications"
                   " WHERE student_id=? AND title LIKE 'Thesis result%'",
                   (a["id"],))
check("student notified of result", note is not None)
ctl.post(f"/controller/case/{a['id']}/action", data={"action": "close"})
case = db.fetchone("SELECT * FROM thesis_cases WHERE student_id=?",
                   (a["id"],))
check("case closed", case["case_status"] == "Closed")

# ---- receive path on a second case (happy path) ----
admin.post(f"/admin/student/{b['id']}/thesis/open")
admin.post(f"/admin/student/{b['id']}/thesis/das",
           data={"action": "forward", "date": "2026-11-04",
                 "dispatch_no": "DSP-10"})
ctl.post(f"/controller/case/{b['id']}/action",
         data={"action": "receive", "date": "2026-11-06"})
caseb = db.fetchone("SELECT * FROM thesis_cases WHERE student_id=?",
                    (b["id"],))
check("controller receipt recorded by controller himself",
      caseb["case_status"] == "Received by Controller"
      and caseb["received_by"] == "Muhammad Rafiq"
      and caseb["received_date"] == "2026-11-06")

# ---- admin oversight + timeline ----
h = admin.get("/admin/thesis-cases").data.decode()
check("admin queue lists cases with counts",
      "Thesis Cases" in h and "Closed" in h)
h = admin.get(f"/admin/student/{a['id']}/timeline").data.decode()
check("timeline carries case events",
      "received by Controller" in h or "forwarded to Controller" in h)
h = admin.get(f"/admin/student/{a['id']}").data.decode()
check("tab bar: Thesis Case is a link now",
      f"/admin/student/{a['id']}/thesis" in h
      and "Thesis Case · Phase 5" not in h)

# ---- inactive controller cannot log in ----
admin.post(f"/admin/controller-users/{cu['id']}/save",
           data={"name": "Muhammad Rafiq",
                 "designation": "Deputy Controller"})
ctl2 = app.test_client()
r = ctl2.post("/controller", data={"email": "coe@cuvas.edu.pk",
                                   "password": "ctrl1234"})
check("inactive controller cannot log in",
      b"Invalid email or password" in r.data)

print(f"\nPHASE 5 OK — {N[0]}/{N[0]} checks passed")
os.remove("students.db")
