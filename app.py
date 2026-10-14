"""
DAS Student Data Management System (simple edition)
---------------------------------------------------
Simple student-record portal for a university Advanced Studies office.

- Admin: students list (12 columns), filters + search, complete student
  profiles, Excel import/export, document management.
- Students: view own complete profile, change password, upload their
  complete file (single merged PDF) from a checklist page.

Run locally:  python3 app.py        (uses SQLite, students.db)
Deploy on Vercel: set DATABASE_URL env var to a Postgres URL
  (e.g. Neon via Vercel's one-click Postgres integration).
"""
import io
import os
import re
import sqlite3
from datetime import datetime, date
from functools import wraps

from flask import (Flask, Response, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "students.db")
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_PG = bool(DATABASE_URL)

app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, "templates"),
            static_folder=os.path.join(BASE_DIR, "static"))
app.secret_key = os.environ.get("PORTAL_SECRET", "das-student-portal-dev-key")
app.config["MAX_CONTENT_LENGTH"] = 26 * 1024 * 1024  # 26 MB (PDF uploads up to ~25 MB)

UNI_NAME = "Advanced Studies Office"
PORTAL_TITLE = "Student Data Management System"

STATUS_LIST = ["Continue", "Freeze", "Left", "Admission Cancelled",
               "Viva Done", "Viva Pending", "Defense Pending", "Pass Out"]
FOLDER_OPTS = ["Completed", "Not Completed"]
RECEIVED_OPTS = ["Received", "Not Received"]
GENDER_OPTS = ["Male", "Female"]

MPHIL_DOCS = [
    "Checklist",
    "GS-01 Forms for All Semesters",
    "GS-02 \u2013 Coursework Completion Form",
    "GS-03 \u2013 Progress Report",
    "GS-04 Form \u2013 With Thesis Submission Fee Paid Challan",
    "Approved Synopsis",
    "Final Thesis (Hardbound Copies)",
]

PHD_DOCS = [
    "Checklist",
    "GS-01 Forms for All Semesters",
    "GS-02 Coursework",
    "GS-03 Form",
    "GS-04 Form",
    "GS-05 Approved Synopsis",
    "GS-06 Certificate for Approval Form",
    "GS-07 Plagiarism Undertaking Form",
    "All Paid Challan(s)",
    "Final Thesis Copy",
    "Form for Semi-Final Thesis Evaluation",
    "Certificate for Plagiarism Checking",
    "Thesis Observation Form",
    "PDF Version of PhD Thesis on CD",
    "PhD Transcript",
    "Teaching Assistantship Notification",
    "Ethical Certificate (if required)",
    "Plagiarism Report Verified by Supervisor and QEC",
]

# Import headers (normalised) -> student field
COLUMN_MAP = {
    "cnic": ["cnic", "cnicno", "cnicnumber", "nic"],
    "name": ["name", "studentname", "candidatename", "fullname"],
    "father_name": ["fathername", "fathersname", "father", "so", "dofather"],
    "reg_no": ["registrationno", "regno", "registrationnumber", "regnumber",
               "regn", "registrationn"],
    "gender": ["gender", "sex"],
    "degree": ["degree", "program", "programme", "applieddegree", "discipline"],
    "session": ["session", "batch"],
    "folder_file": ["folderfilewithgsforms", "folderfile", "folder", "file",
                    "folderfilestatus", "folderwithgsforms"],
    "comments_missing": ["commentsmissing", "comments", "missing",
                         "missingcomments", "remarks", "comment"],
    "synopsis": ["synopsis"],
    "semi_final_thesis": ["semifinalthesis", "semifinal", "semifinalthesisevaluation"],
    "final_thesis": ["finalthesis", "finalthesisreceived", "thesis"],
    "thesis_forward_coe": ["thesisforwardcoe", "thesisforward", "coeforwarddate",
                           "forwarddate", "forwardedtocoe", "thesisforwarddate"],
    "status": ["status", "studentstatus"],
}

