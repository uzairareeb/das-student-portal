"""Batch 1 (Admin Advanced): indexes, CSRF origin defence, role
permissions, shared-login toggle.

    /tmp/dasvenv3/bin/python batch1_tests.py
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
        raise SystemExit(f"batch1 failed at: {name}")


# ---- indexes ----
idx = {r["name"] for r in db.fetchall(
    "SELECT name FROM sqlite_master WHERE type='index'")}
check("student lookup indexes exist",
      {"idx_students_reg", "idx_students_cnic", "idx_students_name",
       "idx_students_dept", "idx_students_status"} <= idx)

# ---- CSRF origin defence ----
pub = app.test_client()
r = pub.post("/admin", data={"password": "admin123"},
             headers={"Origin": "https://evil.example.com"})
check("cross-site POST blocked (403)", r.status_code == 403)
r = pub.post("/admin", data={"password": "admin123"},
             headers={"Origin": "http://localhost"})
check("same-host Origin POST reaches the login (302)",
      r.status_code == 302)
pub.get("/admin/logout")
r = pub.post("/admin", data={"password": "admin123"})
check("no-Origin POST (test client) still works", r.status_code == 302)
pub.get("/admin/logout")

# ---- setup officers ----
admin = app.test_client()
admin.post("/admin", data={"password": "admin123"})
admin.post("/admin/users", data={
    "name": "Officer One", "email": "officer@test.pk",
    "password": "officer123", "role": "DAS Officer"})
admin.post("/admin/users", data={
    "name": "Super Sara", "email": "sara@test.pk",
    "password": "sara12345", "role": "Super Admin"})
check("officer + super officer created",
      db.fetchone("SELECT COUNT(*) AS n FROM admin_users")["n"] == 2)

# ---- permissions: officer is blocked from governance actions ----
off = app.test_client()
off.post("/admin", data={"email": "officer@test.pk",
                         "password": "officer123"})
h = off.get("/admin/users", follow_redirects=True).data.decode()
check("officer cannot open the Users page",
      "Only a Super Admin can do this" in h)
h = off.get("/admin/dashboard").data.decode()
check("officer sidebar hides Users + Backup",
      "/admin/users" not in h and "/admin/backup" not in h)
sana = db.fetchone("SELECT * FROM students WHERE reg_no='22-DAS-0055'")
off.post(f"/admin/student/{sana['id']}/degree/issue",
         data={"issued_date": "2026-11-24", "degree_no": "OFF-1"})
check("officer cannot issue a degree",
      db.fetchone("SELECT COUNT(*) AS n FROM degree_records")["n"] == 0)
off.post(f"/admin/student/{sana['id']}/delete")
check("officer cannot delete a student",
      db.fetchone("SELECT * FROM students WHERE id=?",
                  (sana["id"],)) is not None)
r = off.get("/admin/dashboard")
check("officer can still do normal work (dashboard 200)",
      r.status_code == 200)
h = admin.get("/admin/users").data.decode()
check("super admin (shared) sees the Users page + toggle card",
      "Officer accounts" in h and "Shared admin password" in h)

# ---- shared-login toggle ----
solo = app.test_client()
solo.post("/admin", data={"password": "admin123"})
# a fresh DB would have no super officer; here Sara exists, so first
# deactivate her to prove the lockout guard, then reactivate.
sara = db.fetchone("SELECT * FROM admin_users WHERE email='sara@test.pk'")
solo.post(f"/admin/users/{sara['id']}/toggle")
solo.post("/admin/users/shared-login", data={"action": "disable"})
row = db.fetchone("SELECT value FROM settings"
                  " WHERE key='shared_login_disabled'")
check("disable refused without an active Super Admin officer",
      row is None or row["value"] != "1")
solo.post(f"/admin/users/{sara['id']}/toggle")
solo.post("/admin/users/shared-login", data={"action": "disable"})
row = db.fetchone("SELECT value FROM settings"
                  " WHERE key='shared_login_disabled'")
check("disable succeeds with an active Super Admin officer",
      row and row["value"] == "1")
fresh = app.test_client()
r = fresh.post("/admin", data={"password": "admin123"})
h = r.data.decode()
check("shared password rejected while disabled",
      "shared admin password is disabled" in h)
r = fresh.post("/admin", data={"email": "sara@test.pk",
                                "password": "sara12345"})
check("officer login still works while shared is disabled",
      r.status_code == 302)
fresh.get("/admin/logout")
fresh.post("/admin", data={"email": "sara@test.pk",
                           "password": "sara12345"})
fresh.post("/admin/users/shared-login", data={"action": "enable"})
row = db.fetchone("SELECT value FROM settings"
                  " WHERE key='shared_login_disabled'")
check("re-enable restores the shared password", row["value"] == "0")
fresh.get("/admin/logout")
r = fresh.post("/admin", data={"password": "admin123"})
check("shared login works again after re-enable", r.status_code == 302)

print(f"\nBATCH 1 OK — {N[0]}/{N[0]} checks passed")
os.remove("students.db")
