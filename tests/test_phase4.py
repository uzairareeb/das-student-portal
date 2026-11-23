"""Phase 4 (Academic + Supervisors + ASRB) tests for the DAS system.

Runs against a throwaway local SQLite DB with seeded demo data.
    /tmp/dasvenv3/bin/python phase4_tests.py
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


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(f"phase4 failed at: {name}")


a = db.fetchone("SELECT * FROM students WHERE reg_no='24-DAS-0101'")
b = db.fetchone("SELECT * FROM students WHERE reg_no='23-DAS-0098'")
admin = app.test_client()
admin.post("/admin", data={"password": "admin123"})

# ---- supervisor accounts ----
admin.post("/admin/supervisors",
           data={"name": "Dr. Sana Malik", "email": "sana@cuvas.edu.pk",
                 "password": "super123", "designation": "Associate Professor",
                 "department": "Biochemistry"})
sp1 = db.fetchone("SELECT * FROM supervisors WHERE email='sana@cuvas.edu.pk'")
check("supervisor account created", sp1 is not None)
admin.post("/admin/supervisors",
           data={"name": "Dr. Dup", "email": "sana@cuvas.edu.pk",
                 "password": "super123"})
n = db.fetchone("SELECT COUNT(*) c FROM supervisors")["c"]
check("duplicate supervisor email rejected", n == 1)
admin.post("/admin/supervisors",
           data={"name": "Dr. Imran Ali", "email": "imran@cuvas.edu.pk",
                 "password": "super456", "designation": "Assistant Professor",
                 "department": "Biochemistry"})
sp2 = db.fetchone("SELECT * FROM supervisors"
                  " WHERE email='imran@cuvas.edu.pk'")
h = admin.get("/admin/supervisors").data.decode()
check("supervisors page renders", "Dr. Sana Malik" in h)

# ---- academic records ----
admin.post(f"/admin/student/{a['id']}/semester",
           data={"semester_no": "1", "term": "Fall 2024",
                 "status": "Completed", "gpa": "3.50", "cgpa": "3.50"})
admin.post(f"/admin/student/{a['id']}/semester",
           data={"semester_no": "2", "term": "Spring 2025",
                 "status": "Enrolled"})
n = db.fetchone("SELECT COUNT(*) c FROM academic_records"
                " WHERE student_id=?", (a["id"],))["c"]
check("semester records stored", n == 2)
admin.post(f"/admin/student/{a['id']}/comprehensive",
           data={"exam_date": "2026-06-15", "result": "Pass"})
c = db.fetchone("SELECT * FROM comprehensive_exams WHERE student_id=?",
                (a["id"],))
check("comprehensive exam stored", c and c["result"] == "Pass")
h = admin.get(f"/admin/student/{a['id']}/academic").data.decode()
check("academic tab renders records + Not configured note",
      "Fall 2024" in h and "Not configured" in h
      and "Comprehensive Exam" in h)

# ---- synopsis + assignment ----
admin.post(f"/admin/student/{a['id']}/synopsis",
           data={"title": "Enzyme Kinetics in Cholistan Livestock",
                 "submitted_date": "2026-09-01",
                 "asrb_status": "Not Submitted"})
syn = db.fetchone("SELECT * FROM synopsis_records WHERE student_id=?",
                  (a["id"],))
check("synopsis record created", syn and "Enzyme Kinetics" in syn["title"])
admin.post(f"/admin/student/{a['id']}/assign",
           data={"supervisor_id": str(sp1["id"]), "role": "Supervisor",
                 "assigned_date": "2026-09-05"})
asg = db.fetchone("SELECT * FROM supervisor_assignments"
                  " WHERE student_id=? AND is_current=1", (a["id"],))
check("supervisor assigned", asg and asg["supervisor_id"] == sp1["id"])
note = db.fetchone("SELECT * FROM supervisor_notifications"
                   " WHERE supervisor_id=?", (sp1["id"],))
check("supervisor notified of assignment", note is not None)

# ---- supervisor portal: login, isolation, endorse, progress ----
sup = app.test_client()
r = sup.post("/supervisor", data={"email": "sana@cuvas.edu.pk",
                                  "password": "wrong-pass"})
check("wrong password rejected", b"Invalid email or password" in r.data)
sup.post("/supervisor", data={"email": "sana@cuvas.edu.pk",
                              "password": "super123"})
h = sup.get("/supervisor/dashboard").data.decode()
check("supervisor dashboard shows the student", "Ayesha Raza" in h)
h = sup.get(f"/supervisor/student/{a['id']}").data.decode()
check("supervisor student page shows synopsis",
      "Enzyme Kinetics" in h and "Endorse Synopsis" in h)
r = sup.get(f"/supervisor/student/{b['id']}")
check("unassigned student is not accessible",
      r.status_code == 404 and b"not assigned" in r.data)
sup.post(f"/supervisor/student/{a['id']}/endorse")
syn = db.fetchone("SELECT * FROM synopsis_records WHERE student_id=?",
                  (a["id"],))
check("endorsement recorded + forwarded to ASRB",
      syn["endorsed_by"] == "Dr. Sana Malik"
      and syn["asrb_status"] == "Submitted to ASRB")
sup.post(f"/supervisor/student/{a['id']}/progress",
         data={"period": "Jul–Dec 2026", "report_date": "2026-12-20",
               "progress": "Satisfactory", "remarks": "On track"})
pr = db.fetchone("SELECT * FROM progress_reports WHERE student_id=?",
                 (a["id"],))
check("progress report submitted", pr and pr["status"] == "Submitted")

# ---- DAS reviews the report ----
h = admin.get(f"/admin/student/{a['id']}/supervisor").data.decode()
check("supervisor tab shows report + history",
      "Jul–Dec 2026" in h and "Dr. Sana Malik" in h)
admin.post(f"/admin/progress-report/{pr['id']}/review",
           data={"status": "Reviewed", "das_remarks": "Noted"})
pr = db.fetchone("SELECT * FROM progress_reports WHERE id=?",
                 (pr["id"],))
check("report reviewed by DAS", pr["status"] == "Reviewed")

# ---- ASRB outcome ----
admin.post(f"/admin/synopsis/{syn['id']}/asrb",
           data={"asrb_status": "Approved", "meeting_no": "ASRB-2026-04",
                 "meeting_date": "2026-11-10",
                 "decision": "Approved as submitted",
                 "minutes_ref": "MIN-44", "approval_letter_ref": "DAS/2026/118",
                 "back": "academic"})
syn = db.fetchone("SELECT * FROM synopsis_records WHERE student_id=?",
                  (a["id"],))
check("ASRB approval recorded", syn["asrb_status"] == "Approved"
      and syn["meeting_no"] == "ASRB-2026-04")
h = admin.get("/admin/asrb").data.decode()
check("ASRB queue page shows the approval",
      "Approved" in h and "Ayesha Raza" in h)
note = db.fetchone("SELECT * FROM student_notifications"
                   " WHERE student_id=? AND title LIKE 'Synopsis Approved%'",
                   (a["id"],))
check("student notified of ASRB approval", note is not None)

# ---- change of supervisor keeps history ----
admin.post(f"/admin/student/{a['id']}/assign",
           data={"supervisor_id": str(sp2["id"]), "role": "Supervisor",
                 "assigned_date": "2026-11-15"})
rows = db.execute("SELECT * FROM supervisor_assignments"
                  " WHERE student_id=? ORDER BY id", (a["id"],)).fetchall()
check("old assignment ended, new one current",
      len(rows) == 2 and rows[0]["is_current"] == 0
      and rows[0]["ended_date"] == "2026-11-15"
      and rows[1]["is_current"] == 1
      and rows[1]["supervisor_id"] == sp2["id"])
h = sup.get("/supervisor/students").data.decode()
check("former supervisor no longer sees the student",
      "Ayesha Raza</td>" not in h)
sup2 = app.test_client()
sup2.post("/supervisor", data={"email": "imran@cuvas.edu.pk",
                               "password": "super456"})
h = sup2.get("/supervisor/dashboard").data.decode()
check("new supervisor sees the student", "Ayesha Raza" in h)
# co-supervisor does not disturb the main assignment
admin.post(f"/admin/student/{a['id']}/assign",
           data={"supervisor_id": str(sp1["id"]), "role": "Co-supervisor",
                 "assigned_date": "2026-11-16"})
cur = db.execute("SELECT role FROM supervisor_assignments"
                 " WHERE student_id=? AND is_current=1",
                 (a["id"],)).fetchall()
check("co-supervisor added alongside supervisor", len(cur) == 2)

# ---- student sees My Research ----
stu = app.test_client()
stu.post("/student", data={"reg_no": "24-DAS-0101",
                           "password": "3520212345671"})
h = stu.get("/student/dashboard").data.decode()
check("student dashboard shows My Research",
      "My Research" in h and "Enzyme Kinetics" in h
      and "Dr. Imran Ali" in h and "Approved" in h)

# ---- inactive supervisor cannot log in ----
admin.post(f"/admin/supervisors/{sp1['id']}/save",
           data={"name": "Dr. Sana Malik", "designation": "Associate Professor",
                 "department": "Biochemistry", "phone": ""})
sup3 = app.test_client()
r = sup3.post("/supervisor", data={"email": "sana@cuvas.edu.pk",
                                   "password": "super123"})
check("inactive supervisor cannot log in",
      b"Invalid email or password" in r.data)

# ---- tab bar updated ----
h = admin.get(f"/admin/student/{a['id']}").data.decode()
check("tab bar: Academic/Supervisor are links now",
      f"/admin/student/{a['id']}/academic" in h
      and "Supervisor · Phase 4" not in h
      and "Correspondence · Phase 6" not in h)

print(f"\nPHASE 4 OK — {N[0]}/{N[0]} checks passed")
os.remove("students.db")