TEMPLATE_HEADERS = ["CNIC", "Name", "Father Name", "Registration No.", "Gender",
                    "Degree", "Session", "Folder/File (With GS-Forms)",
                    "Comments (Missing)", "Synopsis", "Semi-Final Thesis",
                    "Final Thesis", "Thesis Forward (COE)", "Status"]


def docs_for_degree(degree):
    """PhD students see the 18-item list, everyone else the MPhil 7-item list."""
    if "phd" in (degree or "").lower():
        return PHD_DOCS
    return MPHIL_DOCS


# ---------------- database (SQLite locally, Postgres on Vercel) ----------------
class DB:
    """Tiny wrapper: same `?` placeholders work on sqlite3 and psycopg2."""

    def __init__(self):
        if USE_PG:
            import psycopg2
            self.conn = psycopg2.connect(DATABASE_URL)
            self.pg = True
        else:
            self.conn = sqlite3.connect(DB_PATH)
            self.conn.row_factory = sqlite3.Row
            self.pg = False
        self._cur = None

    def _sql(self, sql):
        return sql.replace("?", "%s") if self.pg else sql

    def execute(self, sql, params=()):
        if self.pg:
            import psycopg2.extras
            cur = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(self._sql(sql), params)
            self._cur = cur
            return cur
        return self.conn.execute(sql, params)

    def fetchone(self, sql, params=()):
        cur = self.execute(sql, params)
        row = cur.fetchone()
        if self.pg:
            cur.close()
        return row

    def fetchall(self, sql, params=()):
        cur = self.execute(sql, params)
        rows = cur.fetchall()
        if self.pg:
            cur.close()
        return rows

    def commit(self):
        self.conn.commit()

    def close(self):
        try:
            if self._cur is not None:
                self._cur.close()
        except Exception:
            pass
        self.conn.close()


def get_db():
    if "db" not in g:
        g.db = DB()
    return g.db


