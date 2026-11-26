"""Batch 3 (Admin Advanced): Tasks page + auto-tasks, degree queue
blockers, Activity Log filters + export.

    /tmp/dasvenv3/bin/python batch3_tests.py
"""
import io
import os
import re
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

from openpyxl import Workbook  # noqa: E402

HEADERS = ["CNIC", "Name", "Father Name", "Registration No.", "Gender",
           "Degree", "Session", "Folder/File (With GS-Forms)", "Synopsis",
           "Semi-Final Thesis", "Final Thesis", "Thesis Forward (COE)",
           "Status", "Comments (Missing)"]


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(f"batch3 failed at: {name}")


def task(action, status="Pending"):
    return db.fetchone(
        "SELECT * FROM workflow_tasks WHERE action=? AND status=?"
        " ORDER BY id DESC", (action, status))


admin = app.test_client()
admin.post("/admin", data={"password": "admin123"})
ayesha = db.fetchone("SELECT * FROM students WHERE reg_no='24-DAS-0101'")

# ---- manual task lifecycle ----
admin.post("/admin/tasks", data={
    "title": "Call the department about files", "role": "DAS",
    "student_id": str(ayesha["id"]), "due_date": "2020-01-01",
    "priority": "High"})
tk = task("manual")
check("manual task created", tk is not None
      and tk["student_id"] == ayesha["id"])
h = admin.get("/admin/tasks").data.decode()
check("tasks page lists it as overdue",
      "Call the department about files" in h and "overdue" in h)
h = admin.get("/admin/dashboard").data.decode()
_nav = re.search(r'<nav class="side-nav">(.*?)</nav>', h, re.S).group(1)
check("sidebar no longer lists Tasks (redesign 2026-11-26)", "Tasks" not in _nav)
admin.post(f"/admin/tasks/{tk['id']}/done")
check("mark done completes it", task("manual") is None
      and task("manual", "Done") is not None)
admin.post(f"/admin/tasks/{tk['id']}/reopen")
check("reopen restores it", task("manual") is not None)
admin.post(f"/admin/tasks/{tk['id']}/done")

# ---- upload review task lifecycle ----
db.execute("INSERT INTO departments (name, email, password_hash,"
           " created_at) VALUES ('Department of Biochemistry',"
           " 'biochem@test.pk', ?, '2026-11-24 09:00')",
           (__import__("werkzeug.security", fromlist=["x"])
            .generate_password_hash("dept123"),))
db.commit()
dept = app.test_client()
dept.post("/dept", data={"email": "biochem@test.pk",
                         "password": "dept123"})
wb = Workbook()
ws = wb.active
ws.append(HEADERS)
ws.append(["3520266666666", "Task Row", "Father T", "25-TSK-001",
           "Female", "MPhil Biochemistry", "2025", "Not Completed",
           "Not Received", "Not Received", "Not Received", "",
           "Continue", "N/A"])
buf = io.BytesIO()
wb.save(buf)
dept.post("/dept/upload",
          data={"file": (io.BytesIO(buf.getvalue()), "t.xlsx")},
          content_type="multipart/form-data")
up = db.fetchone("SELECT * FROM dept_uploads ORDER BY id DESC")
tk = task(f"upload_review:{up['id']}")
check("dept upload creates a DAS review task", tk is not None
      and tk["responsible_role"] == "DAS")
admin.post(f"/admin/uploads/{up['id']}/approve")
check("approving the batch completes the task",
      task(f"upload_review:{up['id']}") is None)

# ---- thesis receipt task lifecycle ----
admin.post("/admin/controller-users", data={
    "name": "Ctrl One", "email": "ctrl@test.pk",
    "password": "ctrl1234", "designation": "Deputy Controller"})
admin.post(f"/admin/student/{ayesha['id']}/thesis/open")
admin.post(f"/admin/student/{ayesha['id']}/thesis/das",
           data={"action": "forward", "dispatch_no": "DSP-T1",
                 "date": "2026-11-24"})
tk = task("case_receipt")
check("forwarding creates the Controller receipt task",
      tk is not None and tk["responsible_role"] == "Controller")
coe = app.test_client()
coe.post("/controller", data={"email": "ctrl@test.pk",
                              "password": "ctrl1234"})
coe.post(f"/controller/case/{ayesha['id']}/action",
         data={"action": "receive", "date": "2026-11-24"})
check("controller receipt completes the task",
      task("case_receipt") is None)
coe.post(f"/controller/case/{ayesha['id']}/action",
         data={"action": "return", "objections": "Missing page 4"})
tk = task("case_resubmit")
check("return with objections creates a DAS follow-up task",
      tk is not None and tk["responsible_role"] == "DAS")
admin.post(f"/admin/student/{ayesha['id']}/thesis/das",
           data={"action": "resubmit", "date": "2026-11-24"})
check("resubmission completes the follow-up and reopens receipt",
      task("case_resubmit") is None and task("case_receipt") is not None)

# ---- correspondence acknowledgment task ----
admin.post(f"/admin/student/{ayesha['id']}/correspondence/add",
           data={"direction": "Outward", "subject": "Thesis dispatch",
                 "party": "Controller Office", "dispatch_no": "DSP-T9",
                 "letter_date": "2026-11-24"})
corr = db.fetchone("SELECT * FROM correspondence ORDER BY id DESC")
tk = task(f"corr_ack:{corr['id']}")
check("outward letter creates an acknowledgment task", tk is not None)
admin.post(f"/admin/correspondence/{corr['id']}/receive",
           data={"received_date": "2026-11-25"})
check("recording the acknowledgment completes the task",
      task(f"corr_ack:{corr['id']}") is None)

# ---- degree queue blockers ----
h = admin.get("/admin/degree-completion").data.decode()
check("degree queue shows the Waiting On column", "Waiting On" in h)
check("blockers name the pending items (Sana)",
      "Synopsis approved by ASRB" in h and "+4 more" in h)

# ---- activity log filters + export ----
h = admin.get("/admin/activity").data.decode()
check("activity page renders with the filter bar",
      "Recorded Actions" in h and "Export Excel" in h)
h = admin.get("/admin/activity?action=login").data.decode()
check("action filter narrows the log", "login" in h)
r = admin.get("/admin/activity/export.xlsx")
check("activity export downloads an xlsx",
      r.status_code == 200 and r.data[:2] == b"PK")

print(f"\nBATCH 3 OK — {N[0]}/{N[0]} checks passed")
os.remove("students.db")
