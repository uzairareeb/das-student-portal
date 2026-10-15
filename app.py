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
from urllib.parse import urlencode

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
            cnic TEXT,
            name TEXT NOT NULL,
            father_name TEXT,
            reg_no TEXT NOT NULL,
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
            department TEXT,
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
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS departments (
            {id_pg if USE_PG else id_lite},
            name TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT
        )
    """)
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS dept_uploads (
            {id_pg if USE_PG else id_lite},
            department_id INTEGER NOT NULL REFERENCES departments(id),
            file_name TEXT,
            file_data {"BYTEA" if USE_PG else "BLOB"},
            row_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            admin_comment TEXT,
            created_at TEXT,
            reviewed_at TEXT
        )
    """)
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS viva_records (
            {id_pg if USE_PG else id_lite},
            student_id INTEGER NOT NULL REFERENCES students(id),
            examiner_name TEXT,
            examiner_affiliation TEXT,
            viva_date TEXT,
            result TEXT DEFAULT 'Pending',
            remarks TEXT,
            updated_by TEXT,
            created_at TEXT
        )
    """)
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS activity_log (
            {id_pg if USE_PG else id_lite},
            actor TEXT,
            action TEXT,
            detail TEXT,
            created_at TEXT
        )
    """)
    _migrate_drop_unique_constraints(db)
    _migrate_add_department_column(db)
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