@app.teardown_appcontext
def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = DB()
    id_pg = "id SERIAL PRIMARY KEY"
    id_lite = "id INTEGER PRIMARY KEY AUTOINCREMENT"
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS students (
            {id_pg if USE_PG else id_lite},
            cnic TEXT UNIQUE,
            name TEXT NOT NULL,
            father_name TEXT,
            reg_no TEXT NOT NULL UNIQUE,
            gender TEXT,
            degree TEXT,
            session TEXT,
            folder_file TEXT DEFAULT 'Not Completed',
            comments_missing TEXT DEFAULT 'N/A',
            synopsis TEXT DEFAULT 'Not Received',
            semi_final_thesis TEXT DEFAULT 'Not Received',
            final_thesis TEXT DEFAULT 'Not Received',
            thesis_forward_coe {"DATE" if USE_PG else "TEXT"},
            status TEXT DEFAULT 'Continue',
            password_hash TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS documents (
            {id_pg if USE_PG else id_lite},
            student_id INTEGER NOT NULL REFERENCES students(id),
            doc_label TEXT,
            file_name TEXT,
            mime_type TEXT,
            file_data {"BYTEA" if USE_PG else "BLOB"},
            uploaded_by TEXT,
            uploaded_at TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # Admin password comes from the env var (source of truth), stored hashed.
    pw_hash = generate_password_hash(
        os.environ.get("ADMIN_PASSWORD", "admin123") or "admin123")
    if USE_PG:
        db.execute("INSERT INTO settings(key, value) VALUES (?, ?)"
                   " ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
                   ("admin_pw", pw_hash))
    else:
        db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                   ("admin_pw", pw_hash))
    db.commit()
    db.close()


_db_ready = False


@app.before_request
def _ensure_db():
    # On Vercel there is no startup hook; create tables lazily on first request.
    global _db_ready
    if not _db_ready:
        try:
            init_db()
        except Exception:
            # Concurrent cold starts can race on CREATE TABLE; the loser
            # gets a duplicate-object error which is safe to ignore.
            pass
        _db_ready = True


# Vercel forwards every request to the serverless function (api/index.py),
# so Flask may see PATH_INFO like "/api/index" or "/api/index/student/..."
# instead of the real route. Strip that prefix so routing works the same
# locally and on Vercel. No-op when the prefix is absent.
_VERCEL_PREFIX = "/api/index"
_orig_wsgi_app = app.wsgi_app


def _strip_vercel_prefix(environ, start_response):
    path = environ.get("PATH_INFO", "") or ""
    if path == _VERCEL_PREFIX:
        environ["PATH_INFO"] = "/"
    elif path.startswith(_VERCEL_PREFIX + "/"):
        environ["PATH_INFO"] = path[len(_VERCEL_PREFIX):]
    return _orig_wsgi_app(environ, start_response)


app.wsgi_app = _strip_vercel_prefix


# ---------------- helpers ----------------
def norm_cnic(raw):
    return re.sub(r"\D", "", raw or "")


def row_dict(row):
    return dict(row) if row is not None else None


_DATE_FORMATS = ("%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y",
                 "%m/%d/%Y", "%b %d, %Y", "%d.%m.%Y", "%Y/%m/%d")


def parse_coe(raw):
    """Anything date-like -> 'YYYY-MM-DD', else ''."""
    if raw is None:
        return ""
    if isinstance(raw, datetime):
        raw = raw.date()
    if isinstance(raw, date):
        return raw.isoformat()
    s = str(raw).strip()
    if not s:
        return ""
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            continue
    return ""


def fmt_coe(val):
    """DB value (date/str/None) -> 'May 10, 2026' or em dash."""
    if val is None:
        return "\u2014"
    if isinstance(val, datetime):
        val = val.date()
    if isinstance(val, date):
        return val.strftime("%b %d, %Y")
    s = str(val).strip()
    if not s:
        return "\u2014"
    iso = parse_coe(s)
    if iso:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%b %d, %Y")
    return s


def pick(val, options, default):
    v = (val or "").strip()
    for o in options:
        if v.lower() == o.lower():
            return o
    return default


def header_key(header):
    h = re.sub(r"[^a-z0-9]", "", str(header or "").strip().lower())
    for field, aliases in COLUMN_MAP.items():
        if h in aliases:
            return field
    return None


def fmt_dt(s):
    return s or ""


def doc_bytes(row):
    data = row.get("file_data")
    if isinstance(data, memoryview):
        data = data.tobytes()
    return bytes(data or b"")


def safe_filename(name):
    return re.sub(r"[^\w.\- ]", "_", (name or "document").strip()) or "document"


def check_admin_password(pw):
    row = get_db().fetchone("SELECT value FROM settings WHERE key='admin_pw'")
    if not row:
        return False
    h = row["value"] if isinstance(row, dict) else row[0]
    try:
        return check_password_hash(h, pw or "")
    except Exception:
        return False


def query_students(args):
    """Apply the dashboard filters; returns list of dicts."""
    where, params = [], []
    q = (args.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        op = "ILIKE" if USE_PG else "LIKE"
        where.append(f"(name {op} ? OR reg_no {op} ? OR cnic {op} ?)")
        params += [like, like, like]
    for key in ("session", "degree", "status", "gender", "folder_file"):
        v = (args.get(key) or "").strip()
        if v:
            where.append(f"{key}=?")
            params.append(v)
    sql = "SELECT * FROM students"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY name"
    return [dict(r) for r in get_db().fetchall(sql, params)]


def distinct_values(col):
    rows = get_db().fetchall(
        f"SELECT DISTINCT {col} FROM students WHERE {col} IS NOT NULL AND {col}<>'' ORDER BY {col}")
    return [(r[col] if isinstance(r, dict) else r[0]) for r in rows]


# ---------------- public ----------------
@app.route("/")
def index():
    return render_template("index.html", uni=UNI_NAME, title=PORTAL_TITLE)


# ---------------- admin ----------------
def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return f(*a, **kw)
    return wrapper


@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if session.get("admin"):
        return redirect(url_for("admin_dashboard"))
    error = None
    if request.method == "POST":
        if check_admin_password(request.form.get("password")):
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))
        error = "Wrong password."
    return render_template("admin_login.html", uni=UNI_NAME, title=PORTAL_TITLE,
                           error=error)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin_login"))


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    students = query_students(request.args)
    for s in students:
        s["coe_display"] = fmt_coe(s.get("thesis_forward_coe"))
    total = get_db().fetchone("SELECT COUNT(*) AS n FROM students")
    total = (total["n"] if isinstance(total, dict) else total[0]) if total else 0
    stat_rows = get_db().fetchall(
        "SELECT status, COUNT(*) AS n FROM students GROUP BY status")
    stats = {}
    for r in stat_rows:
        r = dict(r)
        stats[r.get("status") or "Continue"] = r["n"]
    qs = request.query_string.decode()
    export_url = url_for("admin_export") + ("?" + qs if qs else "")
    return render_template(
        "admin_dashboard.html", uni=UNI_NAME, title=PORTAL_TITLE,
        students=students, shown=len(students), total=total,
        stats=stats, status_list=STATUS_LIST,
        sessions=distinct_values("session"), degrees=distinct_values("degree"),
        genders=GENDER_OPTS, folders=FOLDER_OPTS,
        f=request.args, export_url=export_url)


