"""Phase 7 — END-TO-END journey test for the DAS Central System.

One student travels the WHOLE system through the real routes of every
portal, in office order:
  Dept intake upload -> DAS review/approve -> student login ->
  document upload/return/re-upload/verify -> GS hard-copy receipts ->
  supervisor account + assignment -> supervisor endorse + progress
  report -> DAS review -> ASRB approval -> semesters + comprehensive
  -> submissions -> dept viva (Pass) -> thesis case -> Controller
  receipt/evaluation/result -> correspondence -> completion checklist
  -> degree ISSUED -> student sees it -> timeline holds the journey.

    /tmp/dasvenv3/bin/python tests/test_e2e_journey.py
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
PDF = b"%PDF-1.4\ne2e journey file\n%%EOF"
HEADERS = ["CNIC", "Name", "Father Name", "Registration No.", "Gender",
           "Degree", "Session", "Folder/File (With GS-Forms)", "Synopsis",
           "Semi-Final Thesis", "Final Thesis", "Thesis Forward (COE)",
           "Status", "Comments (Missing)"]
CNIC = "3520299999999"
REG = "25-DAS-7777"


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(f"e2e failed at: {name}")


def wb_bytes(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ============ 1. DEPARTMENT INTAKE ============
db.execute("INSERT INTO departments (name, email, password_hash,"
           " created_at) VALUES ('Department of Biochemistry',"
           " 'biochem@test.pk', ?, '2026-01-01')",
           (generate_password_hash("dept123"),))
db.commit()
admin = app.test_client()
admin.post("/admin", data={"password": "admin123"})
dept = app.test_client()
dept.post("/dept", data={"email": "biochem@test.pk",
                         "password": "dept123"})
dept.post("/dept/upload",
          data={"file": (io.BytesIO(wb_bytes([[
              CNIC, "Journey Student", "Journey Father", REG, "Female",
              "MPhil Biochemistry", "F. 23-25", "Not Completed",
              "Not Received", "Not Received", "Not Received", "",
              "Continue", "N/A"]])), "journey.xlsx")},
          content_type="multipart/form-data")
up = dict(db.fetchone("SELECT * FROM dept_uploads ORDER BY id DESC"))
check("intake batch stored + parsed",
      up["status"] == "pending" and up["valid_count"] == 1)
admin.post(f"/admin/uploads/{up['id']}/approve")
stu_row = db.fetchone("SELECT * FROM students WHERE reg_no=?", (REG,))
check("DAS approval imported the student", stu_row is not None)
sid = stu_row["id"]
up = dict(db.fetchone("SELECT * FROM dept_uploads WHERE id=?",
                      (up["id"],)))
check("batch locked as approved after import",
      up["status"] == "approved" and up["imported_count"] == 1)

# ============ 2. STUDENT LOGIN + DOCUMENTS ============
stu = app.test_client()
stu.post("/student", data={"reg_no": REG, "password": CNIC})
check("student logs in with CNIC password",
      stu.get("/student/dashboard").status_code == 200)


def upload_doc(client):
    return client.post(
        "/student/upload",
        data={"doc_type": "Complete File (Merged PDF)",
              "file": (io.BytesIO(PDF), "file.pdf")},
        content_type="multipart/form-data")


upload_doc(stu)
doc = db.fetchone("SELECT * FROM documents WHERE student_id=?"
                  " ORDER BY id DESC", (sid,))
check("student upload stored as version 1", doc["version"] == 1)
admin.post(f"/admin/document/{doc['id']}/verify",
           data={"status": "Returned", "remark": "Page 3 is blank",
                 "back": "documents"})
h = stu.get("/student/upload").data.decode()
check("student sees the return + remark",
      "Returned — please re-upload" in h and "Page 3 is blank" in h)
upload_doc(stu)
doc = db.fetchone("SELECT * FROM documents WHERE student_id=?"
                  " ORDER BY id DESC", (sid,))
check("re-upload is version 2", doc["version"] == 2)
admin.post(f"/admin/document/{doc['id']}/verify",
           data={"status": "Verified", "back": "documents"})
doc = db.fetchone("SELECT * FROM documents WHERE id=?", (doc["id"],))
check("DAS verified the re-upload", doc["status"] == "Verified")
admin.post(f"/admin/student/{sid}/gs-receipt-all",
           data={"received_date": "2026-10-02", "received_by": "Clerk",
                 "office": "DAS", "diary_no": "D-E2E"})
n = db.fetchone("SELECT COUNT(*) c FROM gs_receipts WHERE student_id=?",
                (sid,))["c"]
check("GS hard copies received (5 MPhil items)", n == 5)

# ============ 3. SUPERVISOR ============
admin.post("/admin/supervisors",
           data={"name": "Dr. Journey Supervisor",
                 "email": "sup@test.pk", "password": "sup12345",
                 "designation": "Associate Professor",
                 "department": "Biochemistry"})
sup_row = db.fetchone("SELECT * FROM supervisors WHERE email='sup@test.pk'")
admin.post("/admin/supervisors",
           data={"name": "Dr. Outsider", "email": "out@test.pk",
                 "password": "out12345"})
out_row = db.fetchone("SELECT * FROM supervisors WHERE email='out@test.pk'")
admin.post(f"/admin/student/{sid}/assign",
           data={"supervisor_id": str(sup_row["id"]),
                 "role": "Supervisor", "assigned_date": "2026-10-05"})
sup = app.test_client()
sup.post("/supervisor", data={"email": "sup@test.pk",
                              "password": "sup12345"})
h = sup.get("/supervisor/students").data.decode()
check("supervisor sees the journey student", "Journey Student" in h)
outsider = app.test_client()
outsider.post("/supervisor", data={"email": "out@test.pk",
                                   "password": "out12345"})
r = outsider.get(f"/supervisor/student/{sid}")
check("unassigned supervisor gets 404 (isolation)",
      r.status_code == 404)
admin.post(f"/admin/student/{sid}/synopsis",
           data={"title": "Journey Thesis Title",
                 "submitted_date": "2026-10-10",
                 "asrb_status": "Not Submitted"})
syn = db.fetchone("SELECT * FROM synopsis_records WHERE student_id=?",
                  (sid,))
sup.post(f"/supervisor/student/{sid}/endorse")
syn = db.fetchone("SELECT * FROM synopsis_records WHERE id=?",
                  (syn["id"],))
check("supervisor endorsed the synopsis",
      syn["endorsed_by"] == "Dr. Journey Supervisor"
      and syn["asrb_status"] == "Submitted to ASRB")
sup.post(f"/supervisor/student/{sid}/progress",
         data={"period": "Oct–Dec 2026", "report_date": "2026-12-20",
               "progress": "Satisfactory", "remarks": "On track"})
pr = db.fetchone("SELECT * FROM progress_reports WHERE student_id=?",
                 (sid,))
admin.post(f"/admin/progress-report/{pr['id']}/review",
           data={"status": "Reviewed", "das_remarks": "Noted"})
pr = db.fetchone("SELECT * FROM progress_reports WHERE id=?",
                 (pr["id"],))
check("DAS reviewed the progress report", pr["status"] == "Reviewed")
admin.post(f"/admin/synopsis/{syn['id']}/asrb",
           data={"asrb_status": "Approved", "meeting_no": "ASRB-E2E",
                 "meeting_date": "2027-01-10",
                 "decision": "Approved as submitted"})
syn = db.fetchone("SELECT * FROM synopsis_records WHERE id=?",
                  (syn["id"],))
check("ASRB approved the synopsis", syn["asrb_status"] == "Approved")

# ============ 4. ACADEMIC + SUBMISSIONS + VIVA ============
admin.post(f"/admin/student/{sid}/semester",
           data={"semester_no": "1", "term": "Fall 2025",
                 "status": "Completed", "gpa": "3.60", "cgpa": "3.60"})
admin.post(f"/admin/student/{sid}/comprehensive",
           data={"exam_date": "2027-02-01", "result": "Pass"})
admin.post(f"/admin/student/{sid}/save",
           data={"name": "Journey Student", "status": "Continue",
                 "synopsis": "Received", "semi_final_thesis": "Received",
                 "final_thesis": "Received", "folder_file": "Completed"})
dept.post("/dept/viva",
          data={"student_id": str(sid), "examiner_name": "Dr. Viva Ext",
                "viva_date": "2027-03-01", "result": "Pass",
                "obtained_marks": "95", "total_marks": "120"})
viva = db.fetchone("SELECT * FROM viva_records WHERE student_id=?",
                   (sid,))
check("department recorded the viva Pass",
      viva and viva["result"] == "Pass")
stu_row = db.fetchone("SELECT * FROM students WHERE id=?", (sid,))
check("auto rules moved the status forward (Viva Done)",
      stu_row["status"] == "Viva Done")

# ============ 5. THESIS CASE + CONTROLLER ============
admin.post("/admin/controller-users",
           data={"name": "Controller E2E", "email": "coe@test.pk",
                 "password": "ctrl1234"})
admin.post(f"/admin/student/{sid}/thesis/open")
admin.post(f"/admin/student/{sid}/thesis/das",
           data={"action": "submitted", "date": "2027-03-05"})
admin.post(f"/admin/student/{sid}/thesis/examiner",
           data={"examiner_name": "Prof. Secret Examiner",
                 "affiliation": "External University",
                 "examiner_role": "External",
                 "appointed_date": "2027-03-06"})
admin.post(f"/admin/student/{sid}/thesis/das",
           data={"action": "forward", "date": "2027-03-07",
                 "dispatch_no": "DSP-E2E"})
case = db.fetchone("SELECT * FROM thesis_cases WHERE student_id=?",
                   (sid,))
check("forwarded but NOT received (no false automation)",
      case["case_status"] == "Forwarded to Controller"
      and not case["received_date"])
ctl = app.test_client()
ctl.post("/controller", data={"email": "coe@test.pk",
                              "password": "ctrl1234"})
h = ctl.get("/controller/dashboard").data.decode()
check("controller dashboard queues the receipt",
      "Journey Student" in h)
ctl.post(f"/controller/case/{sid}/action",
         data={"action": "receive", "date": "2027-03-09"})
ctl.post(f"/controller/case/{sid}/action",
         data={"action": "evaluation_start"})
ctl.post(f"/controller/case/{sid}/action",
         data={"action": "evaluation_complete"})
ctl.post(f"/controller/case/{sid}/action",
         data={"action": "result",
               "result_status": "Declared — Pass",
               "date": "2027-04-01"})
case = db.fetchone("SELECT * FROM thesis_cases WHERE student_id=?",
                   (sid,))
check("controller completed the case",
      case["case_status"] == "Result Declared"
      and case["received_by"] == "Controller E2E")

# ============ 6. CORRESPONDENCE ============
admin.post(f"/admin/student/{sid}/correspondence/add",
           data={"direction": "Outward", "subject": "Result dispatch",
                 "party": "Registrar Office", "dispatch_no": "DSP-C1",
                 "letter_date": "2027-04-02"})
corr = db.fetchone("SELECT * FROM correspondence"
                   " WHERE dispatch_no='DSP-C1'")
check("letter dispatched, not yet received",
      corr["status"] == "Dispatched" and not corr["received_date"])
admin.post(f"/admin/correspondence/{corr['id']}/receive",
           data={"received_date": "2027-04-05"})
corr = db.fetchone("SELECT * FROM correspondence WHERE id=?",
                   (corr["id"],))
check("acknowledgment recorded", corr["status"] == "Received")

# ============ 7. COMPLETION + DEGREE ISSUED ============
items = db.execute("SELECT * FROM completion_items").fetchall()
manual = {dict(i)["name"]: dict(i)["id"] for i in items
          if not dict(i)["auto_source"]}
admin.post(f"/admin/student/{sid}/completion/{manual['Dues / fee clearance']}/mark",
           data={"status": "Done"})
admin.post(f"/admin/student/{sid}/completion/{manual['Library clearance']}/mark",
           data={"status": "Done"})
h = admin.get(f"/admin/student/{sid}/progress").data.decode()
check("checklist complete end-to-end (7 of 7)", "7 of 7" in h)
admin.post(f"/admin/student/{sid}/degree/issue",
           data={"issued_date": "2027-04-10",
                 "degree_no": "DEG-E2E-001"})
rec = db.fetchone("SELECT * FROM degree_records WHERE student_id=?",
                  (sid,))
stu_row = db.fetchone("SELECT * FROM students WHERE id=?", (sid,))
check("DEGREE ISSUED at the end of the journey",
      rec and rec["degree_no"] == "DEG-E2E-001"
      and stu_row["degree_status"] == "Issued")
check("dimensions coherent (result Approved, degree Issued)",
      stu_row["result_status"] == "Approved")

# ============ 8. STUDENT'S OWN VIEW ============
h = stu.get("/student/dashboard").data.decode()
check("student sees supervisor + thesis title",
      "Dr. Journey Supervisor" in h and "Journey Thesis Title" in h)
check("student sees the issued degree", "DEG-E2E-001" in h)
check("examiner name NEVER reached the student",
      "Prof. Secret Examiner" not in h)
h = admin.get(f"/admin/student/{sid}/timeline").data.decode()
check("admin timeline holds the journey",
      "Issued" in h and "Controller" in h)

print(f"\nE2E JOURNEY OK — {N[0]}/{N[0]} checks passed")
os.remove("students.db")