def _migrate_drop_unique_constraints(db):
    """Oct 2026: imports must keep every file row as its own record --
    neither CNIC nor Reg.No is UNIQUE anymore. Drops the old unique
    constraints (Postgres) or rebuilds the table without them (SQLite).
    Safe to rerun."""
    try:
        if USE_PG:
            db.execute("ALTER TABLE students DROP CONSTRAINT IF EXISTS students_cnic_key")
            db.execute("ALTER TABLE students DROP CONSTRAINT IF EXISTS students_reg_no_key")
            return
        row = db.fetchone(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='students'")
        ddl = (row["sql"] if isinstance(row, dict) else row[0]) if row else ""
        norm = " ".join(ddl.split())
        if "cnic TEXT UNIQUE" not in norm and "reg_no TEXT NOT NULL UNIQUE" not in norm:
            return
        db.execute("DROP TABLE IF EXISTS students_new")
        old_cols = [r[1] for r in db.fetchall("PRAGMA table_info(students)")]
        new_cols = ["id", "cnic", "name", "father_name", "reg_no", "gender",
                    "degree", "session", "folder_file", "comments_missing",
                    "synopsis", "semi_final_thesis", "final_thesis",
                    "thesis_forward_coe", "status", "department",
                    "password_hash", "created_at", "updated_at"]
        cols = [c for c in new_cols if c in old_cols]
        colstr = ", ".join(cols)
        db.execute("""CREATE TABLE students_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cnic TEXT,
                name TEXT NOT NULL,
                father_name TEXT,
                reg_no TEXT NOT NULL,
                gender TEXT,
                degree TEXT,
                session TEXT,
                folder_file TEXT DEFAULT 'Not Completed',
                comments_missing TEXT DEFAULT 'N/A',
                synopsis TEXT DEFAULT 'Not Received',
                semi_final_thesis TEXT DEFAULT 'Not Received',
                final_thesis TEXT DEFAULT 'Not Received',
                thesis_forward_coe TEXT,
                status TEXT DEFAULT 'Continue',
                department TEXT,
                password_hash TEXT,
                created_at TEXT,
                updated_at TEXT
            )""")
        db.execute(f"INSERT INTO students_new ({colstr}) SELECT {colstr} FROM students")
        db.execute("DROP TABLE students")
        db.execute("ALTER TABLE students_new RENAME TO students")
    except Exception:
        # A migration must never break startup; worst case the old
        # constraint stays.
        try:
            db.conn.rollback()
        except Exception:
            pass


def _migrate_add_department_column(db):
    """Add students.department for department-portal uploads. Safe to rerun."""
    try:
        if USE_PG:
            db.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS department TEXT")
        else:
            cols = [r[1] for r in db.fetchall("PRAGMA table_info(students)")]
            if "department" not in cols:
                db.execute("ALTER TABLE students ADD COLUMN department TEXT")
        db.commit()
    except Exception:
        try:
            db.conn.rollback()
        except Exception:
            pass


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


def _student_where(args):
    """Shared WHERE clause for the dashboard filters."""
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
    return where, params


def count_students(args):
    where, params = _student_where(args)
    sql = "SELECT COUNT(*) AS n FROM students"
    if where:
        sql += " WHERE " + " AND ".join(where)
    row = get_db().fetchone(sql, params)
    return (row["n"] if isinstance(row, dict) else row[0]) if row else 0


def query_students(args, limit=None, offset=None):
    """Apply the dashboard filters; returns list of dicts."""
    where, params = _student_where(args)
    sql = "SELECT * FROM students"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY name"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset or 0]
    return [dict(r) for r in get_db().fetchall(sql, params)]


PER_PAGE = 20


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
            log_activity("admin", "login", "admin portal sign-in")
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
    """Overview only: stats, charts, recent activity. The full student
    list lives on its own dedicated page (/admin/students)."""
    db = get_db()
    total = count_students({})
    stat_rows = db.fetchall(
        "SELECT status, COUNT(*) AS n FROM students GROUP BY status")
    stats = {}
    for r in stat_rows:
        r = dict(r)
        stats[r.get("status") or "Continue"] = r["n"]
    deg_rows = db.fetchall(
        "SELECT degree, COUNT(*) AS n FROM students GROUP BY degree"
        " ORDER BY n DESC LIMIT 8")
    degrees = [(dict(r).get("degree") or "—", dict(r)["n"]) for r in deg_rows]
    ses_rows = db.fetchall(
        "SELECT session, COUNT(*) AS n FROM students GROUP BY session"
        " ORDER BY n DESC LIMIT 8")
    sessions = [(dict(r).get("session") or "—", dict(r)["n"]) for r in ses_rows]
    doc_total = db.fetchone("SELECT COUNT(*) AS n FROM documents")
    doc_total = (doc_total["n"] if isinstance(doc_total, dict)
                 else doc_total[0]) if doc_total else 0
    with_docs = db.fetchone(
        "SELECT COUNT(DISTINCT student_id) AS n FROM documents")
    with_docs = (with_docs["n"] if isinstance(with_docs, dict)
                 else with_docs[0]) if with_docs else 0
    recent = db.fetchall(
        "SELECT d.doc_label, d.file_name, d.uploaded_at, d.uploaded_by,"
        " s.name AS student_name, s.reg_no, d.id AS doc_id"
        " FROM documents d JOIN students s ON s.id=d.student_id"
        " ORDER BY d.id DESC LIMIT 5")
    recent = [dict(r) for r in recent]
    return render_template(
        "admin_dashboard.html", uni=UNI_NAME, title=PORTAL_TITLE,
        total=total, stats=stats, status_list=STATUS_LIST,
        status_counts=[stats.get(st, 0) for st in STATUS_LIST],
        degrees=degrees, sessions=sessions,
        doc_total=doc_total, with_docs=with_docs, recent=recent)


@app.route("/admin/students")
@admin_required
def admin_students():
    """Dedicated full student list page: filters, table, pagination,
    bulk actions, Excel/PDF export."""
    try:
        page = max(int(request.args.get("page", 1) or 1), 1)
    except (TypeError, ValueError):
        page = 1
    filtered_total = count_students(request.args)
    pages = max((filtered_total + PER_PAGE - 1) // PER_PAGE, 1)
    page = min(page, pages)
    students = query_students(request.args, limit=PER_PAGE,
                              offset=(page - 1) * PER_PAGE)
    for s in students:
        s["coe_display"] = fmt_coe(s.get("thesis_forward_coe"))
    total = count_students({})
    base_args = {k: v for k, v in request.args.items() if k != "page"}
    export_url = url_for("admin_export")
    export_pdf_url = url_for("admin_export_pdf")
    if base_args:
        qs = "?" + urlencode(base_args)
        export_url += qs
        export_pdf_url += qs

    def page_url(p):
        a = dict(base_args)
        a["page"] = p
        return url_for("admin_students") + "?" + urlencode(a)

    return render_template(
        "admin_students.html", uni=UNI_NAME, title=PORTAL_TITLE,
        students=students, shown=len(students), total=total,
        filtered_total=filtered_total, page=page, pages=pages,
        page_url=page_url, per_page=PER_PAGE,
        sessions=distinct_values("session"), degrees=distinct_values("degree"),
        genders=GENDER_OPTS, folders=FOLDER_OPTS, status_list=STATUS_LIST,
        f=request.args, export_url=export_url,
        export_pdf_url=export_pdf_url)


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


@app.route("/admin/export-pdf")
@admin_required
def admin_export_pdf():
    """Well-organized landscape PDF of the (filtered) student list."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                    Paragraph, Spacer)
    students = query_students(request.args)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=10 * mm, rightMargin=10 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title="DAS Students Export",
        author="Advanced Studies Office")
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title2", parent=styles["Title"],
                                 fontSize=16, textColor=colors.HexColor("#2d3290"),
                                 spaceAfter=4)
    sub_style = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=9,
                               textColor=colors.HexColor("#667085"), spaceAfter=10)
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=7,
                          leading=8.5)
    head_cell = ParagraphStyle("hcell", parent=styles["Normal"], fontSize=7,
                               leading=8.5, textColor=colors.white,
                               fontName="Helvetica-Bold")
    story = [Paragraph("Student Records — Advanced Studies Office", title_style),
             Paragraph(f"{len(students)} student(s) &middot; generated "
                       f"{datetime.now().strftime('%d %b %Y, %H:%M')}", sub_style)]
    headers = ["Name", "Reg.No", "Gender", "Degree", "Session", "Folder/File",
               "Comments", "Synopsis", "Semi-Final", "Final Thesis",
               "COE Date", "Status"]
    widths = [32 * mm, 26 * mm, 14 * mm, 34 * mm, 20 * mm, 22 * mm,
              34 * mm, 18 * mm, 18 * mm, 18 * mm, 20 * mm, 21 * mm]
    data = [[Paragraph(h, head_cell) for h in headers]]
    for s in students:
        data.append([
            Paragraph(s.get("name") or "—", cell),
            Paragraph(s.get("reg_no") or "—", cell),
            Paragraph(s.get("gender") or "—", cell),
            Paragraph(s.get("degree") or "—", cell),
            Paragraph(s.get("session") or "—", cell),
            Paragraph(s.get("folder_file") or "—", cell),
            Paragraph(s.get("comments_missing") or "—", cell),
            Paragraph(s.get("synopsis") or "—", cell),
            Paragraph(s.get("semi_final_thesis") or "—", cell),
            Paragraph(s.get("final_thesis") or "—", cell),
            Paragraph(fmt_coe(s.get("thesis_forward_coe")), cell),
            Paragraph(s.get("status") or "—", cell),
        ])
    tbl = Table(data, colWidths=widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2d3290")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d5d9e8")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f4f5fb")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f"Page <page> &middot; Advanced Studies Office — Student Data Management System",
        ParagraphStyle("foot", parent=styles["Normal"], fontSize=7,
                       textColor=colors.HexColor("#667085"),
                       alignment=1)))
    doc.build(story)
    buf.seek(0)
    resp = Response(buf.getvalue(), mimetype="application/pdf")
    resp.headers["Content-Disposition"] = \
        "attachment; filename=das_students_export.pdf"
    return resp


STUDENT_INSERT_COLS = ["cnic", "name", "father_name", "reg_no", "gender",
    "degree", "session", "folder_file", "comments_missing", "synopsis",
    "semi_final_thesis", "final_thesis", "thesis_forward_coe",
    "status", "department", "password_hash", "created_at", "updated_at"]


def log_activity(actor, action, detail=""):
    """Append to the activity log. Never breaks the request."""
    try:
        db = get_db()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        db.execute(
            "INSERT INTO activity_log (actor, action, detail, created_at)"
            " VALUES (?, ?, ?, ?)",
            (actor, action, (detail or "")[:500], now))
        db.commit()
    except Exception:
        pass


def parse_import_rows(file_storage):
    """Parse an .xlsx upload into student record value-tuples.

    Returns (records, skipped, skip_notes); records is a list of
    (row_no, reg_no, values) aligned with STUDENT_INSERT_COLS
    (department=None; the caller may override it).
    Raises ValueError on structural problems.
    """
    try:
        raw_rows = read_xlsx(file_storage)
    except Exception as e:
        raise ValueError(f"Could not read file: {e}")
    if not raw_rows:
        raise ValueError("No data rows found in the file.")
    mapping = {}
    for hdr in raw_rows[0].keys():
        k = header_key(hdr)
        if k and k not in mapping.values():
            mapping[hdr] = k
    if "name" not in mapping.values() or "reg_no" not in mapping.values():
        raise ValueError(
            "File must contain at least a Name column and a Registration No. column.")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    # Every file row becomes its own record (plain INSERT, no merging):
    # the file is imported exactly as-is, even with duplicate Reg.No/CNIC.
    records, skipped, skip_notes = [], 0, []
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
        if not name and not reg_no:
            skipped += 1
            if len(skip_notes) < 8:
                skip_notes.append(f"row {i}: blank row (no Name / Reg.No)")
            continue
        rec["name"] = name
        rec["reg_no"] = reg_no
        rec["cnic"] = cnic
        rec["folder_file"] = pick(rec.get("folder_file"), FOLDER_OPTS,
                                  "Not Completed")
        rec["synopsis"] = pick(rec.get("synopsis"), RECEIVED_OPTS,
                               "Not Received")
        rec["semi_final_thesis"] = pick(rec.get("semi_final_thesis"),
                                        RECEIVED_OPTS, "Not Received")
        rec["final_thesis"] = pick(rec.get("final_thesis"), RECEIVED_OPTS,
                                   "Not Received")
        rec["status"] = pick(rec.get("status"), STATUS_LIST, "Continue")
        cm = rec.get("comments_missing", "").strip()
        rec["comments_missing"] = cm if cm else "N/A"
        rec["thesis_forward_coe"] = parse_coe(rec.get("thesis_forward_coe")) or None
        rec["department"] = None
        # password_hash stays NULL: it is created lazily on the student's
        # first portal login (default password = CNIC digits). Hashing every
        # row up-front would take minutes for large files.
        values = tuple(rec.get(c) for c in STUDENT_INSERT_COLS[:-3]) + (None, now, now)
        records.append((i, reg_no, values))
    return records, skipped, skip_notes


def insert_student_records(db, records, department=None):
    """Batched savepoint-isolated plain inserts.

    Each batch is isolated in a savepoint; if it fails, its rows are
    retried one-by-one so a single bad row can neither poison the
    transaction nor abort the whole import.
    Returns (added, skipped, skip_notes, first_error).
    """
    cols = STUDENT_INSERT_COLS
    dep_idx = cols.index("department")
    if department is not None:
        records = [(rn, rg, tuple(department if j == dep_idx else v
                                  for j, v in enumerate(vals)))
                   for rn, rg, vals in records]
    one_ph = ", ".join(["?"] * len(cols))
    one_insert = f"INSERT INTO students ({', '.join(cols)}) VALUES ({one_ph})"

    def _one(vals):
        db.execute(one_insert, vals)

    def _chunk(chunk_vals):
        n = len(chunk_vals)
        ph = ", ".join(["(" + ", ".join(["?"] * len(cols)) + ")"] * n)
        sql = f"INSERT INTO students ({', '.join(cols)}) VALUES {ph}"
        flat = [v for vals in chunk_vals for v in vals]
        db.execute(sql, flat)

    added, skipped, skip_notes, first_error = 0, 0, [], ""
    sp_seq = [0]

    def _sp():
        sp_seq[0] += 1
        return f"imp_sp{sp_seq[0]}"

    def _rollback(sp):
        try:
            db.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        except Exception:
            pass

    CHUNK = 100
    for start in range(0, len(records), CHUNK):
        chunk = records[start:start + CHUNK]
        sp = _sp()
        try:
            db.execute(f"SAVEPOINT {sp}")
            _chunk([vals for _, _, vals in chunk])
            db.execute(f"RELEASE SAVEPOINT {sp}")
            added += len(chunk)
        except Exception:
            _rollback(sp)
            for row_no, reg_no, vals in chunk:
                sp2 = _sp()
                try:
                    db.execute(f"SAVEPOINT {sp2}")
                    _one(vals)
                    db.execute(f"RELEASE SAVEPOINT {sp2}")
                    added += 1
                except Exception as e2:
                    _rollback(sp2)
                    skipped += 1
                    if not first_error:
                        first_error = str(e2)[:160]
                    if len(skip_notes) < 8:
                        skip_notes.append(f"row {row_no} ({reg_no}): save failed")
    return added, skipped, skip_notes, first_error


def dept_required(view):
    @wraps(view)
    def wrapper(*a, **k):
        if not session.get("dept_id"):
            return redirect(url_for("dept_login"))
        return view(*a, **k)
    wrapper.__name__ = view.__name__
    return wrapper


def current_dept():
    did = session.get("dept_id")
    if not did:
        return None
    row = get_db().fetchone("SELECT * FROM departments WHERE id=?", (did,))
    return dict(row) if row else None


# ==================== DEPARTMENTS (admin-managed) ====================

@app.route("/admin/departments", methods=["GET", "POST"])
@admin_required
def admin_departments():
    """Create department portal logins. DAS shares the email + password
    with each department; the department then uploads its own data."""
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email or not password:
            flash("Department name, email and password are all required.")
        else:
            try:
                db.execute(
                    "INSERT INTO departments (name, email, password_hash,"
                    " created_at) VALUES (?, ?, ?, ?)",
                    (name, email, generate_password_hash(password),
                     datetime.now().strftime("%Y-%m-%d %H:%M")))
                db.commit()
                log_activity("admin", "department_add", f"{name} <{email}>")
                flash(f"Department '{name}' created. Share the email and"
                      f" password with them so they can sign in at /dept.")
            except Exception:
                flash("Could not create it — the name or email already exists.")
        return redirect(url_for("admin_departments"))
    depts = [dict(r) for r in db.fetchall(
        "SELECT d.*, (SELECT COUNT(*) FROM dept_uploads u"
        " WHERE u.department_id=d.id AND u.status='pending') AS pending_n"
        " FROM departments d ORDER BY d.name")]
    return render_template("admin_departments.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, depts=depts)


@app.route("/admin/department/<int:did>/delete", methods=["POST"])
@admin_required
def admin_department_delete(did):
    db = get_db()
    row = db.fetchone("SELECT * FROM departments WHERE id=?", (did,))
    if row:
        d = dict(row)
        db.execute("DELETE FROM dept_uploads WHERE department_id=?", (did,))
        db.execute("DELETE FROM departments WHERE id=?", (did,))
        db.commit()
        log_activity("admin", "department_delete",
                     f"{d['name']} <{d['email']}>")
        flash(f"Department '{d['name']}' and its uploads were deleted.")
    return redirect(url_for("admin_departments"))


# ==================== UPLOAD REVIEW QUEUE (admin) ====================

@app.route("/admin/uploads")
@admin_required
def admin_uploads():
    """Files departments sent for verification. Nothing is added to the
    system until DAS approves it here."""
    db = get_db()
    pending = [dict(r) for r in db.fetchall(
        "SELECT u.id, u.file_name, u.row_count, u.status, u.created_at,"
        " d.name AS dept_name FROM dept_uploads u"
        " JOIN departments d ON d.id=u.department_id"
        " WHERE u.status='pending' ORDER BY u.id DESC")]
    reviewed = [dict(r) for r in db.fetchall(
        "SELECT u.id, u.file_name, u.row_count, u.status, u.admin_comment,"
        " u.created_at, u.reviewed_at, d.name AS dept_name FROM dept_uploads u"
        " JOIN departments d ON d.id=u.department_id"
        " WHERE u.status!='pending' ORDER BY u.id DESC LIMIT 50")]
    return render_template("admin_uploads.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, pending=pending,
                           reviewed=reviewed)


@app.route("/admin/uploads/<int:uid>/download")
@admin_required
def admin_upload_download(uid):
    row = get_db().fetchone(
        "SELECT * FROM dept_uploads WHERE id=?", (uid,))
    if not row:
        flash("Upload not found.")
        return redirect(url_for("admin_uploads"))
    u = dict(row)
    return Response(doc_bytes(u),
                    mimetype="application/vnd.openxmlformats-officedocument."
                             "spreadsheetml.sheet",
                    headers={"Content-Disposition":
                             f'attachment; filename="{safe_filename(u.get("file_name"))}"'})


@app.route("/admin/uploads/<int:uid>/approve", methods=["POST"])
@admin_required
def admin_upload_approve(uid):
    """Approve a department file: every row is inserted as its own
    student record, tagged with the department name."""
    db = get_db()
    row = db.fetchone(
        "SELECT u.*, d.name AS dept_name FROM dept_uploads u"
        " JOIN departments d ON d.id=u.department_id WHERE u.id=?", (uid,))
    if not row:
        flash("Upload not found.")
        return redirect(url_for("admin_uploads"))
    u = dict(row)
    if u["status"] != "pending":
        flash("This upload was already reviewed.")
        return redirect(url_for("admin_uploads"))
    try:
        records, skipped, _ = parse_import_rows(io.BytesIO(doc_bytes(u)))
    except ValueError as e:
        flash(f"Could not parse the file: {e}")
        return redirect(url_for("admin_uploads"))
    added, _, _, _ = insert_student_records(db, records,
                                            department=u["dept_name"])
    db.execute("UPDATE dept_uploads SET status='approved', reviewed_at=?"
               " WHERE id=?",
               (datetime.now().strftime("%Y-%m-%d %H:%M"), uid))
    db.commit()
    log_activity("admin", "upload_approve",
                 f"{u['dept_name']}: {added} student(s) added from"
                 f" {u['file_name']}")
    flash(f"Approved — {added} student record(s) from {u['dept_name']}"
          f" are now in the system.")
    return redirect(url_for("admin_uploads"))


@app.route("/admin/uploads/<int:uid>/reject", methods=["POST"])
@admin_required
def admin_upload_reject(uid):
    """Reject with a comment — the department sees it and re-uploads."""
    comment = request.form.get("comment", "").strip()
    if not comment:
        flash("Please write a comment explaining what needs fixing.")
        return redirect(url_for("admin_uploads"))
    db = get_db()
    row = db.fetchone(
        "SELECT u.*, d.name AS dept_name FROM dept_uploads u"
        " JOIN departments d ON d.id=u.department_id WHERE u.id=?", (uid,))
    if not row:
        flash("Upload not found.")
        return redirect(url_for("admin_uploads"))
    u = dict(row)
    if u["status"] != "pending":
        flash("This upload was already reviewed.")
        return redirect(url_for("admin_uploads"))
    db.execute("UPDATE dept_uploads SET status='rejected', admin_comment=?,"
               " reviewed_at=? WHERE id=?",
               (comment, datetime.now().strftime("%Y-%m-%d %H:%M"), uid))
    db.commit()
    log_activity("admin", "upload_reject",
                 f"{u['dept_name']}: {comment[:120]}")
    flash(f"Rejected with your comment — {u['dept_name']} will see it on"
          f" their portal and can fix and re-upload.")
    return redirect(url_for("admin_uploads"))


# ==================== ACTIVITY LOG (admin) ====================

@app.route("/admin/activity")
@admin_required
def admin_activity():
    """Who did what, when — transparency for every change."""
    db = get_db()
    try:
        page = max(int(request.args.get("page", 1) or 1), 1)
    except (TypeError, ValueError):
        page = 1
    per = 50
    total_row = db.fetchone("SELECT COUNT(*) AS n FROM activity_log")
    total = (total_row["n"] if isinstance(total_row, dict)
             else total_row[0]) if total_row else 0
    pages = max((total + per - 1) // per, 1)
    page = min(page, pages)
    rows = [dict(r) for r in db.fetchall(
        "SELECT * FROM activity_log ORDER BY id DESC LIMIT ? OFFSET ?",
        (per, (page - 1) * per))]
    return render_template("admin_activity.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, rows=rows, total=total,
                           page=page, pages=pages)


# ==================== REPORTS (admin, printable) ====================

@app.route("/admin/reports")
@admin_required
def admin_reports():
    """Meeting-ready summary: filter by session/degree/status, print it."""
    f = request.args
    students = query_students(f)
    for s in students:
        s["coe_display"] = fmt_coe(s.get("thesis_forward_coe"))
    by_status, by_degree, by_session = {}, {}, {}
    for s in students:
        st = s.get("status") or "Continue"
        by_status[st] = by_status.get(st, 0) + 1
        dg = s.get("degree") or "—"
        by_degree[dg] = by_degree.get(dg, 0) + 1
        se = s.get("session") or "—"
        by_session[se] = by_session.get(se, 0) + 1
    return render_template(
        "admin_reports.html", uni=UNI_NAME, title=PORTAL_TITLE,
        students=students, total=len(students),
        by_status=sorted(by_status.items(), key=lambda x: -x[1]),
        by_degree=sorted(by_degree.items(), key=lambda x: -x[1]),
        by_session=sorted(by_session.items(), key=lambda x: -x[1]),
        status_list=STATUS_LIST, sessions=distinct_values("session"),
        degrees=distinct_values("degree"), f=f,
        today=datetime.now().strftime("%d %b %Y"))


@app.context_processor
def inject_admin_counts():
    """Pending-upload badge for the admin sidebar."""
    if session.get("admin"):
        try:
            row = get_db().fetchone(
                "SELECT COUNT(*) AS n FROM dept_uploads WHERE status='pending'")
            n = (row["n"] if isinstance(row, dict) else row[0]) if row else 0
            return {"pending_uploads": n}
        except Exception:
            pass
    return {"pending_uploads": 0}


# ==================== DEPARTMENT PORTAL ====================

_EX_FIRST = ["Ahmed", "Fatima", "Bilal", "Ayesha", "Usman", "Maryam", "Ali",
             "Sana", "Hassan", "Iqra", "Danish", "Hira", "Fahad", "Zainab"]
_EX_LAST = ["Khan", "Ahmed", "Malik", "Raza", "Hussain", "Shah", "Iqbal",
            "Butt", "Chaudhry", "Farooq"]
_EX_DEGREES = ["MPhil Zoology", "MPhil Biochemistry",
               "MPhil Food Science & Technology", "PhD Zoology"]


def random_example_row():
    """One random filled row so departments see the expected format."""
    import random
    fn, ln = random.choice(_EX_FIRST), random.choice(_EX_LAST)
    father = random.choice(_EX_FIRST) + " " + random.choice(_EX_LAST)
    cnic = "".join(random.choice("0123456789") for _ in range(13))
    return [cnic, f"{fn} {ln}", father,
            f"{random.randint(20, 25)}-CUVAS-{random.randint(1000, 9999):04d}",
            random.choice(["Male", "Female"]), random.choice(_EX_DEGREES),
            random.choice(["F. 23-25", "24-26 Fall", "S. 21-24"]),
            "Not Completed", "N/A", "Not Received", "Not Received",
            "Not Received", "", "Continue"]


VIVA_RESULTS = ["Pending", "Pass", "Fail", "Rescheduled"]


@app.route("/dept", methods=["GET", "POST"])
def dept_login():
    if session.get("dept_id"):
        return redirect(url_for("dept_dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        row = get_db().fetchone(
            "SELECT * FROM departments WHERE email=?", (email,))
        if row and check_password_hash(dict(row)["password_hash"], password):
            d = dict(row)
            session["dept_id"] = d["id"]
            log_activity(f"dept:{d['name']}", "login",
                         "department portal sign-in")
            return redirect(url_for("dept_dashboard"))
        flash("Invalid email or password.")
        return redirect(url_for("dept_login"))
    return render_template("dept_login.html", uni=UNI_NAME, title=PORTAL_TITLE)


@app.route("/dept/logout")
def dept_logout():
    session.pop("dept_id", None)
    return redirect(url_for("dept_login"))


@app.route("/dept/dashboard")
@dept_required
def dept_dashboard():
    dept = current_dept()
    uploads = [dict(r) for r in get_db().fetchall(
        "SELECT id, file_name, row_count, status, admin_comment, created_at,"
        " reviewed_at FROM dept_uploads WHERE department_id=?"
        " ORDER BY id DESC", (dept["id"],))]
    return render_template("dept_dashboard.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, dept=dept, uploads=uploads)


@app.route("/dept/upload", methods=["POST"])
@dept_required
def dept_upload():
    """Departments send their file for DAS verification — nothing is
    added to the system until an admin approves it."""
    dept = current_dept()
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Please choose a file to upload.")
        return redirect(url_for("dept_dashboard"))
    if not file.filename.lower().endswith(".xlsx"):
        flash("Only Excel (.xlsx) files are allowed.")
        return redirect(url_for("dept_dashboard"))
    raw = file.read()
    if not raw:
        flash("The file is empty.")
        return redirect(url_for("dept_dashboard"))
    try:
        records, skipped, _ = parse_import_rows(io.BytesIO(raw))
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("dept_dashboard"))
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    db.execute(
        "INSERT INTO dept_uploads (department_id, file_name, file_data,"
        " row_count, status, created_at)"
        " VALUES (?, ?, ?, ?, 'pending', ?)",
        (dept["id"], file.filename, raw, len(records), now))
    db.commit()
    log_activity(f"dept:{dept['name']}", "upload",
                 f"{len(records)} row(s) sent for review ({file.filename})")
    flash(f"Sent for DAS review: {len(records)} student row(s)."
          f" You will see the decision here.")
    return redirect(url_for("dept_dashboard"))


@app.route("/dept/template")
@dept_required
def dept_template():
    """Sample template with one random filled example row."""
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
    ws.append(random_example_row())
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
        "attachment; filename=department_upload_template.xlsx"
    return resp


@app.route("/dept/viva", methods=["GET", "POST"])
@dept_required
def dept_viva():
    """Departments manage viva for their own students: examiner, date,
    result."""
    dept = current_dept()
    db = get_db()
    if request.method == "POST":
        sid = request.form.get("student_id", "")
        examiner = request.form.get("examiner_name", "").strip()
        affiliation = request.form.get("examiner_affiliation", "").strip()
        viva_date = request.form.get("viva_date", "").strip()
        result = request.form.get("result", "Pending")
        remarks = request.form.get("remarks", "").strip()
        st = db.fetchone(
            "SELECT * FROM students WHERE id=? AND department=?",
            (sid, dept["name"]))
        if not st or not examiner:
            flash("Select one of your department's students and enter the"
                  " examiner name.")
            return redirect(url_for("dept_viva"))
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        db.execute(
            "INSERT INTO viva_records (student_id, examiner_name,"
            " examiner_affiliation, viva_date, result, remarks, updated_by,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, examiner, affiliation, viva_date or None, result,
             remarks, dept["name"], now))
        db.commit()
        st = dict(st)
        log_activity(f"dept:{dept['name']}", "viva_add",
                     f"{st['name']} ({st['reg_no']}): {examiner},"
                     f" {viva_date or 'no date'}, {result}")
        flash("Viva record saved.")
        return redirect(url_for("dept_viva"))
    students = [dict(r) for r in db.fetchall(
        "SELECT id, name, reg_no FROM students WHERE department=?"
        " ORDER BY name", (dept["name"],))]
    vivas = [dict(r) for r in db.fetchall(
        "SELECT v.*, s.name AS student_name, s.reg_no FROM viva_records v"
        " JOIN students s ON s.id=v.student_id"
        " WHERE s.department=? ORDER BY v.id DESC", (dept["name"],))]
    return render_template("dept_viva.html", uni=UNI_NAME, title=PORTAL_TITLE,
                           dept=dept, students=students, vivas=vivas,
                           results=VIVA_RESULTS)


@app.route("/dept/viva/<int:vid>/delete", methods=["POST"])
@dept_required
def dept_viva_delete(vid):
    dept = current_dept()
    db = get_db()
    row = db.fetchone(
        "SELECT v.id FROM viva_records v JOIN students s ON s.id=v.student_id"
        " WHERE v.id=? AND s.department=?", (vid, dept["name"]))
    if row:
        db.execute("DELETE FROM viva_records WHERE id=?", (vid,))
        db.commit()
        log_activity(f"dept:{dept['name']}", "viva_delete",
                     f"viva record #{vid} removed")
        flash("Viva record deleted.")
    return redirect(url_for("dept_viva"))


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
        records, skipped, skip_notes = parse_import_rows(file)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("admin_import"))
    db = get_db()
    added, skipped2, skip_notes2, first_error = insert_student_records(db, records)
    skipped += skipped2
    skip_notes += skip_notes2
    db.commit()
    msg = f"Import complete: {added} added, {skipped} skipped."
    if skip_notes:
        msg += " Skipped: " + "; ".join(skip_notes)
        msg += "." if skipped <= len(skip_notes) else "..."
    if first_error and skipped:
        msg += f" (first error: {first_error})"
    log_activity("admin", "import",
                 f"{added} added, {skipped} skipped via admin import")
    flash(msg)
    return redirect(url_for("admin_students"))


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
        log_activity("admin", "student_add", f"{name} ({reg_no})")
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
    vivas = [dict(r) for r in get_db().fetchall(
        "SELECT * FROM viva_records WHERE student_id=? ORDER BY id DESC",
        (sid,))]
    return render_template("student_profile.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, s=s, docs=docs, vivas=vivas,
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
    log_activity("admin", "student_edit", f"id={sid} ({name}) record updated")
    flash("Student record updated.")
    return redirect(url_for("admin_student_profile", sid=sid))


@app.route("/admin/students/delete-selected", methods=["POST"])
@admin_required
def admin_students_delete_selected():
    ids = [int(x) for x in request.form.getlist("sel") if x.isdigit()]
    if not ids:
        flash("No students selected.")
        return redirect(url_for("admin_students"))
    db = get_db()
    ph = ", ".join(["?"] * len(ids))
    db.execute(f"DELETE FROM documents WHERE student_id IN ({ph})", ids)
    db.execute(f"DELETE FROM students WHERE id IN ({ph})", ids)
    db.commit()
    log_activity("admin", "student_bulk_delete", f"{len(ids)} student(s) deleted")
    flash(f"Deleted {len(ids)} student(s) and their documents.")
    return redirect(url_for("admin_students"))


@app.route("/admin/students/delete-all", methods=["POST"])
@admin_required
def admin_students_delete_all():
    db = get_db()
    db.execute("DELETE FROM documents")
    db.execute("DELETE FROM students")
    db.commit()
    log_activity("admin", "student_delete_all", "ALL students and documents deleted")
    flash("All student data has been deleted.")
    return redirect(url_for("admin_students"))


@app.route("/admin/student/<int:sid>/delete", methods=["POST"])
@admin_required
def admin_student_delete(sid):
    db = get_db()
    db.execute("DELETE FROM documents WHERE student_id=?", (sid,))
    db.execute("DELETE FROM students WHERE id=?", (sid,))
    db.commit()
    log_activity("admin", "student_delete", f"id={sid} and documents deleted")
    flash("Student and their documents deleted.")
    return redirect(url_for("admin_students"))


ADMIN_DOC_EXTS = {"pdf", "doc", "docx", "jpg", "jpeg", "png"}
MIME_BY_EXT = {"pdf": "application/pdf",
               "doc": "application/msword",
               "docx": "application/vnd.openxmlformats-officedocument."
                       "wordprocessingml.document",
               "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}


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
    log_activity("admin", "doc_delete", f"doc #{doc_id} deleted (student id={sid})")
    flash("Document deleted.")
    return redirect(url_for("admin_student_profile", sid=sid)) if sid \
        else redirect(url_for("admin_students"))


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
        db = get_db()
        row = db.fetchone("SELECT * FROM students WHERE reg_no=?", (reg_no,))
        s = row_dict(row)
        ok = False
        if s:
            if s.get("password_hash"):
                ok = check_password_hash(s["password_hash"], pw)
            elif pw and pw == (s.get("cnic") or ""):
                # Bulk-imported record logging in for the first time:
                # default password is the CNIC digits; hash it now.
                db.execute(
                    "UPDATE students SET password_hash=?, updated_at=? WHERE id=?",
                    (generate_password_hash(pw),
                     datetime.now().strftime("%Y-%m-%d %H:%M"), s["id"]))
                db.commit()
                ok = True
        if ok:
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