@app.route("/admin/export")
@admin_required
def admin_export():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    students = query_students(request.args)
    wb = Workbook()
    ws = wb.active
    ws.title = "Students"
    headers = ["Name", "Reg.No", "Gender", "Degree", "Session", "Folder/File",
               "Comments (Missing)", "Synopsis", "Semi-Final Thesis",
               "Final Thesis", "Thesis Forward (COE)", "Status"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2D3290")
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
    for s in students:
        ws.append([s.get("name"), s.get("reg_no"), s.get("gender"),
                   s.get("degree"), s.get("session"), s.get("folder_file"),
                   s.get("comments_missing"), s.get("synopsis"),
                   s.get("semi_final_thesis"), s.get("final_thesis"),
                   parse_coe(s.get("thesis_forward_coe")) or "",
                   s.get("status")])
    widths = [24, 16, 10, 22, 12, 14, 30, 12, 14, 14, 18, 16]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = Response(buf.getvalue(),
                    mimetype="application/vnd.openxmlformats-officedocument."
                             "spreadsheetml.sheet")
    resp.headers["Content-Disposition"] = \
        "attachment; filename=das_students_export.xlsx"
    return resp


@app.route("/admin/import")
@admin_required
def admin_import():
    return render_template("admin_import.html", uni=UNI_NAME, title=PORTAL_TITLE,
                           status_list=STATUS_LIST)


@app.route("/admin/import/template")
@admin_required
def admin_import_template():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "Students"
    ws.append(TEMPLATE_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2D3290")
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
    ws.append(["3520212345671", "Ayesha Raza", "Muhammad Raza", "24-DAS-0101",
               "Female", "MPhil Biochemistry", "F. 23-25", "Completed", "N/A",
               "Received", "Received", "Not Received", "2026-05-10", "Continue"])
    widths = [18, 22, 22, 16, 10, 22, 12, 18, 30, 12, 14, 14, 18, 16]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = Response(buf.getvalue(),
                    mimetype="application/vnd.openxmlformats-officedocument."
                             "spreadsheetml.sheet")
    resp.headers["Content-Disposition"] = \
        "attachment; filename=das_students_import_template.xlsx"
    return resp


def read_xlsx(file_storage):
    """Read an .xlsx straight from the upload stream (no disk writes)."""
    from openpyxl import load_workbook
    data = file_storage.read()
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.values)
    if not rows:
        return []
    headers = [str(h) if h is not None else "" for h in rows[0]]
    return [dict(zip(headers, r)) for r in rows[1:] if any(
        v is not None and str(v).strip() != "" for v in r)]


@app.route("/admin/import/upload", methods=["POST"])
@admin_required
def admin_import_upload():
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Please choose a file to upload.")
        return redirect(url_for("admin_import"))
    if not file.filename.lower().endswith(".xlsx"):
        flash("Only Excel (.xlsx) files are allowed.")
        return redirect(url_for("admin_import"))
    try:
        raw_rows = read_xlsx(file)
    except Exception as e:
        flash(f"Could not read file: {e}")
        return redirect(url_for("admin_import"))
    if not raw_rows:
        flash("No data rows found in the file.")
        return redirect(url_for("admin_import"))

    mapping = {}
    for hdr in raw_rows[0].keys():
        k = header_key(hdr)
        if k and k not in mapping.values():
            mapping[hdr] = k
    if "name" not in mapping.values() or "reg_no" not in mapping.values():
        flash("File must contain at least a Name column and a Registration No. column.")
        return redirect(url_for("admin_import"))

    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cols = ["cnic", "name", "father_name", "reg_no", "gender", "degree",
            "session", "folder_file", "comments_missing", "synopsis",
            "semi_final_thesis", "final_thesis", "thesis_forward_coe",
            "status", "password_hash", "created_at", "updated_at"]
    placeholders = ", ".join(["?"] * len(cols))
    upd = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols
                    if c not in ("reg_no", "password_hash"))
    upsert = (f"INSERT INTO students ({', '.join(cols)}) VALUES ({placeholders})"
              f" ON CONFLICT(reg_no) DO UPDATE SET {upd}")
    inserted = updated = skipped = 0
    skip_notes = []
    for i, rr in enumerate(raw_rows, start=2):
        rec = {}
        for hdr, val in rr.items():
            field = mapping.get(hdr)
            if not field:
                continue
            if isinstance(val, datetime):
                val = val.date().isoformat()
            elif isinstance(val, date):
                val = val.isoformat()
            rec[field] = str(val).strip() if val is not None else ""
        name = rec.get("name", "")
        reg_no = rec.get("reg_no", "")
        cnic = norm_cnic(rec.get("cnic", ""))
        if not name or not reg_no or not cnic:
            skipped += 1
            if len(skip_notes) < 5:
                skip_notes.append(f"row {i} (missing Name / Reg.No / CNIC)")
            continue
        rec["name"] = name
        rec["reg_no"] = reg_no
        rec["cnic"] = cnic
        rec["folder_file"] = pick(rec.get("folder_file"), FOLDER_OPTS, "Not Completed")
        rec["synopsis"] = pick(rec.get("synopsis"), RECEIVED_OPTS, "Not Received")
        rec["semi_final_thesis"] = pick(rec.get("semi_final_thesis"), RECEIVED_OPTS, "Not Received")
        rec["final_thesis"] = pick(rec.get("final_thesis"), RECEIVED_OPTS, "Not Received")
        rec["status"] = pick(rec.get("status"), STATUS_LIST, "Continue")
        cm = rec.get("comments_missing", "").strip()
        rec["comments_missing"] = cm if cm else "N/A"
        rec["thesis_forward_coe"] = parse_coe(rec.get("thesis_forward_coe")) or None
        exists = db.fetchone("SELECT id FROM students WHERE reg_no=?", (reg_no,))
        values = tuple(rec.get(c) for c in cols[:-3]) + (
            generate_password_hash(cnic), now, now)
        try:
            db.execute(upsert, values)
            if exists:
                updated += 1
            else:
                inserted += 1
        except Exception:
            skipped += 1
            if len(skip_notes) < 5:
                skip_notes.append(f"row {i} ({reg_no}: save failed, maybe duplicate CNIC)")
    db.commit()
    msg = (f"Import complete: {inserted} new, {updated} updated, {skipped} skipped.")
    if skip_notes:
        msg += " Skipped: " + "; ".join(skip_notes) + ("." if skipped <= 5 else "...")
    flash(msg)
    return redirect(url_for("admin_dashboard"))


