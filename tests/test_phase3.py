"""Phase 3 (Student 360 + Documents) tests for the DAS Central System.

Runs against a throwaway local SQLite DB with seeded demo data.
    /tmp/dasvenv3/bin/python phase3_tests.py
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
app.app_context().push()
app.config["TESTING"] = True
N = [0]
db = get_db()
PDF = b"%PDF-1.4\nphase3 test file\n%%EOF"


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(f"phase3 failed at: {name}")


# ---- catalogue seed ----
n = db.fetchone("SELECT COUNT(*) c FROM document_types")["c"]
check("catalogue seeded with 18 entries", n == 18)
gs05 = db.fetchone("SELECT * FROM document_types WHERE code='GS-05'")
check("GS-05 is PhD-only", gs05 and gs05["applies_to"] == "PhD")
chk = db.fetchone("SELECT * FROM document_types WHERE name='Checklist'")
check("Checklist applies to all", chk and chk["applies_to"] == "All")
eth = db.fetchone("SELECT * FROM document_types"
                  " WHERE name LIKE 'Ethical Certificate%'")
check("conditional entry not marked required",
      eth and eth["required"] == 0)

admin = app.test_client()
admin.post("/admin", data={"password": "admin123"})
h = admin.get("/admin/doc-catalogue").data.decode()
check("catalogue page renders", "Document Catalogue" in h
      and "GS-05" in h)
admin.post("/admin/doc-catalogue",
           data={"code": "GS-08", "name": "Turnitin Similarity Report",
                 "applies_to": "All", "stage": "Thesis",
                 "required": "on", "requires_hardcopy": "on"})
n = db.fetchone("SELECT COUNT(*) c FROM document_types")["c"]
check("catalogue entry added", n == 19)
new = db.fetchone("SELECT * FROM document_types WHERE code='GS-08'")
admin.post(f"/admin/doc-catalogue/{new['id']}/save",
           data={"code": "GS-08", "name": "Turnitin Similarity Report",
                 "applies_to": "PhD", "stage": "Thesis",
                 "required": "on", "active": "on"})
new = db.fetchone("SELECT * FROM document_types WHERE code='GS-08'")
check("catalogue entry edited", new["applies_to"] == "PhD"
      and new["requires_hardcopy"] == 0)

# ---- student A (MPhil): uploads with versions ----
a = db.fetchone("SELECT * FROM students WHERE reg_no='24-DAS-0101'")
stu = app.test_client()
stu.post("/student", data={"reg_no": "24-DAS-0101",
                           "password": "3520212345671"})
h = stu.get("/student/upload").data.decode()
check("student checklist shows hard-copy column",
      "Hard Copy at DAS" in h and "Not Received Yet" in h)
check("MPhil checklist hides PhD-only GS-05",
      "Approved Synopsis" not in h)


def up(client, label):
    return client.post("/student/upload",
                       data={"doc_type": label,
                             "file": (io.BytesIO(PDF), "file.pdf")},
                       content_type="multipart/form-data")


up(stu, "Complete File (Merged PDF)")
d1 = db.fetchone("SELECT * FROM documents WHERE student_id=?"
                 " ORDER BY id DESC", (a["id"],))
# seed.py already stores one demo copy of this label (version 1)
check("upload versions increment with checksum",
      d1["version"] == 2 and len(d1["checksum"] or "") == 64)
up(stu, "Complete File (Merged PDF)")
d2 = db.fetchone("SELECT * FROM documents WHERE student_id=?"
                 " ORDER BY id DESC", (a["id"],))
check("next upload is version 3", d2["version"] == 3
      and d2["id"] != d1["id"])
h = stu.get("/student/upload").data.decode()
check("student sees version labels", "v3" in h)

# ---- digital upload never marks a hard copy ----
n = db.fetchone("SELECT COUNT(*) c FROM gs_receipts WHERE student_id=?",
                (a["id"],))["c"]
check("uploads created no hard-copy receipt", n == 0)

# ---- admin documents tab + review ----
h = admin.get(f"/admin/student/{a['id']}/documents").data.decode()
check("documents tab renders checklist + uploads",
      "Hard-Copy Checklist" in h and "SHA-256" in h and "v3" in h)
admin.post(f"/admin/document/{d2['id']}/verify",
           data={"status": "Verified", "remark": "Looks complete",
                 "back": "documents"})
d2 = db.fetchone("SELECT * FROM documents WHERE id=?", (d2["id"],))
check("document verified", d2["status"] == "Verified")
note = db.fetchone("SELECT * FROM student_notifications"
                   " WHERE student_id=? AND title LIKE 'Document verified%'",
                   (a["id"],))
check("student notified of verification", note is not None)
admin.post(f"/admin/document/{d1['id']}/verify",
           data={"status": "Returned",
                 "remark": "Pages 4-5 are missing — re-upload",
                 "back": "documents"})
d1 = db.fetchone("SELECT * FROM documents WHERE id=?", (d1["id"],))
check("document returned with remark", d1["status"] == "Returned")
h = stu.get("/student/upload").data.decode()
check("student sees the return + remark",
      "Returned — please re-upload" in h
      and "Pages 4-5 are missing" in h)

# ---- GS hard-copy receipts ----
gs01 = db.fetchone("SELECT * FROM document_types WHERE code='GS-01'")
admin.post(f"/admin/student/{a['id']}/gs-receipt",
           data={"doc_type_id": str(gs01["id"]),
                 "received_date": "2026-11-20", "received_by": "Office Clerk",
                 "office": "DAS", "diary_no": "D-101"})
r = db.fetchone("SELECT * FROM gs_receipts WHERE student_id=?",
                (a["id"],))
check("single receipt recorded", r and r["diary_no"] == "D-101")
h = stu.get("/student/upload").data.decode()
check("student sees hard-copy receipt + diary",
      "D-101" in h and "Received" in h)
admin.post(f"/admin/student/{a['id']}/gs-receipt-all",
           data={"received_date": "2026-11-21", "received_by": "Clerk",
                 "office": "DAS", "diary_no": "D-102"})
n = db.fetchone("SELECT COUNT(*) c FROM gs_receipts WHERE student_id=?",
                (a["id"],))["c"]
# MPhil catalogue = Checklist + GS-01..04 = 5, GS-01 already had one
check("mark-all fills the remaining items", n == 5)
admin.post(f"/admin/student/{a['id']}/gs-receipt-all",
           data={"received_date": "2026-11-22", "diary_no": "D-103"})
n2 = db.fetchone("SELECT COUNT(*) c FROM gs_receipts WHERE student_id=?",
                 (a["id"],))["c"]
check("mark-all again adds nothing", n2 == 5)
note = db.fetchone("SELECT * FROM student_notifications"
                   " WHERE student_id=?"
                   " AND title LIKE 'Hard copy received%'", (a["id"],))
check("student notified of hard-copy receipt", note is not None)

# ---- other tabs render ----
h = admin.get(f"/admin/student/{a['id']}/timeline").data.decode()
check("timeline shows receipt + upload events",
      "Hard copy received" in h and "Uploaded:" in h)
h = admin.get(f"/admin/student/{a['id']}/progress").data.decode()
check("progress tab shows dimensions",
      "Status Dimensions" in h and "Degree" in h
      and "official degree-completion checklist" in h)
h = admin.get(f"/admin/student/{a['id']}/viva").data.decode()
check("viva tab renders", "Viva Records" in h)
h = admin.get(f"/admin/student/{a['id']}").data.decode()
check("overview shows the tab bar",
      "Result &amp; Degree" in h and "Thesis Case" in h)

# ---- receipt delete ----
admin.post(f"/admin/gs-receipt/{r['id']}/delete")
gone = db.fetchone("SELECT * FROM gs_receipts WHERE id=?", (r["id"],))
check("receipt can be removed", gone is None)

# ---- isolation + PhD checklist ----
b = db.fetchone("SELECT * FROM students WHERE reg_no='23-DAS-0098'")
db.execute("UPDATE students SET degree='PhD Zoology' WHERE id=?",
           (b["id"],))
db.commit()
stu2 = app.test_client()
stu2.post("/student", data={"reg_no": "23-DAS-0098",
                            "password": b["cnic"]})
h = stu2.get("/student/upload").data.decode()
check("PhD checklist includes GS-07", "Plagiarism Undertaking" in h)
check("other student's diary number not visible", "D-102" not in h)

print(f"\nPHASE 3 OK — {N[0]}/{N[0]} checks passed")
os.remove("students.db")
