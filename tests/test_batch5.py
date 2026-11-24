"""Batch 5 (Admin Advanced): student photo — upload, guarded
serving, list flag, PDF embedding, removal.

    /tmp/dasvenv3/bin/python batch5_tests.py
"""
import base64
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

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDw"
    "AEhQGAhKmMIQAAAABJRU5ErkJggg==")


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(f"batch5 failed at: {name}")


admin = app.test_client()
admin.post("/admin", data={"password": "admin123"})
ayesha = db.fetchone("SELECT * FROM students WHERE reg_no='24-DAS-0101'")
bilal = db.fetchone("SELECT * FROM students WHERE reg_no='23-DAS-0098'")
aid, bid = ayesha["id"], bilal["id"]

check("no photo at first", admin.get(f"/photo/{aid}").status_code == 404)
r = admin.post(f"/admin/student/{aid}/photo",
               data={"photo": (io.BytesIO(PNG), "pic.png")},
               content_type="multipart/form-data")
row = db.fetchone("SELECT * FROM students WHERE id=?", (aid,))
check("photo stored with its mime type",
      row["photo_mime"] == "image/png" and row["photo_name"] == "pic.png")
r = admin.get(f"/photo/{aid}")
check("admin can view the photo, bytes identical",
      r.status_code == 200 and r.data == PNG)
r = admin.post(f"/admin/student/{bid}/photo",
               data={"photo": (io.BytesIO(b"GIF89a"), "x.gif")},
               content_type="multipart/form-data")
check("a GIF is refused",
      db.fetchone("SELECT * FROM students WHERE id=?",
                  (bid,))["photo_data"] is None)
r = admin.get("/admin/students/search.json?q=Ayesha")
rows = r.get_json()["students"]
check("list JSON flags the photo without shipping its bytes",
      rows and rows[0]["has_photo"] == 1 and "photo_data" not in rows[0])
h = admin.get("/admin/students").data.decode()
check("student list renders the photo avatar",
      '<img class="row-avatar"' in h and f"/photo/{aid}" in h)
h = admin.get(f"/admin/student/{aid}").data.decode()
check("profile hero shows the photo + replace/remove controls",
      f"/photo/{aid}" in h and "Replace Photo" in h
      and "Remove Photo" in h)

stu = app.test_client()
stu.post("/student", data={"reg_no": "24-DAS-0101",
                           "password": "3520212345671"})
check("the student can view their own photo",
      stu.get(f"/photo/{aid}").status_code == 200)
check("the student cannot view another student's photo",
      stu.get(f"/photo/{bid}").status_code == 404)
h = stu.get("/student/dashboard").data.decode()
check("student dashboard hero shows the photo", f"/photo/{aid}" in h)
r = stu.get("/student/profile-pdf")
check("Profile PDF still generates with a photo embedded",
      r.status_code == 200 and r.data[:4] == b"%PDF" and len(r.data) > 5000)
anon = app.test_client()
check("anonymous visitors get nothing",
      anon.get(f"/photo/{aid}").status_code == 404)

admin.post(f"/admin/student/{aid}/photo/remove")
check("removal clears the photo",
      db.fetchone("SELECT * FROM students WHERE id=?",
                  (aid,))["photo_data"] is None
      and admin.get(f"/photo/{aid}").status_code == 404)

print(f"\nBATCH 5 OK — {N[0]}/{N[0]} checks passed")
os.remove("students.db")
