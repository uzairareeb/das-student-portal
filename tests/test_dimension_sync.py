"""Dimension freshness fix (2026-11-24, live-test finding).

Two gaps closed in workflow.sync_student_dimensions:
1. The thesis case now feeds the Thesis / Result / DAS dimensions —
   a Controller-declared result no longer leaves Result at "Awaited"
   and Thesis untouched until some unrelated legacy field moves.
2. A synopsis record with a submission date reconciles the legacy
   submission field, so the Overview / student journey stop claiming
   "Not Received" for a synopsis DAS has on record.

    /tmp/dasvenv3/bin/python tests/test_dimension_sync.py
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
        raise SystemExit(f"dimension-sync failed at: {name}")


bilal = db.fetchone("SELECT * FROM students WHERE reg_no='24-DAS-0110'")
sid = bilal["id"]
check("fixture: no viva, no COE, nothing received",
      not bilal["thesis_forward_coe"]
      and bilal["synopsis"] == "Not Received")

admin = app.test_client()
admin.post("/admin", data={"password": "admin123"})

# synopsis record only (new module) — legacy field still blank
admin.post(f"/admin/student/{sid}/synopsis",
           data={"title": "Sync Fix Thesis", "submitted_date": "2026-10-10",
                 "asrb_status": "Submitted to ASRB"})
row = db.fetchone("SELECT * FROM students WHERE id=?", (sid,))
check("before any profile view, legacy synopsis untouched",
      row["synopsis"] != "Received")

# thesis case: DAS submits + forwards; Controller declares Pass —
# deliberately NO viva record and NO legacy submission fields.
admin.post(f"/admin/student/{sid}/thesis/das",
           data={"action": "submitted", "date": "2026-11-01"})
admin.post(f"/admin/student/{sid}/thesis/das",
           data={"action": "forward", "date": "2026-11-02",
                 "dispatch_no": "SYNC-1"})
h = admin.get(f"/admin/student/{sid}/progress").data.decode()
check("forwarded case: Thesis dimension Forwarded",
      "Thesis" in h and "Forwarded" in h)
check("forwarded case: Result still Awaited (nothing declared)",
      "Awaited" in h)

db.execute("INSERT INTO controller_users (name, email, password_hash,"
           " designation, is_active) VALUES ('Sync Controller',"
           " 'sync@test.pk', 'x', 'DC', 1)")
from werkzeug.security import generate_password_hash  # noqa: E402
db.execute("UPDATE controller_users SET password_hash=? WHERE email=?",
           (generate_password_hash("sync1234"), "sync@test.pk"))
db.commit()
coe = app.test_client()
coe.post("/controller", data={"email": "sync@test.pk",
                              "password": "sync1234"})
coe.post(f"/controller/case/{sid}/action", data={"action": "receive",
                                                  "date": "2026-11-03"})
coe.post(f"/controller/case/{sid}/action",
         data={"action": "evaluation_start"})
coe.post(f"/controller/case/{sid}/action",
         data={"action": "evaluation_complete", "date": "2026-11-04"})
coe.post(f"/controller/case/{sid}/action",
         data={"action": "result", "result_status": "Declared — Pass",
               "date": "2026-11-05"})

# the Result & Degree page syncs on open — dimensions must be current
h = admin.get(f"/admin/student/{sid}/progress").data.decode()
check("declared result: Result dimension Approved", "Approved" in h)
check("declared result: Thesis dimension Accepted", "Accepted" in h)
row = db.fetchone("SELECT * FROM students WHERE id=?", (sid,))
check("stored dimensions updated by the read-time sync",
      row["result_status"] == "Approved"
      and row["thesis_status"] == "Accepted"
      and row["das_status"] == "Forwarded")
check("legacy synopsis reconciled from the synopsis record",
      row["synopsis"] == "Received" and row["synopsis_at"] == "2026-10-10")

# student side agrees: journey/stats no longer claim Not Received
stu = app.test_client()
stu.post("/student", data={"reg_no": "24-DAS-0110",
                           "password": bilal["cnic"]})
h = stu.get("/student/dashboard").data.decode()
check("student dashboard shows the synopsis as received",
      "Synopsis" in h and "Not Received" not in h.split("Synopsis")[1][:200])

print(f"\nDIMENSION SYNC FIX OK — {N[0]}/{N[0]} checks passed")
os.remove("students.db")
