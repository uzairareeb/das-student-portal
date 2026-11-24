"""My Research card — always visible (user decision 2026-11-24, Option A).

The card renders on EVERY student dashboard. Slots without data show
empty states ("Not assigned yet" / "Not submitted yet"); slots with
data show the real records exactly as before.

    /tmp/dasvenv3/bin/python tests/test_my_research_card.py
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
        raise SystemExit(f"my-research failed at: {name}")


bilal = db.fetchone("SELECT * FROM students WHERE reg_no='23-DAS-0098'")
ayesha = db.fetchone("SELECT * FROM students WHERE reg_no='24-DAS-0101'")

# ---- student with NO research data: card + empty states ----
stu = app.test_client()
stu.post("/student", data={"reg_no": "23-DAS-0098",
                           "password": bilal["cnic"]})
h = stu.get("/student/dashboard").data.decode()
check("card renders with no data at all", "My Research" in h)
check("supervisor empty state", "Not assigned yet" in h)
check("synopsis + thesis empty states", h.count("Not submitted yet") >= 2)

# ---- student WITH data: real records, no empty states ----
admin = app.test_client()
admin.post("/admin", data={"password": "admin123"})
admin.post("/admin/supervisors",
           data={"name": "Dr. Card Test", "email": "card@test.pk",
                 "password": "card1234"})
sp = db.fetchone("SELECT * FROM supervisors WHERE email='card@test.pk'")
admin.post(f"/admin/student/{ayesha['id']}/assign",
           data={"supervisor_id": str(sp["id"]), "role": "Supervisor",
                 "assigned_date": "2026-10-05"})
admin.post(f"/admin/student/{ayesha['id']}/synopsis",
           data={"title": "Card Visibility Thesis",
                 "submitted_date": "2026-10-10",
                 "asrb_status": "Submitted to ASRB"})
stu2 = app.test_client()
stu2.post("/student", data={"reg_no": "24-DAS-0101",
                            "password": "3520212345671"})
h = stu2.get("/student/dashboard").data.decode()
check("card shows the real title + supervisor",
      "Card Visibility Thesis" in h and "Dr. Card Test" in h)
check("filled slots show data; only the thesis slot stays empty",
      "Not assigned yet" not in h
      and h.count("Not submitted yet") == 1)  # thesis case slot only

print(f"\nMY RESEARCH CARD OK — {N[0]}/{N[0]} checks passed")
os.remove("students.db")
