"""Phase 7 — UI consistency audit for the DAS Central System.

Renders the main page of every portal and asserts each one loads
cleanly (HTTP 200 after redirects), contains no leftover phase
placeholder text and no error traces.

    /tmp/dasvenv3/bin/python tests/test_ui_consistency.py
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
from werkzeug.security import generate_password_hash  # noqa: E402

app.app_context().push()
app.config["TESTING"] = True
N = [0]
db = get_db()
FAILED = []


ICON_PAGES = set()


def audit(client, label, url):
    if url in ICON_PAGES:
        N[0] += 1
        r0 = client.get(url, follow_redirects=True)
        h0 = r0.data.decode("utf-8", "replace")
        ok0 = "section-icon" in h0
        print(("PASS " if ok0 else "FAIL ") + f"design {url}"
              + ("" if ok0 else " (no section-icon)"))
        if not ok0:
            FAILED.append(url + " (design)")
    N[0] += 1
    r = client.get(url, follow_redirects=True)
    h = r.data.decode("utf-8", "replace")
    ok = (r.status_code == 200 and "· Phase" not in h
          and "Traceback" not in h and "</html>" in h)
    print(("PASS " if ok else "FAIL ") + f"{label} {url}"
          + ("" if ok else f" (status {r.status_code})"))
    if not ok:
        FAILED.append(url)


a = db.fetchone("SELECT * FROM students WHERE reg_no='24-DAS-0101'")
admin = app.test_client()
admin.post("/admin", data={"password": "admin123"})

# public + login pages
pub = app.test_client()
for url in ["/", "/admin", "/student", "/dept", "/supervisor",
            "/controller"]:
    audit(pub, "public", url)

ICON_PAGES.update([
    "/admin/archive", "/admin/trash", "/admin/supervisors",
    "/admin/asrb", "/admin/thesis-cases", "/admin/controller-users",
    "/admin/degree-completion", "/admin/completion-items",
    "/admin/correspondence", "/admin/doc-catalogue", "/admin/users",
    "/admin/backup", "/admin/activity", "/admin/notifications",
    "/controller/cases", "/supervisor/students",
    "/student/notifications", "/supervisor/notifications",
    "/controller/notifications", "/dept/notifications",
])

# admin pages
for url in ["/admin/dashboard", "/admin/students", "/admin/archive",
            "/admin/trash", "/admin/departments", "/admin/uploads",
            "/admin/viva", "/admin/reports", "/admin/schedule",
            "/admin/supervisors", "/admin/asrb", "/admin/thesis-cases",
            "/admin/controller-users", "/admin/degree-completion",
            "/admin/completion-items", "/admin/correspondence",
            "/admin/doc-catalogue", "/admin/users", "/admin/backup",
            "/admin/activity", "/admin/notifications"]:
    audit(admin, "admin", url)

# every student profile tab
sid = a["id"]
for tab in ["", "/documents", "/academic", "/supervisor", "/viva",
            "/thesis", "/progress", "/correspondence", "/timeline"]:
    audit(admin, "profile-tab", f"/admin/student/{sid}{tab}")

# department portal
db.execute("INSERT INTO departments (name, email, password_hash,"
           " created_at) VALUES ('Department of Biochemistry',"
           " 'biochem@test.pk', ?, '2026-01-01')",
           (generate_password_hash("dept123"),))
db.commit()
dept = app.test_client()
dept.post("/dept", data={"email": "biochem@test.pk",
                         "password": "dept123"})
for url in ["/dept/dashboard", "/dept/upload", "/dept/students",
            "/dept/viva", "/dept/viva/calendar", "/dept/notifications"]:
    audit(dept, "dept", url)

# supervisor portal
admin.post("/admin/supervisors",
           data={"name": "Dr. Audit", "email": "audit@test.pk",
                 "password": "audit123"})
sup_row = db.fetchone("SELECT * FROM supervisors"
                      " WHERE email='audit@test.pk'")
admin.post(f"/admin/student/{sid}/assign",
           data={"supervisor_id": str(sup_row["id"]),
                 "role": "Supervisor", "assigned_date": "2026-10-05"})
sup = app.test_client()
sup.post("/supervisor", data={"email": "audit@test.pk",
                              "password": "audit123"})
for url in ["/supervisor/dashboard", "/supervisor/students",
            f"/supervisor/student/{sid}", "/supervisor/notifications"]:
    audit(sup, "supervisor", url)

# controller portal
admin.post("/admin/controller-users",
           data={"name": "Audit COE", "email": "coe@test.pk",
                 "password": "ctrl1234"})
admin.post(f"/admin/student/{sid}/thesis/open")
ctl = app.test_client()
ctl.post("/controller", data={"email": "coe@test.pk",
                              "password": "ctrl1234"})
for url in ["/controller/dashboard", "/controller/cases",
            f"/controller/case/{sid}", "/controller/notifications"]:
    audit(ctl, "controller", url)

# student portal
stu = app.test_client()
stu.post("/student", data={"reg_no": "24-DAS-0101",
                           "password": "3520212345671"})
for url in ["/student/dashboard", "/student/upload",
            "/student/notifications", "/student/record"]:
    audit(stu, "student", url)

print(f"\nUI AUDIT: {N[0] - len(FAILED)}/{N[0]} pages clean")
if FAILED:
    raise SystemExit(f"ui audit failed: {FAILED}")
os.remove("students.db")
