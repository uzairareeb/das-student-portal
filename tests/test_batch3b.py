"""Batch 3b (Admin Advanced): Ctrl+K global search, correspondence
templates + scan attachments + awaiting-acknowledgment filter.

    /tmp/dasvenv3/bin/python batch3b_tests.py
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


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(f"batch3b failed at: {name}")


admin = app.test_client()
admin.post("/admin", data={"password": "admin123"})
ayesha = db.fetchone("SELECT * FROM students WHERE reg_no='24-DAS-0101'")

# ---- global search ----
r = admin.get("/admin/search.json?q=Ayesha")
data = r.get_json()
check("search finds the student by name",
      any(x["kind"] == "Student" and "Ayesha Raza" in x["title"]
          for x in data["results"]))
r = admin.get("/admin/search.json?q=3520212345")
check("search finds the student by CNIC fragment",
      any(x["kind"] == "Student" for x in r.get_json()["results"]))
r = admin.get("/admin/search.json?q=A")
check("one-letter query returns nothing",
      r.get_json()["results"] == [])
admin.post("/admin/supervisors", data={
    "name": "Dr Searchable", "email": "search@test.pk",
    "password": "super123", "designation": "Professor"})
r = admin.get("/admin/search.json?q=Searchable")
check("search finds supervisors",
      any(x["kind"] == "Supervisor" for x in r.get_json()["results"]))
h = admin.get("/admin/dashboard").data.decode()
check("sidebar carries the Ctrl+K search trigger",
      "gsTrigger" in h and "gsearch" in h)

# ---- correspondence: attachment + awaiting ----
pdf = b"%PDF-1.4 dummy scan bytes"
before = db.fetchone("SELECT COUNT(*) AS n FROM correspondence")["n"]
r = admin.post(
    f"/admin/student/{ayesha['id']}/correspondence/add",
    data={"direction": "Outward", "subject": "Old dispatched letter",
          "party": "Controller Office", "dispatch_no": "DSP-OLD-1",
          "letter_date": "2026-11-04",
          "attachment": (io.BytesIO(pdf), "scan.pdf")},
    content_type="multipart/form-data")
corr = db.fetchone("SELECT * FROM correspondence ORDER BY id DESC")
check("letter recorded with its attachment",
      corr["file_name"] == "scan.pdf"
      and db.fetchone("SELECT COUNT(*) AS n FROM correspondence")["n"]
      == before + 1)
r = admin.get(f"/admin/correspondence/{corr['id']}/attachment")
check("attachment downloads with the same bytes",
      r.status_code == 200 and r.data == pdf)
r = admin.post(
    f"/admin/student/{ayesha['id']}/correspondence/add",
    data={"direction": "Inward", "subject": "Bad file letter",
          "attachment": (io.BytesIO(b"plain text"), "note.txt")},
    content_type="multipart/form-data")
check("a .txt attachment is refused and nothing is recorded",
      db.fetchone("SELECT COUNT(*) AS n FROM correspondence")["n"]
      == before + 1)
h = admin.get("/admin/correspondence?awaiting=1").data.decode()
check("awaiting filter lists the old dispatched letter",
      "Old dispatched letter" in h and "day(s) waiting" in h)
h = admin.get(
    f"/admin/student/{ayesha['id']}/correspondence").data.decode()
check("student tab shows the attachment + waiting days + templates",
      "scan.pdf" in h and "day(s) waiting" in h
      and "letter-template" in h)

print(f"\nBATCH 3b OK — {N[0]}/{N[0]} checks passed")
os.remove("students.db")
