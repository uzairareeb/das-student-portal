"""Phase 6 (Degree Completion + Correspondence) tests for DAS system.

Runs against a throwaway local SQLite DB with seeded demo data.
    /tmp/dasvenv3/bin/python tests/test_phase6.py
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
        raise SystemExit(f"phase6 failed at: {name}")


sana = db.fetchone("SELECT * FROM students WHERE reg_no='22-DAS-0055'")
sid = sana["id"]
admin = app.test_client()
admin.post("/admin", data={"password": "admin123"})

# ---- checklist seeded + initially incomplete ----
items = db.execute("SELECT * FROM completion_items ORDER BY sort").fetchall()
check("checklist seeded with 7 items", len(items) == 7)
h = admin.get(f"/admin/student/{sid}/progress").data.decode()
check("progress page shows the official checklist",
      "Degree Completion Checklist" in h and "0 of 7" in h)
check("issuance blocked while items pending",
      "cannot be issued yet" in h)

# ---- auto items follow records (never hand-ticked) ----
syn_item = [dict(i) for i in items if dict(i)["auto_source"] == "synopsis"][0]
admin.post(f"/admin/student/{sid}/completion/{syn_item['id']}/mark",
           data={"status": "Done"})
check("auto item cannot be ticked by hand",
      db.fetchone("SELECT COUNT(*) c FROM completion_marks"
                  " WHERE student_id=?", (sid,))["c"] == 0)
admin.post(f"/admin/student/{sid}/synopsis",
           data={"title": "Sana Thesis", "submitted_date": "2026-09-01",
                 "asrb_status": "Not Submitted"})
syn = db.fetchone("SELECT * FROM synopsis_records WHERE student_id=?",
                  (sid,))
admin.post(f"/admin/synopsis/{syn['id']}/asrb",
           data={"asrb_status": "Approved", "meeting_no": "ASRB-2026-09",
                 "meeting_date": "2026-11-10", "decision": "Approved"})
admin.post(f"/admin/student/{sid}/comprehensive",
           data={"exam_date": "2026-06-15", "result": "Pass"})
admin.post(f"/admin/student/{sid}/gs-receipt-all",
           data={"received_date": "2026-11-21", "received_by": "Clerk",
                 "office": "DAS", "diary_no": "D-200"})
db.execute(
    "INSERT INTO viva_records (student_id, viva_date, examiner_name,"
    " result) VALUES (?, '2026-11-15', 'Dr. Test', 'Pass')", (sid,))
db.commit()

# thesis auto item via the Phase 5 flow
admin.post("/admin/controller-users",
           data={"name": "Rafiq", "email": "coe@cuvas.edu.pk",
                 "password": "ctrl1234"})
admin.post(f"/admin/student/{sid}/thesis/open")
admin.post(f"/admin/student/{sid}/thesis/das",
           data={"action": "forward", "date": "2026-11-03",
                 "dispatch_no": "DSP-1"})
ctl = app.test_client()
ctl.post("/controller", data={"email": "coe@cuvas.edu.pk",
                              "password": "ctrl1234"})
ctl.post(f"/controller/case/{sid}/action", data={"action": "receive"})
ctl.post(f"/controller/case/{sid}/action",
         data={"action": "evaluation_start"})
ctl.post(f"/controller/case/{sid}/action",
         data={"action": "evaluation_complete"})
ctl.post(f"/controller/case/{sid}/action",
         data={"action": "result", "result_status": "Declared — Pass"})
h = admin.get(f"/admin/student/{sid}/progress").data.decode()
check("five auto items complete from records", "5 of 7" in h)

# ---- issue attempt blocked until manual items are done ----
admin.post(f"/admin/student/{sid}/degree/issue",
           data={"issued_date": "2026-11-25", "degree_no": "DEG-1"})
check("issue blocked at 5 of 7",
      db.fetchone("SELECT * FROM degree_records WHERE student_id=?",
                  (sid,)) is None)
manual = {dict(i)["name"]: dict(i)["id"] for i in items
          if not dict(i)["auto_source"]}
admin.post(f"/admin/student/{sid}/completion/{manual['Dues / fee clearance']}/mark",
           data={"status": "Done", "remarks": "Verified"})
admin.post(f"/admin/student/{sid}/completion/{manual['Library clearance']}/mark",
           data={"status": "Waived", "remarks": "No dues"})
h = admin.get(f"/admin/student/{sid}/progress").data.decode()
check("all 7 complete — ready to issue",
      "7 of 7" in h and "Mark Degree Issued" in h)
h = admin.get("/admin/degree-completion").data.decode()
check("completion queue shows Sana as ready",
      "Sana Tariq" in h and "Ready to Issue" in h)

# ---- issuance ----
admin.post(f"/admin/student/{sid}/degree/issue",
           data={"issued_date": "2026-11-25", "degree_no": "DEG-2026-001"})
rec = db.fetchone("SELECT * FROM degree_records WHERE student_id=?",
                  (sid,))
stu_row = db.fetchone("SELECT * FROM students WHERE id=?", (sid,))
check("degree record created by the officer",
      rec and rec["degree_no"] == "DEG-2026-001"
      and rec["issued_by"] == "Administrator")
check("degree dimension is Issued", stu_row["degree_status"] == "Issued")
note = db.fetchone("SELECT * FROM student_notifications"
                   " WHERE student_id=? AND title='Degree issued'",
                   (sid,))
check("student notified of issuance", note is not None)

# ---- derivation can never un-issue (sync guard) ----
admin.post(f"/admin/student/{sid}/save",
           data={"name": sana["name"], "status": "Pass Out"})
stu_row = db.fetchone("SELECT * FROM students WHERE id=?", (sid,))
check("sync after a save keeps the degree Issued",
      stu_row["degree_status"] == "Issued")

# ---- student sees the issued degree ----
stu = app.test_client()
stu.post("/student", data={"reg_no": "22-DAS-0055",
                           "password": sana["cnic"]})
h = stu.get("/student/dashboard").data.decode()
check("student dashboard shows Degree Issued + number",
      "Issued" in h and "DEG-2026-001" in h)

# ---- revoke corrects the record ----
admin.post(f"/admin/student/{sid}/degree/revoke")
stu_row = db.fetchone("SELECT * FROM students WHERE id=?", (sid,))
check("revoke removes record + dimension derives again (Eligible)",
      db.fetchone("SELECT * FROM degree_records WHERE student_id=?",
                  (sid,)) is None
      and stu_row["degree_status"] == "Eligible")

# ---- correspondence: sent is not received ----
admin.post(f"/admin/student/{sid}/correspondence/add",
           data={"direction": "Inward", "subject": "Result notification",
                 "party": "Controller Office", "diary_no": "D-501",
                 "letter_date": "2026-11-20"})
c = db.fetchone("SELECT * FROM correspondence WHERE diary_no='D-501'")
check("inward letter recorded as Received with diary no.",
      c and c["status"] == "Received" and c["received_date"])
admin.post(f"/admin/student/{sid}/correspondence/add",
           data={"direction": "Outward", "subject": "Degree dispatch",
                 "party": "Registrar Office", "dispatch_no": "DSP-77",
                 "letter_date": "2026-11-26"})
c = db.fetchone("SELECT * FROM correspondence WHERE dispatch_no='DSP-77'")
check("outward letter is Dispatched, NOT received",
      c and c["status"] == "Dispatched" and not c["received_date"])
admin.post(f"/admin/correspondence/{c['id']}/receive",
           data={"received_date": "2026-11-28"})
c = db.fetchone("SELECT * FROM correspondence WHERE id=?", (c["id"],))
check("acknowledgment records the receipt",
      c["status"] == "Received" and c["received_date"] == "2026-11-28")
admin.post(f"/admin/correspondence/{db.fetchone('SELECT id FROM correspondence WHERE diary_no=?', ('D-501',))['id']}/receive")
c = db.fetchone("SELECT * FROM correspondence WHERE diary_no='D-501'")
check("inward letter can be filed", c["status"] == "Filed")

# general register entry (no student)
admin.post("/admin/student/0/correspondence/add",
           data={"direction": "Inward", "subject": "HEC circular",
                 "party": "HEC", "diary_no": "D-900",
                 "letter_date": "2026-11-22", "back": "register"})
h = admin.get("/admin/correspondence").data.decode()
check("register lists letters incl. general entry",
      "HEC circular" in h and "General" in h and "DSP-77" in h)
h = admin.get("/admin/correspondence?q=DSP-77").data.decode()
check("register search by dispatch no.", "Degree dispatch" in h
      and "HEC circular" not in h)

# ---- tab bar: every tab is a live link now ----
h = admin.get(f"/admin/student/{sid}").data.decode()
check("correspondence tab is a link; no Phase chips remain",
      f"/admin/student/{sid}/correspondence" in h
      and "· Phase" not in h)

# ---- checklist stays configuration (add an item) ----
admin.post("/admin/completion-items",
           data={"name": "Hostel clearance", "auto_source": "",
                 "required": "on", "sort": "8"})
n = db.fetchone("SELECT COUNT(*) c FROM completion_items")["c"]
check("admin can extend the checklist", n == 8)

print(f"\nPHASE 6 OK — {N[0]}/{N[0]} checks passed")
os.remove("students.db")
