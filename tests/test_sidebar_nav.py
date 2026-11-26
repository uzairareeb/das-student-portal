"""Sidebar redesign (live system): 5-group accordion nav, Dashboard standalone,
Tasks / Data Quality / Correspondence removed from the sidebar, collapsed
icon-only mode, active-group auto-expansion, permissions intact.

    /tmp/dasvenv3/bin/python test_sidebar_nav.py
"""
import os
import pathlib
import re
import sys

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
        raise SystemExit(f"sidebar_nav failed at: {name}")


def nav_of(html):
    m = re.search(r'<nav class="side-nav">(.*?)</nav>', html, re.S)
    assert m, "side-nav block missing"
    return m.group(1)


admin = app.test_client()
admin.post("/admin", data={"password": "admin123"})
dash = admin.get("/admin/dashboard").get_data(as_text=True)
nav = nav_of(dash)

# ---- 1. Dashboard standalone, before the first group ----
check("Dashboard standalone before first group",
      nav.index("Dashboard") < nav.index('data-group="students"')
      and 'id="nav-dashboard"' in nav)

# ---- 2. five groups in the specified order ----
groups = re.findall(r'data-group="(\w+)"', nav)
check("five groups in order",
      groups == ["students", "departments", "academic", "reports", "system"])
for title in ["Student Management", "Department Management", "Academic & Research",
              "Reports & Documents", "System Administration"]:
    check("group title: " + title, title in nav)

# ---- 3. items assigned to the right group, in order ----
def group_body(html, key):
    m = re.search(r'data-group="%s"(.*?)</div>\s*</div>' % key, html, re.S)
    assert m, "group " + key + " missing"
    return m.group(1)

stu = group_body(nav, "students")
check("students group items in order",
      [x for x in ["Student List", "Add Student", "Import Students",
                   "Archived Students", "Recycle Bin", "Duplicate Records"]
       if x in stu] == ["Student List", "Add Student", "Import Students",
                        "Archived Students", "Recycle Bin", "Duplicate Records"])
acad = group_body(nav, "academic")
check("academic group items in order",
      [x for x in ["Thesis Cases", "Synopsis & ASRB", "Viva Records", "Supervisors",
                   "Schedule Generator", "Degree Completion", "Controller Office"]
       if x in acad] == ["Thesis Cases", "Synopsis & ASRB", "Viva Records", "Supervisors",
                         "Schedule Generator", "Degree Completion", "Controller Office"])
sysg = group_body(nav, "system")
check("system group items in order",
      [x for x in ["User Management", "Notifications", "Activity Log",
                   "Session Management", "Backup & Restore", "Completion Checklist"]
       if x in sysg] == ["User Management", "Notifications", "Activity Log",
                         "Session Management", "Backup & Restore", "Completion Checklist"])
check("dept group items", "Departments" in group_body(nav, "departments")
      and "Department Uploads" in group_body(nav, "departments"))
check("reports group items", "Reports" in group_body(nav, "reports")
      and "Document Catalogue" in group_body(nav, "reports"))

# ---- 4. removed items appear nowhere in the sidebar ----
for needle in ["admin_tasks", "admin_data_quality", "admin_correspondence",
               ">Tasks<", "Data Quality", "Correspondence"]:
    check("sidebar has no " + needle, needle not in nav)

# ---- 5. the pages still exist (routes preserved, only the menu entry is gone) ----
for path in ["/admin/tasks", "/admin/data-quality", "/admin/correspondence"]:
    check("route preserved: " + path, admin.get(path).status_code == 200)

# ---- 6. active highlighting + active group auto-open ----
stu_page = admin.get("/admin/students").get_data(as_text=True)
check("Student List active on list page",
      re.search(r'class="active"[^>]*>\s*<svg[^>]*>.*?</svg>\s*Student List',
                nav_of(stu_page), re.S) is not None)
check("students group open on list page",
      re.search(r'data-group="students"[^>]*class="[^"]*open', stu_page) is not None
      or 'nav-group open" data-group="students"' in stu_page)
dup_page = admin.get("/admin/duplicates").get_data(as_text=True)
check("Duplicate Records active on duplicates page",
      "Duplicate Records" in dup_page
      and re.search(r'class="active"[^>]*>.*?Duplicate Records', nav_of(dup_page), re.S) is not None)
tc_page = admin.get("/admin/thesis-cases").get_data(as_text=True)
check("Thesis Cases active + academic group open",
      re.search(r'class="active"[^>]*>.*?Thesis Cases', nav_of(tc_page), re.S) is not None
      and 'nav-group open" data-group="academic"' in tc_page)
check("Dashboard active on dashboard, no group open",
      re.search(r'id="nav-dashboard"[^>]*class="active"', dash) is not None
      and 'nav-group open' not in dash)

# ---- 7. collapsed mode wiring present ----
for needle in ["sideCollapseBtn", "slim-pop", "side-collapsed", "das-side-collapsed",
               "nav-group-title", "tb-collapse-btn"]:
    check("collapsed wiring: " + needle, needle in dash)
check("mobile drawer toggle present", 'id="sideToggle"' in dash)

# ---- 8. permissions: officer sees no Users / Backup links ----
admin.post("/admin/users", data={"name": "Officer One", "email": "officer@test.pk",
                                "password": "officer123", "role": "DAS Officer"})
officer = app.test_client()
officer.post("/admin", data={"email": "officer@test.pk", "password": "officer123"})
officer_dash = officer.get("/admin/dashboard").get_data(as_text=True)
officer_nav = nav_of(officer_dash)
check("officer: no User Management link", "User Management" not in officer_nav)
check("officer: no Backup & Restore link", "Backup & Restore" not in officer_nav)
check("officer: still sees Notifications + Reports",
      "Notifications" in officer_nav and "Reports" in officer_nav)
check("officer blocked from Users page",
      officer.get("/admin/users").status_code in (302, 403))

# ---- 9. polish (2026-11-26): wider sidebar, single-line text, old solid-blue active ----
compiled = pathlib.Path(REPO, "static/css/tailwind.css").read_text()
src = pathlib.Path(REPO, "static/css/tailwind-input.css").read_text()
check("sidebar width 288px", "288px" in compiled and "252px" not in compiled)
check("group titles single-line (nowrap)", "white-space: nowrap" in compiled)
check("active state back to solid blue",
      ".side-nav a.active { @apply bg-primary text-white shadow-md; }" in src
      and "a.active::before" not in src)

print(f"SIDEBAR NAV OK — {N[0]}/{N[0]} checks passed")