def get_student(sid):
    row = get_db().fetchone("SELECT * FROM students WHERE id=?", (sid,))
    return row_dict(row)


def get_docs(sid):
    rows = get_db().fetchall(
        "SELECT id, student_id, doc_label, file_name, mime_type, uploaded_by,"
        " uploaded_at, LENGTH(file_data) AS size FROM documents"
        " WHERE student_id=? ORDER BY uploaded_at DESC, id DESC", (sid,))
    return [dict(r) for r in rows]


@app.route("/admin/student/new", methods=["GET", "POST"])
@admin_required
def admin_student_new():
    if request.method == "POST":
        f = request.form
        name = f.get("name", "").strip()
        reg_no = f.get("reg_no", "").strip()
        cnic = norm_cnic(f.get("cnic", ""))
        if not name or not reg_no or not cnic:
            flash("Name, Registration No. and CNIC are required.")
            return render_template("student_form.html", uni=UNI_NAME,
                                   title=PORTAL_TITLE, form=f,
                                   status_list=STATUS_LIST,
                                   genders=GENDER_OPTS, folders=FOLDER_OPTS,
                                   received=RECEIVED_OPTS)
        db = get_db()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        try:
            db.execute(
                """INSERT INTO students (cnic, name, father_name, reg_no, gender,
                   degree, session, folder_file, comments_missing, synopsis,
                   semi_final_thesis, final_thesis, thesis_forward_coe, status,
                   password_hash, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (cnic, name, f.get("father_name", "").strip(), reg_no,
                 f.get("gender", "").strip(), f.get("degree", "").strip(),
                 f.get("session", "").strip(),
                 pick(f.get("folder_file"), FOLDER_OPTS, "Not Completed"),
                 f.get("comments_missing", "").strip() or "N/A",
                 pick(f.get("synopsis"), RECEIVED_OPTS, "Not Received"),
                 pick(f.get("semi_final_thesis"), RECEIVED_OPTS, "Not Received"),
                 pick(f.get("final_thesis"), RECEIVED_OPTS, "Not Received"),
                 parse_coe(f.get("thesis_forward_coe")) or None,
                 pick(f.get("status"), STATUS_LIST, "Continue"),
                 generate_password_hash(cnic), now, now))
            db.commit()
        except Exception as e:
            flash(f"Could not save (maybe duplicate Reg.No or CNIC): {e}")
            return render_template("student_form.html", uni=UNI_NAME,
                                   title=PORTAL_TITLE, form=f,
                                   status_list=STATUS_LIST,
                                   genders=GENDER_OPTS, folders=FOLDER_OPTS,
                                   received=RECEIVED_OPTS)
        flash(f"Student {name} added. Default portal password: CNIC digits.")
        row = db.fetchone("SELECT id FROM students WHERE reg_no=?", (reg_no,))
        return redirect(url_for("admin_student_profile", sid=row["id"] if isinstance(row, dict) else row[0]))
    return render_template("student_form.html", uni=UNI_NAME, title=PORTAL_TITLE,
                           form={}, status_list=STATUS_LIST,
                           genders=GENDER_OPTS, folders=FOLDER_OPTS,
                           received=RECEIVED_OPTS)


@app.route("/admin/student/<int:sid>")
@admin_required
def admin_student_profile(sid):
    s = get_student(sid)
    if not s:
        return render_template("notfound.html", uni=UNI_NAME, title=PORTAL_TITLE,
                               message="Student not found."), 404
    s["coe_display"] = fmt_coe(s.get("thesis_forward_coe"))
    s["coe_iso"] = parse_coe(s.get("thesis_forward_coe"))
    docs = get_docs(sid)
    return render_template("student_profile.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, s=s, docs=docs,
                           status_list=STATUS_LIST, genders=GENDER_OPTS,
                           folders=FOLDER_OPTS, received=RECEIVED_OPTS)


@app.route("/admin/student/<int:sid>/save", methods=["POST"])
@admin_required
def admin_student_save(sid):
    s = get_student(sid)
    if not s:
        return "Student not found.", 404
    f = request.form
    name = f.get("name", "").strip()
    reg_no = f.get("reg_no", "").strip()
    cnic = norm_cnic(f.get("cnic", ""))
    if not name or not reg_no or not cnic:
        flash("Name, Registration No. and CNIC are required.")
        return redirect(url_for("admin_student_profile", sid=sid))
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        db.execute(
            """UPDATE students SET cnic=?, name=?, father_name=?, reg_no=?,
               gender=?, degree=?, session=?, folder_file=?, comments_missing=?,
               synopsis=?, semi_final_thesis=?, final_thesis=?,
               thesis_forward_coe=?, status=?, updated_at=?
               WHERE id=?""",
            (cnic, name, f.get("father_name", "").strip(), reg_no,
             f.get("gender", "").strip(), f.get("degree", "").strip(),
             f.get("session", "").strip(),
             pick(f.get("folder_file"), FOLDER_OPTS, "Not Completed"),
             f.get("comments_missing", "").strip() or "N/A",
             pick(f.get("synopsis"), RECEIVED_OPTS, "Not Received"),
             pick(f.get("semi_final_thesis"), RECEIVED_OPTS, "Not Received"),
             pick(f.get("final_thesis"), RECEIVED_OPTS, "Not Received"),
             parse_coe(f.get("thesis_forward_coe")) or None,
             pick(f.get("status"), STATUS_LIST, "Continue"),
             now, sid))
        db.commit()
    except Exception as e:
        flash(f"Could not save (maybe duplicate Reg.No or CNIC): {e}")
        return redirect(url_for("admin_student_profile", sid=sid))
    flash("Student record updated.")
    return redirect(url_for("admin_student_profile", sid=sid))


@app.route("/admin/student/<int:sid>/delete", methods=["POST"])
@admin_required
def admin_student_delete(sid):
    db = get_db()
    db.execute("DELETE FROM documents WHERE student_id=?", (sid,))
    db.execute("DELETE FROM students WHERE id=?", (sid,))
    db.commit()
    flash("Student and their documents deleted.")
    return redirect(url_for("admin_dashboard"))


ADMIN_DOC_EXTS = {"pdf", "doc", "docx", "jpg", "jpeg", "png"}
MIME_BY_EXT = {"pdf": "application/pdf",
               "doc": "application/msword",
               "docx": "application/vnd.openxmlformats-officedocument."
                       "wordprocessingml.document",
               "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}


@app.route("/admin/student/<int:sid>/upload-doc", methods=["POST"])
@admin_required
def admin_student_upload_doc(sid):
    s = get_student(sid)
    if not s:
        return "Student not found.", 404
    label = request.form.get("doc_label", "").strip() or "Document"
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Please choose a file to upload.")
        return redirect(url_for("admin_student_profile", sid=sid))
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ADMIN_DOC_EXTS:
        flash("Allowed file types: PDF, DOC, DOCX, JPG, PNG.")
        return redirect(url_for("admin_student_profile", sid=sid))
    data = file.read()
    db = get_db()
    db.execute(
        """INSERT INTO documents (student_id, doc_label, file_name, mime_type,
           file_data, uploaded_by, uploaded_at)
           VALUES (?, ?, ?, ?, ?, 'admin', ?)""",
        (sid, label, secure_filename(file.filename), MIME_BY_EXT.get(ext),
         data, datetime.now().strftime("%Y-%m-%d %H:%M")))
    db.commit()
    flash(f"Document '{label}' saved to {s['name']}'s record.")
    return redirect(url_for("admin_student_profile", sid=sid))


@app.route("/admin/doc/<int:doc_id>/download")
@admin_required
def admin_doc_download(doc_id):
    row = get_db().fetchone("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not row:
        return "Document not found.", 404
    r = dict(row)
    return Response(doc_bytes(r),
                    mimetype=r.get("mime_type") or "application/octet-stream",
                    headers={"Content-Disposition":
                             f'attachment; filename="{safe_filename(r.get("file_name"))}"'})


@app.route("/admin/doc/<int:doc_id>/delete", methods=["POST"])
@admin_required
def admin_doc_delete(doc_id):
    row = get_db().fetchone("SELECT student_id FROM documents WHERE id=?", (doc_id,))
    sid = (row["student_id"] if isinstance(row, dict) else row[0]) if row else None
    get_db().execute("DELETE FROM documents WHERE id=?", (doc_id,))
    get_db().commit()
    flash("Document deleted.")
    return redirect(url_for("admin_student_profile", sid=sid)) if sid \
        else redirect(url_for("admin_dashboard"))


# ---------------- student portal ----------------
def student_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("student_id"):
            return redirect(url_for("student_login"))
        return f(*a, **kw)
    return wrapper


def current_student():
    return get_student(session.get("student_id"))


@app.route("/student", methods=["GET", "POST"])
def student_login():
    if session.get("student_id"):
        return redirect(url_for("student_dashboard"))
    error = None
    if request.method == "POST":
        reg_no = request.form.get("reg_no", "").strip()
        pw = request.form.get("password", "")
        row = get_db().fetchone("SELECT * FROM students WHERE reg_no=?", (reg_no,))
        s = row_dict(row)
        if s and s.get("password_hash") and check_password_hash(s["password_hash"], pw):
            session["student_id"] = s["id"]
            return redirect(url_for("student_dashboard"))
        error = "Wrong Registration No. or password."
    return render_template("student_login.html", uni=UNI_NAME, title=PORTAL_TITLE,
                           error=error)


@app.route("/student/logout")
def student_logout():
    session.pop("student_id", None)
    return redirect(url_for("student_login"))


@app.route("/student/dashboard")
@student_required
def student_dashboard():
    s = current_student()
    if not s:
        session.pop("student_id", None)
        return redirect(url_for("student_login"))
    s["coe_display"] = fmt_coe(s.get("thesis_forward_coe"))
    docs = get_docs(s["id"])
    return render_template("student_dashboard.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, s=s, docs=docs)


@app.route("/student/change-password", methods=["POST"])
@student_required
def student_change_password():
    s = current_student()
    cur = request.form.get("current", "")
    new = request.form.get("new", "")
    confirm = request.form.get("confirm", "")
    if not (s and s.get("password_hash") and check_password_hash(s["password_hash"], cur)):
        flash("Current password is wrong.")
    elif len(new) < 6:
        flash("New password must be at least 6 characters.")
    elif new != confirm:
        flash("New passwords do not match.")
    else:
        get_db().execute("UPDATE students SET password_hash=?, updated_at=?"
                         " WHERE id=?",
                         (generate_password_hash(new),
                          datetime.now().strftime("%Y-%m-%d %H:%M"), s["id"]))
        get_db().commit()
        flash("Password changed.")
    return redirect(url_for("student_dashboard"))


@app.route("/student/upload", methods=["GET", "POST"])
@student_required
def student_upload():
    s = current_student()
    if not s:
        session.pop("student_id", None)
        return redirect(url_for("student_login"))
    doc_list = docs_for_degree(s.get("degree"))
    is_phd = "phd" in (s.get("degree") or "").lower()
    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Please choose a PDF file to upload.")
        elif not file.filename.lower().endswith(".pdf"):
            flash("Only PDF files are allowed.")
        else:
            data = file.read()
            if not data.startswith(b"%PDF"):
                flash("This file does not look like a valid PDF.")
            else:
                db = get_db()
                db.execute(
                    """INSERT INTO documents (student_id, doc_label, file_name,
                       mime_type, file_data, uploaded_by, uploaded_at)
                       VALUES (?, 'Complete File (Merged PDF)', ?, 'application/pdf',
                       ?, 'student', ?)""",
                    (s["id"], secure_filename(file.filename), data,
                     datetime.now().strftime("%Y-%m-%d %H:%M")))
                db.commit()
                flash("Your complete file has been uploaded and saved to your "
                      "office record. You can upload again if needed \u2014 old "
                      "versions are kept.")
        return redirect(url_for("student_upload"))
    uploads = [d for d in get_docs(s["id"]) if d.get("uploaded_by") == "student"]
    return render_template("student_upload.html", uni=UNI_NAME, title=PORTAL_TITLE,
                           s=s, doc_list=doc_list, is_phd=is_phd, uploads=uploads)


@app.route("/student/doc/<int:doc_id>/download")
@student_required
def student_doc_download(doc_id):
    row = get_db().fetchone(
        "SELECT * FROM documents WHERE id=? AND student_id=?",
        (doc_id, session.get("student_id")))
    if not row:
        return "Document not found.", 404
    r = dict(row)
    return Response(doc_bytes(r),
                    mimetype=r.get("mime_type") or "application/octet-stream",
                    headers={"Content-Disposition":
                             f'attachment; filename="{safe_filename(r.get("file_name"))}"'})


@app.errorhandler(404)
def page_not_found(_e):
    return render_template("notfound.html", uni=UNI_NAME, title=PORTAL_TITLE,
                           message="Page not found."), 404


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
