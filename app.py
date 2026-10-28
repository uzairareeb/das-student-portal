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
from datetime import datetime, date, timedelta
from functools import wraps
from urllib.parse import urlencode

from flask import (Flask, Response, flash, g, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from viva_pdf import build_viva_pdf

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
               "Viva Done", "Viva Pending", "Defense Pending", "Sent to COE",
               "Pass Out"]
def status_pill_class(status):
    """Pill colour for a status."""
    if status in ("Continue", "Viva Done", "Pass Out", "Sent to COE"):
        return "q"
    if status in ("Viva Pending", "Defense Pending"):
        return "warn"
    return "nq"


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
                    "Synopsis", "Semi-Final Thesis", "Final Thesis",
                    "Thesis Forward (COE)", "Status", "Comments (Missing)"]


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
            synopsis_at TEXT,
            semi_final_thesis TEXT DEFAULT 'Not Received',
            final_thesis TEXT DEFAULT 'Not Received',
            semi_final_thesis_at TEXT,
            final_thesis_at TEXT,
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
            created_at TEXT,
            admin_seen INTEGER DEFAULT 0,
            total_marks INTEGER DEFAULT 120,
            obtained_marks INTEGER,
            next_viva_date TEXT
        )
    """)
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS student_notifications (
            {id_pg if USE_PG else id_lite},
            student_id INTEGER NOT NULL,
            kind TEXT,
            title TEXT,
            message TEXT,
            link TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS admin_notifications (
            {id_pg if USE_PG else id_lite},
            kind TEXT,
            title TEXT,
            message TEXT,
            link TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS dept_notifications (
            {id_pg if USE_PG else id_lite},
            department_id INTEGER NOT NULL REFERENCES departments(id),
            kind TEXT,
            title TEXT,
            message TEXT,
            link TEXT,
            is_read INTEGER DEFAULT 0,
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
    _migrate_add_viva_admin_seen(db)
    _migrate_add_viva_marks(db)
    _migrate_add_thesis_dates(db)
    _fix_passout_rows(db)
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
                    "synopsis", "synopsis_at", "semi_final_thesis",
                    "final_thesis", "semi_final_thesis_at", "final_thesis_at",
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
                synopsis_at TEXT,
                semi_final_thesis TEXT DEFAULT 'Not Received',
                final_thesis TEXT DEFAULT 'Not Received',
                semi_final_thesis_at TEXT,
                final_thesis_at TEXT,
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


def _migrate_add_viva_marks(db):
    """Add viva_records.total_marks / obtained_marks / next_viva_date."""
    try:
        cols_needed = {"total_marks": "INTEGER DEFAULT 120",
                       "obtained_marks": "INTEGER",
                       "next_viva_date": "TEXT"}
        if USE_PG:
            for col, typ in cols_needed.items():
                db.execute(f"ALTER TABLE viva_records ADD COLUMN IF NOT EXISTS"
                           f" {col} {typ}")
        else:
            cols = [r[1] for r in db.fetchall("PRAGMA table_info(viva_records)")]
            for col, typ in cols_needed.items():
                if col not in cols:
                    db.execute(f"ALTER TABLE viva_records ADD COLUMN {col} {typ}")
        db.commit()
    except Exception:
        pass


def _migrate_add_viva_admin_seen(db):
    """Add viva_records.admin_seen for the admin 'new viva' badge."""
    try:
        if USE_PG:
            db.execute("ALTER TABLE viva_records ADD COLUMN IF NOT EXISTS"
                       " admin_seen INTEGER DEFAULT 0")
        else:
            cols = [r[1] for r in db.fetchall("PRAGMA table_info(viva_records)")]
            if "admin_seen" not in cols:
                db.execute("ALTER TABLE viva_records ADD COLUMN"
                           " admin_seen INTEGER DEFAULT 0")
        db.commit()
    except Exception:
        pass


def _migrate_add_thesis_dates(db):
    """Add the received-on datetime columns. Safe to rerun."""
    try:
        if USE_PG:
            for col in ("synopsis_at", "semi_final_thesis_at",
                        "final_thesis_at"):
                db.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS"
                           f" {col} TEXT")
        else:
            cols = [r[1] for r in db.fetchall("PRAGMA table_info(students)")]
            for col in ("synopsis_at", "semi_final_thesis_at",
                        "final_thesis_at"):
                if col not in cols:
                    db.execute(f"ALTER TABLE students ADD COLUMN {col} TEXT")
        db.commit()
    except Exception:
        try:
            db.conn.rollback()
        except Exception:
            pass


def _fix_passout_rows(db):
    """Pass Out students always show folder + synopsis + both theses complete.
    Applies to rows already in the DB (e.g. uploaded before this rule) and
    is safe to rerun: it only touches Pass Out rows that are not yet complete,
    and never writes any received-on date."""
    try:
        db.execute("""UPDATE students SET folder_file='Completed',
            synopsis='Received', semi_final_thesis='Received',
            final_thesis='Received' WHERE status='Pass Out'
            AND (folder_file<>'Completed' OR synopsis<>'Received'
            OR semi_final_thesis<>'Received' OR final_thesis<>'Received')""")
        db.commit()
    except Exception:
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
            # Cold-start CREATE TABLE races are safe to ignore.
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


def parse_dt(raw):
    """Form datetime -> 'YYYY-MM-DD HH:MM' or ''."""
    s = (str(raw) if raw is not None else "").strip().replace("T", " ")
    if not s:
        return ""
    for cand in (s, s[:16]):
        try:
            return datetime.strptime(cand, "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return ""


def fmt_stamp(val):
    """DB datetime -> '22 Oct 2026, 09:41 AM' or em dash."""
    iso = parse_dt(val)
    if not iso:
        return "\u2014"
    return datetime.strptime(iso, "%Y-%m-%d %H:%M").strftime("%d %b %Y, %I:%M %p")


def dt_local(val):
    """DB datetime -> value for an <input type=datetime-local>."""
    iso = parse_dt(val)
    return iso.replace(" ", "T") if iso else ""


def _thesis_stamp(new_status, posted_raw, old_status, old_val):
    """Received-on stamp: Not Received clears; posted wins; an existing
    Received stamp is kept; a fresh Received = now."""
    if new_status != "Received":
        return None
    posted = parse_dt(posted_raw)
    if posted:
        return posted
    if old_status == "Received" and old_val:
        return parse_dt(old_val) or None
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def pick(val, options, default):
    v = (val or "").strip().replace(" ", "").lower()
    for o in options:
        if v == o.replace(" ", "").lower():
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
    dept = (args.get("department") or "").strip()
    if dept == "__unassigned":
        where.append("(department IS NULL OR department='')")
    elif dept:
        where.append("department=?")
        params.append(dept)
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


def dept_names():
    """Department names from the departments table (exact match matters:
    the dept portal filters students with department=<name>)."""
    rows = get_db().fetchall("SELECT name FROM departments ORDER BY name")
    return [(r["name"] if isinstance(r, dict) else r[0]) for r in rows]


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
    """Overview: stats, charts, pending actions, recent uploads."""
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
    from pending_actions import compute_pending
    pending = compute_pending(db, datetime.now(), parse_coe, parse_dt,
                              fmt_coe, fmt_stamp)
    # ---- dashboard-only additions: read-only aggregates, no schema change
    def _cnt(sql, params=()):
        r = db.fetchone(sql, params)
        return (r["n"] if isinstance(r, dict) else r[0]) if r else 0
    now = datetime.now()
    pipeline = [
        ("Synopsis Received", _cnt(
            "SELECT COUNT(*) AS n FROM students WHERE synopsis='Received'")),
        ("Semi-Final Received", _cnt(
            "SELECT COUNT(*) AS n FROM students"
            " WHERE semi_final_thesis='Received'")),
        ("Final Received", _cnt(
            "SELECT COUNT(*) AS n FROM students"
            " WHERE final_thesis='Received'")),
        ("Sent to COE", _cnt(
            "SELECT COUNT(*) AS n FROM students"
            " WHERE thesis_forward_coe IS NOT NULL")),
        ("Viva Passed", _cnt(
            "SELECT COUNT(DISTINCT student_id) AS n FROM viva_records"
            " WHERE result='Pass'")),
        ("Pass Out", stats.get("Pass Out", 0)),
    ]
    pipeline = [{"stage": s, "count": c,
                 "pct": (c * 100 // total) if total else 0}
                for s, c in pipeline]
    dept_rows = []
    for d in dept_names():
        dept_rows.append({
            "name": d,
            "students": _cnt(
                "SELECT COUNT(*) AS n FROM students WHERE department=?", (d,)),
            "uploads_pending": _cnt(
                "SELECT COUNT(*) AS n FROM dept_uploads u"
                " JOIN departments dep ON dep.id=u.department_id"
                " WHERE dep.name=? AND u.status='pending'", (d,)),
            "viva_pending": _cnt(
                "SELECT COUNT(*) AS n FROM viva_records v"
                " JOIN students s ON s.id=v.student_id"
                " WHERE s.department=? AND v.result='Pending'", (d,)),
        })
    unassigned = _cnt(
        "SELECT COUNT(*) AS n FROM students"
        " WHERE department IS NULL OR department=''")
    if unassigned:
        dept_rows.append({"name": "Unassigned", "students": unassigned,
                          "uploads_pending": 0, "viva_pending": _cnt(
                              "SELECT COUNT(*) AS n FROM viva_records v"
                              " JOIN students s ON s.id=v.student_id"
                              " WHERE (s.department IS NULL OR s.department='')"
                              " AND v.result='Pending'")})
    uploads_queue = [dict(r) for r in db.fetchall(
        "SELECT u.id, u.file_name, u.row_count, u.created_at,"
        " dep.name AS dept_name FROM dept_uploads u"
        " JOIN departments dep ON dep.id=u.department_id"
        " WHERE u.status='pending' ORDER BY u.id DESC LIMIT 5")]
    activity = [dict(r) for r in db.fetchall(
        "SELECT actor, action, detail, created_at FROM activity_log"
        " ORDER BY id DESC LIMIT 8")]
    quality = {
        "missing_cnic": _cnt(
            "SELECT COUNT(*) AS n FROM students"
            " WHERE cnic IS NULL OR cnic=''"),
        "missing_degree": _cnt(
            "SELECT COUNT(*) AS n FROM students"
            " WHERE degree IS NULL OR degree=''"),
        "missing_session": _cnt(
            "SELECT COUNT(*) AS n FROM students"
            " WHERE session IS NULL OR session=''"),
        "no_documents": _cnt(
            "SELECT COUNT(*) AS n FROM students s"
            " WHERE NOT EXISTS (SELECT 1 FROM documents d"
            " WHERE d.student_id=s.id)"),
        "folder_pending": _cnt(
            "SELECT COUNT(*) AS n FROM students"
            " WHERE folder_file<>'Completed'"),
    }
    # monthly trend: last 6 months, counted in Python (TEXT dates, both DBs)
    months, trend_counts = [], []
    y, m = now.year, now.month
    for _ in range(6):
        months.append(f"{y:04d}-{m:02d}")
        if m == 1:
            y, m = y - 1, 12
        else:
            m -= 1
    months.reverse()
    by_month = {}
    for r in db.fetchall("SELECT created_at FROM students"):
        v = (dict(r).get("created_at") or "")[:7]
        if v in months:
            by_month[v] = by_month.get(v, 0) + 1
    trend_counts = [by_month.get(x, 0) for x in months]
    cur_month = months[-1]
    prev_month = months[-2]
    ym_pat = cur_month + "%"
    viva_month = {
        "pass": _cnt(
            "SELECT COUNT(*) AS n FROM viva_records"
            " WHERE result='Pass' AND viva_date LIKE ?", (ym_pat,)),
        "fail": _cnt(
            "SELECT COUNT(*) AS n FROM viva_records"
            " WHERE result='Fail' AND viva_date LIKE ?", (ym_pat,)),
        "pending": _cnt(
            "SELECT COUNT(*) AS n FROM viva_records"
            " WHERE result='Pending' AND viva_date LIKE ?", (ym_pat,)),
    }
    # Top Degree Programmes: group full degree strings by first word
    # (e.g. "MPhil Biochemistry" -> MPhil), counted in Python, both DBs.
    prog = {}
    for name, n in degrees:
        prog[name.split(" ")[0] if name else "Other"] = \
            prog.get(name.split(" ")[0] if name else "Other", 0) + n
    top_programs = sorted(prog.items(), key=lambda kv: -kv[1])
    greeting = ("Good Morning" if now.hour < 12 else
                "Good Afternoon" if now.hour < 17 else "Good Evening")
    return render_template(
        "admin_dashboard.html", uni=UNI_NAME, title=PORTAL_TITLE,
        total=total, stats=stats, status_list=STATUS_LIST,
        status_counts=[stats.get(st, 0) for st in STATUS_LIST],
        degrees=degrees, sessions=sessions,
        doc_total=doc_total, with_docs=with_docs, recent=recent,
        pending=pending, pipeline=pipeline, dept_rows=dept_rows,
        uploads_queue=uploads_queue, activity=activity, quality=quality,
        trend_months=months, trend_counts=trend_counts,
        trend_current=by_month.get(cur_month, 0),
        trend_previous=by_month.get(prev_month, 0),
        viva_month=viva_month, today_str=now.strftime("%A, %d %B %Y"),
        greeting=greeting, top_programs=top_programs)


@app.route("/admin/students")
@admin_required
def admin_students():
    """Student list page: filters, table, pagination, bulk, export."""
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
        s["syn_at_disp"] = fmt_stamp(s.get("synopsis_at"))
        s["semi_at_disp"] = fmt_stamp(s.get("semi_final_thesis_at"))
        s["final_at_disp"] = fmt_stamp(s.get("final_thesis_at"))
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
        received=RECEIVED_OPTS, departments=dept_names(),
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
               "Synopsis", "Synopsis Received On", "Semi-Final Thesis",
               "Semi-Final Received On", "Final Thesis",
               "Final Received On", "Thesis Forward (COE)",
               "Status", "Comments (Missing)"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2D3290")
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
    for s in students:
        _syn = fmt_stamp(s.get("synopsis_at"))
        _semi = fmt_stamp(s.get("semi_final_thesis_at"))
        _fin = fmt_stamp(s.get("final_thesis_at"))
        ws.append([s.get("name"), s.get("reg_no"), s.get("gender"),
                   s.get("degree"), s.get("session"), s.get("folder_file"),
                   s.get("synopsis"),
                   "" if _syn == "\u2014" else _syn,
                   s.get("semi_final_thesis"),
                   "" if _semi == "\u2014" else _semi,
                   s.get("final_thesis"),
                   "" if _fin == "\u2014" else _fin,
                   parse_coe(s.get("thesis_forward_coe")) or "",
                   s.get("status"), s.get("comments_missing")])
    widths = [24, 16, 10, 22, 12, 14, 12, 21, 14, 21, 14, 21, 18, 16, 26]
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
    """Landscape PDF of the (filtered) student list."""
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
               "Synopsis", "Semi-Final", "Final Thesis", "COE Date", "Status",
               "Comments"]
    widths = [32 * mm, 26 * mm, 14 * mm, 34 * mm, 20 * mm, 22 * mm,
              18 * mm, 18 * mm, 18 * mm, 20 * mm, 21 * mm, 34 * mm]
    data = [[Paragraph(h, head_cell) for h in headers]]
    for s in students:
        data.append([
            Paragraph(s.get("name") or "—", cell),
            Paragraph(s.get("reg_no") or "—", cell),
            Paragraph(s.get("gender") or "—", cell),
            Paragraph(s.get("degree") or "—", cell),
            Paragraph(s.get("session") or "—", cell),
            Paragraph(s.get("folder_file") or "—", cell),
            Paragraph((s.get("synopsis") or "—") + (
                "<br/>" + fmt_stamp(s.get("synopsis_at"))
                if s.get("synopsis_at") else ""), cell),
            Paragraph((s.get("semi_final_thesis") or "—") + (
                "<br/>" + fmt_stamp(s.get("semi_final_thesis_at"))
                if s.get("semi_final_thesis_at") else ""), cell),
            Paragraph((s.get("final_thesis") or "—") + (
                "<br/>" + fmt_stamp(s.get("final_thesis_at"))
                if s.get("final_thesis_at") else ""), cell),
            Paragraph(fmt_coe(s.get("thesis_forward_coe")), cell),
            Paragraph(s.get("status") or "—", cell),
            Paragraph(s.get("comments_missing") or "—", cell),
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


def notify_dept(department_id, kind, title, message="", link=""):
    """Create an in-portal notification for a department."""
    try:
        get_db().execute(
            "INSERT INTO dept_notifications (department_id, kind, title,"
            " message, link, created_at) VALUES (?,?,?,?,?,?)",
            (department_id, kind, title, message, link,
             datetime.now().strftime("%Y-%m-%d %H:%M")))
        get_db().commit()
    except Exception:
        pass


def notify_admin(kind, title, message="", link=""):
    """Create an in-portal notification for the DAS admin."""
    try:
        get_db().execute(
            "INSERT INTO admin_notifications (kind, title, message, link,"
            " created_at) VALUES (?,?,?,?,?)",
            (kind, title, message, link,
             datetime.now().strftime("%Y-%m-%d %H:%M")))
        get_db().commit()
    except Exception:
        pass


def notify_student(student_id, kind, title, message="", link=""):
    """Create an in-portal notification for a student."""
    try:
        get_db().execute(
            "INSERT INTO student_notifications (student_id, kind, title,"
            " message, link, created_at) VALUES (?,?,?,?,?,?)",
            (student_id, kind, title, message, link,
             datetime.now().strftime("%Y-%m-%d %H:%M")))
        get_db().commit()
    except Exception:
        pass


def student_unread_count(student_id):
    row = get_db().fetchone(
        "SELECT COUNT(*) AS n FROM student_notifications"
        " WHERE student_id=? AND is_read=0", (student_id,))
    return (row["n"] if isinstance(row, dict) else row[0]) if row else 0


def admin_unread_count():
    row = get_db().fetchone(
        "SELECT COUNT(*) AS n FROM admin_notifications WHERE is_read=0")
    return (row["n"] if isinstance(row, dict) else row[0]) if row else 0


def dept_unread_count(department_id):
    row = get_db().fetchone(
        "SELECT COUNT(*) AS n FROM dept_notifications"
        " WHERE department_id=? AND is_read=0", (department_id,))
    return (row["n"] if isinstance(row, dict) else row[0]) if row else 0


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
        if rec["status"] == "Pass Out":
            rec["folder_file"] = "Completed"
            rec["synopsis"] = rec["semi_final_thesis"] = rec["final_thesis"] = "Received"
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

@app.route("/admin/department/<int:did>/edit", methods=["GET", "POST"])
@admin_required
def admin_department_edit(did):
    """Update a department login (name/email; password only if re-entered)."""
    db = get_db()
    row = db.fetchone("SELECT * FROM departments WHERE id=?", (did,))
    if not row:
        flash("Department not found.")
        return redirect(url_for("admin_departments"))
    d = dict(row)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email:
            flash("Department name and email are required.")
            return redirect(url_for("admin_department_edit", did=did))
        try:
            if password:
                db.execute("UPDATE departments SET name=?, email=?,"
                           " password_hash=? WHERE id=?",
                           (name, email, generate_password_hash(password), did))
            else:
                db.execute("UPDATE departments SET name=?, email=? WHERE id=?",
                           (name, email, did))
            db.commit()
            log_activity("admin", "department_edit", f"{name} <{email}>")
            flash(f"Department '{name}' updated.")
        except Exception:
            flash("Could not update — the name or email may already exist.")
        return redirect(url_for("admin_departments"))
    return render_template("admin_department_edit.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, d=d)



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
    notify_dept(u["department_id"], "upload", "Upload approved",
                f"{added} student record(s) from {u['file_name']}"
                " were added to the system.",
                url_for("dept_dashboard"))
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
    notify_dept(u["department_id"], "upload", "Upload rejected — action needed",
                comment, url_for("dept_dashboard"))
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

def query_vivas(args):
    """Viva records with department/degree/result filters."""
    where, params = [], []
    dept = (args.get("department") or "").strip()
    if dept:
        where.append("s.department=?")
        params.append(dept)
    degree = (args.get("degree") or "").strip()
    if degree:
        where.append("s.degree=?")
        params.append(degree)
    result = (args.get("result") or "").strip()
    if result:
        where.append("v.result=?")
        params.append(result)
    q = (args.get("q") or "").strip()
    if q:
        op = "ILIKE" if USE_PG else "LIKE"
        where.append(f"(s.name {op} ? OR s.reg_no {op} ? OR v.examiner_name {op} ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    sql = ("SELECT v.*, s.name AS student_name, s.reg_no, s.degree,"
           " s.department FROM viva_records v"
           " JOIN students s ON s.id=v.student_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY v.viva_date DESC NULLS LAST, v.id DESC" if USE_PG else \
        " ORDER BY v.viva_date DESC, v.id DESC"
    return [dict(r) for r in get_db().fetchall(sql, params)]


def build_viva_card(vid):
    """Assemble viva result-card data for a viva record id. Returns None if
    the record has no marks yet (card not available)."""
    row = get_db().fetchone(
        "SELECT v.*, s.name, s.father_name, s.reg_no, s.department,"
        " s.degree, s.session FROM viva_records v"
        " JOIN students s ON s.id=v.student_id WHERE v.id=?", (vid,))
    if not row:
        return None
    v = dict(row)
    if v.get("obtained_marks") is None:
        return None
    total = v.get("total_marks") or 120
    obtained = v["obtained_marks"]
    pct = round(obtained * 100.0 / total, 1) if total else 0
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Karachi"))
    except Exception:
        now = datetime.now()
    return {
        "name": v["name"], "father_name": v.get("father_name"),
        "reg_no": v["reg_no"], "department": v.get("department"),
        "degree": v.get("degree"), "session": v.get("session"),
        "examiner_name": v.get("examiner_name"),
        "examiner_affiliation": v.get("examiner_affiliation"),
        "viva_date": v.get("viva_date"), "result": v.get("result"),
        "total_marks": total, "obtained_marks": obtained,
        "percentage": pct, "remarks": v.get("remarks"),
        "year": now.year,
        "generated_on": now.strftime("%d %b %Y, %I:%M %p") + " (PKT)",
    }




@app.route("/admin/viva")
@admin_required
def admin_viva():
    """All viva records with filters + export."""
    db = get_db()
    try:
        db.execute("UPDATE viva_records SET admin_seen=1 WHERE admin_seen=0")
        db.commit()
    except Exception:
        pass
    vivas = query_vivas(request.args)
    return render_template(
        "admin_viva.html", uni=UNI_NAME, title=PORTAL_TITLE, vivas=vivas,
        departments=[d["name"] for d in get_db().fetchall(
            "SELECT name FROM departments ORDER BY name")],
        degrees=distinct_values("degree"), results=VIVA_RESULTS,
        f=request.args)


@app.route("/admin/viva/export")
@admin_required
def admin_viva_export():
    """Excel export of (filtered) viva records."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    vivas = query_vivas(request.args)
    wb = Workbook()
    ws = wb.active
    ws.title = "Viva Records"
    headers = ["Student", "Reg.No", "Degree", "Department", "Examiner",
               "Affiliation", "Viva Date", "Result", "Marks", "Remarks"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2D3290")
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
    for v in vivas:
        marks = (f"{v.get('obtained_marks')}/{v.get('total_marks') or 120}"
                 if v.get("obtained_marks") is not None else "")
        ws.append([v.get("student_name"), v.get("reg_no"), v.get("degree"),
                   v.get("department"), v.get("examiner_name"),
                   v.get("examiner_affiliation"), v.get("viva_date"),
                   v.get("result"), marks, v.get("remarks")])
    widths = [24, 16, 22, 18, 22, 20, 14, 12, 12, 30]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = Response(buf.getvalue(),
                    mimetype="application/vnd.openxmlformats-officedocument."
                             "spreadsheetml.sheet")
    resp.headers["Content-Disposition"] = \
        "attachment; filename=viva_records_export.xlsx"
    return resp


@app.route("/admin/viva/export-pdf")
@admin_required
def admin_viva_export_pdf():
    """PDF export of (filtered) viva records."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    vivas = query_vivas(request.args)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=12*mm, rightMargin=12*mm,
                            topMargin=14*mm, bottomMargin=14*mm,
                            title="Viva Records", author=UNI_NAME)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title2", parent=styles["Title"],
                                 textColor=colors.HexColor("#2D3290"),
                                 fontSize=18, spaceAfter=4*mm)
    normal = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=8,
                            leading=10)
    head = ParagraphStyle("Head", parent=normal, textColor=colors.white,
                          fontName="Helvetica-Bold", alignment=1)
    story = [Paragraph("Viva Records", title_style),
             Paragraph(UNI_NAME, styles["Normal"]), Spacer(1, 6*mm)]
    data = [[Paragraph(h, head) for h in
             ["Student", "Reg.No", "Degree", "Department", "Examiner",
              "Viva Date", "Result", "Marks", "Remarks"]]]
    for v in vivas:
        marks = (f"{v.get('obtained_marks')}/{v.get('total_marks') or 120}"
                 if v.get("obtained_marks") is not None else "—")
        data.append([Paragraph(str(v.get(k) or "—"), normal) for k in
                     ("student_name", "reg_no", "degree", "department",
                      "examiner_name", "viva_date", "result")]
                    + [Paragraph(marks, normal),
                       Paragraph(str(v.get("remarks") or "—"), normal)])
    t = Table(data, repeatRows=1,
              colWidths=[40*mm, 22*mm, 30*mm, 28*mm, 32*mm, 20*mm, 16*mm,
                         18*mm, 44*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2D3290")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), .5, colors.HexColor("#dfe3f5")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f6f8ff")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(f"Total: {len(vivas)} viva record(s)",
                           styles["Normal"]))
    doc.build(story)
    buf.seek(0)
    resp = Response(buf.getvalue(), mimetype="application/pdf")
    resp.headers["Content-Disposition"] = \
        "attachment; filename=viva_records_export.pdf"
    return resp


TASK_LABELS = {
    "student_add": "Student added", "student_edit": "Student record updated",
    "student_delete": "Student deleted", "student_bulk_delete": "Students deleted (bulk)",
    "student_bulk_update": "Bulk update applied", "student_delete_all": "All students deleted",
    "import": "Students imported", "upload_approve": "Department upload approved",
    "upload_reject": "Department upload rejected", "upload": "File uploaded (department)",
    "doc_upload": "Document uploaded", "doc_delete": "Document deleted",
    "department_add": "Department added", "department_edit": "Department updated",
    "department_delete": "Department deleted", "viva_add": "Viva record added",
    "viva_edit": "Viva record updated", "viva_delete": "Viva record deleted",
    "viva_bulk": "Viva records added (bulk)",
}


@app.route("/admin/notifications")
@admin_required
def admin_notifications():
    """In-portal notifications for the DAS admin."""
    db = get_db()
    notes = [dict(r) for r in db.fetchall(
        "SELECT * FROM admin_notifications ORDER BY id DESC LIMIT 50")]
    tasks = [dict(r) for r in db.fetchall(
        "SELECT actor, action, detail, created_at FROM activity_log"
        " WHERE action != 'login' ORDER BY id DESC LIMIT 50")]
    for task in tasks:
        task["label"] = TASK_LABELS.get(task["action"],
                                        task["action"].replace("_", " ").title())
    db.execute("UPDATE admin_notifications SET is_read=1")
    db.commit()
    return render_template("admin_notifications.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, notes=notes, tasks=tasks)


@app.route("/admin/viva/card/<int:vid>")
@admin_required
def admin_viva_card(vid):
    """View viva result card (admin)."""
    card = build_viva_card(vid)
    if not card:
        flash("Result card is available once marks are entered.")
        return redirect(url_for("admin_viva"))
    return render_template("viva_card.html", uni=UNI_NAME, title=PORTAL_TITLE,
                           base="admin_base.html", card=card,
                           back_url=url_for("admin_viva"),
                           pdf_url=url_for("admin_viva_card_pdf", vid=vid))


@app.route("/admin/viva/card/<int:vid>/pdf")
@admin_required
def admin_viva_card_pdf(vid):
    """Download viva result card PDF (admin)."""
    card = build_viva_card(vid)
    if not card:
        flash("Result card is available once marks are entered.")
        return redirect(url_for("admin_viva"))
    buf = build_viva_pdf(card)
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"Viva_Result_Card_{card['reg_no']}.pdf")


@app.route("/admin/reports")
@admin_required
def admin_reports():
    """Filter the student list and export it as Excel or PDF."""
    return render_template(
        "admin_reports.html", uni=UNI_NAME, title=PORTAL_TITLE,
        status_list=STATUS_LIST, sessions=distinct_values("session"),
        degrees=distinct_values("degree"), genders=GENDER_OPTS,
        folders=FOLDER_OPTS)


@app.context_processor
def _inject_dept_notif():
    try:
        if request.path.startswith("/dept") and "dept_id" in session:
            return {"dept_unread": dept_unread_count(session["dept_id"])}
        if request.path.startswith("/admin") and session.get("admin_ok"):
            return {"admin_unread": admin_unread_count()}
        if request.path.startswith("/student") and session.get("student_id"):
            return {"student_unread": student_unread_count(session["student_id"])}
    except Exception:
        pass
    return {}


@app.context_processor
def inject_admin_counts():
    """Pending-upload + new-viva badges for the admin sidebar."""
    if session.get("admin"):
        try:
            db = get_db()
            row = db.fetchone(
                "SELECT COUNT(*) AS n FROM dept_uploads WHERE status='pending'")
            n = (row["n"] if isinstance(row, dict) else row[0]) if row else 0
            row = db.fetchone(
                "SELECT COUNT(*) AS n FROM viva_records WHERE admin_seen=0")
            nv = (row["n"] if isinstance(row, dict) else row[0]) if row else 0
            return {"pending_uploads": n, "new_vivas": nv}
        except Exception:
            pass
    return {"pending_uploads": 0, "new_vivas": 0}


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
            "Not Completed", "Not Received", "Not Received",
            "Not Received", "", "Continue", "N/A"]


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
    db = get_db()
    uploads = [dict(r) for r in db.fetchall(
        "SELECT id, file_name, row_count, status, admin_comment, created_at,"
        " reviewed_at FROM dept_uploads WHERE department_id=?"
        " ORDER BY id DESC", (dept["id"],))]
    def _n(sql, params):
        row = db.fetchone(sql, params)
        return (row["n"] if isinstance(row, dict) else row[0]) if row else 0
    stats = {
        "students": _n("SELECT COUNT(*) AS n FROM students WHERE department=?",
                       (dept["name"],)),
        "viva_done": _n("SELECT COUNT(*) AS n FROM viva_records v"
                        " JOIN students s ON s.id=v.student_id"
                        " WHERE s.department=? AND v.result='Pass'",
                        (dept["name"],)),
        "viva_pending": _n("SELECT COUNT(*) AS n FROM viva_records v"
                           " JOIN students s ON s.id=v.student_id"
                           " WHERE s.department=? AND v.result='Pending'",
                           (dept["name"],)),
        "uploads_pending": _n("SELECT COUNT(*) AS n FROM dept_uploads"
                              " WHERE department_id=? AND status='pending'",
                              (dept["id"],)),
    }
    return render_template("dept_dashboard.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, dept=dept, uploads=uploads,
                           stats=stats)


@app.route("/dept/students")
@dept_required
def dept_students():
    """Read-only paginated list of the department's approved students."""
    dept = current_dept()
    db = get_db()
    page = max(1, int(request.args.get("page", 1) or 1))
    per = 15
    q = (request.args.get("q") or "").strip()
    op = "ILIKE" if USE_PG else "LIKE"
    where = "department=?"
    params = [dept["name"]]
    if q:
        where += f" AND (name {op} ? OR reg_no {op} ?)"
        params += [f"%{q}%", f"%{q}%"]
    row = db.fetchone(f"SELECT COUNT(*) AS n FROM students WHERE {where}",
                      tuple(params))
    total = (row["n"] if isinstance(row, dict) else row[0]) if row else 0
    pages = max(1, (total + per - 1) // per)
    page = min(page, pages)
    students = [dict(r) for r in db.fetchall(
        f"SELECT id, name, reg_no, degree, session, status, folder_file"
        f" FROM students WHERE {where} ORDER BY name LIMIT ? OFFSET ?",
        tuple(params) + (per, (page - 1) * per))]
    return render_template("dept_students.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, dept=dept, students=students,
                           page=page, pages=pages, total=total, q=q)


@app.route("/dept/students/delete-selected", methods=["POST"])
@dept_required
def dept_students_delete_selected():
    """Delete selected students — only from this department."""
    dept = current_dept()
    ids = [int(x) for x in request.form.getlist("sel") if x.isdigit()]
    if not ids:
        flash("No students selected.")
        return redirect(url_for("dept_students"))
    db = get_db()
    ph = ", ".join(["?"] * len(ids))
    # scope to this department's students only
    rows = db.fetchall(
        f"SELECT id FROM students WHERE department=? AND id IN ({ph})",
        (dept["name"],) + tuple(ids))
    scoped = [r["id"] if isinstance(r, dict) else r[0] for r in rows]
    if not scoped:
        flash("No students selected.")
        return redirect(url_for("dept_students"))
    ph2 = ", ".join(["?"] * len(scoped))
    db.execute(f"DELETE FROM viva_records WHERE student_id IN ({ph2})", scoped)
    db.execute(f"DELETE FROM documents WHERE student_id IN ({ph2})", scoped)
    db.execute(f"DELETE FROM students WHERE id IN ({ph2})", scoped)
    db.commit()
    log_activity(f"dept:{dept['name']}", "student_bulk_delete",
                 f"{len(scoped)} student(s) deleted")
    flash(f"Deleted {len(scoped)} student(s) and their documents.")
    return redirect(url_for("dept_students"))


@app.route("/dept/students/delete-all", methods=["POST"])
@dept_required
def dept_students_delete_all():
    """Delete ALL of this department's students."""
    dept = current_dept()
    db = get_db()
    ids = [r["id"] if isinstance(r, dict) else r[0] for r in db.fetchall(
        "SELECT id FROM students WHERE department=?", (dept["name"],))]
    if ids:
        ph = ", ".join(["?"] * len(ids))
        db.execute(f"DELETE FROM viva_records WHERE student_id IN ({ph})", ids)
        db.execute(f"DELETE FROM documents WHERE student_id IN ({ph})", ids)
        db.execute(f"DELETE FROM students WHERE id IN ({ph})", ids)
        db.commit()
    log_activity(f"dept:{dept['name']}", "student_delete_all",
                 "ALL department students and documents deleted")
    flash("All your department's student data has been deleted.")
    return redirect(url_for("dept_students"))


@app.route("/dept/student/<int:sid>")
@dept_required
def dept_student_profile(sid):
    """Full read-only profile of one of the department's students —
    shows the uploaded data plus every update made by the DAS office."""
    dept = current_dept()
    s = get_student(sid)
    if not s or s.get("department") != dept["name"]:
        flash("Student not found.")
        return redirect(url_for("dept_students"))
    s["coe_display"] = fmt_coe(s.get("thesis_forward_coe"))
    s["syn_at_disp"] = fmt_stamp(s.get("synopsis_at"))
    s["semi_at_disp"] = fmt_stamp(s.get("semi_final_thesis_at"))
    s["final_at_disp"] = fmt_stamp(s.get("final_thesis_at"))
    docs = get_docs(sid)
    vivas = [dict(r) for r in get_db().fetchall(
        "SELECT examiner_name, examiner_affiliation, viva_date, result,"
        " remarks FROM viva_records WHERE student_id=? ORDER BY id DESC",
        (sid,))]
    return render_template("dept_student_profile.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, dept=dept, s=s, docs=docs,
                           vivas=vivas)


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


@app.route("/dept/uploads/<int:uid>/download")
@dept_required
def dept_upload_download(uid):
    """Download one of the department's own submitted files (e.g. to fix
    and re-upload after a rejection)."""
    dept = current_dept()
    row = get_db().fetchone(
        "SELECT * FROM dept_uploads WHERE id=? AND department_id=?",
        (uid, dept["id"]))
    if not row:
        flash("Upload not found.")
        return redirect(url_for("dept_dashboard"))
    u = dict(row)
    return Response(doc_bytes(u),
                    mimetype="application/vnd.openxmlformats-officedocument."
                             "spreadsheetml.sheet",
                    headers={"Content-Disposition":
                             f'attachment; filename="{safe_filename(u.get("file_name"))}"'})


@app.route("/dept/notifications")
@dept_required
def dept_notifications():
    """In-portal notifications for the department."""
    dept = current_dept()
    db = get_db()
    notes = [dict(r) for r in db.fetchall(
        "SELECT * FROM dept_notifications WHERE department_id=?"
        " ORDER BY id DESC LIMIT 50", (dept["id"],))]
    db.execute("UPDATE dept_notifications SET is_read=1"
               " WHERE department_id=?", (dept["id"],))
    db.commit()
    return render_template("dept_notifications.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, dept=dept, notes=notes)


@app.route("/dept/viva/calendar")
@dept_required
def dept_viva_calendar():
    """Month calendar of the department's viva schedule."""
    import calendar as calmod
    dept = current_dept()
    today = datetime.now()
    try:
        year = int(request.args.get("y", today.year))
        month = int(request.args.get("m", today.month))
    except ValueError:
        year, month = today.year, today.month
    if month < 1:
        month, year = 12, year - 1
    if month > 12:
        month, year = 1, year + 1
    vivas = [dict(r) for r in get_db().fetchall(
        "SELECT v.viva_date, s.name, s.reg_no, v.result FROM viva_records v"
        " JOIN students s ON s.id=v.student_id"
        " WHERE s.department=? AND v.viva_date IS NOT NULL"
        " AND v.viva_date LIKE ? ORDER BY v.viva_date",
        (dept["name"], f"{year}-{month:02d}%"))]
    by_day = {}
    for v in vivas:
        try:
            day = int(v["viva_date"].split("-")[2])
            by_day.setdefault(day, []).append(v)
        except (ValueError, IndexError):
            pass
    weeks = calmod.monthcalendar(year, month)
    prev_m, prev_y = (12, year - 1) if month == 1 else (month - 1, year)
    next_m, next_y = (1, year + 1) if month == 12 else (month + 1, year)
    upcoming = [dict(r) for r in get_db().fetchall(
        "SELECT v.viva_date, s.name, s.reg_no, v.result, v.examiner_name"
        " FROM viva_records v JOIN students s ON s.id=v.student_id"
        " WHERE s.department=? AND v.viva_date >= ?"
        " ORDER BY v.viva_date LIMIT 10",
        (dept["name"], today.strftime("%Y-%m-%d")))]
    return render_template("dept_viva_calendar.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, dept=dept, weeks=weeks,
                           by_day=by_day, year=year, month=month,
                           month_name=calmod.month_name[month],
                           prev_m=prev_m, prev_y=prev_y,
                           next_m=next_m, next_y=next_y,
                           upcoming=upcoming)


@app.route("/dept/students/export")
@dept_required
def dept_students_export():
    """Excel export of own students."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    dept = current_dept()
    students = [dict(r) for r in get_db().fetchall(
        "SELECT * FROM students WHERE department=? ORDER BY name",
        (dept["name"],))]
    wb = Workbook()
    ws = wb.active
    ws.title = "Students"
    headers = ["Name", "Reg.No", "CNIC", "Gender", "Degree", "Session",
               "Folder/File", "Synopsis", "Synopsis Received On",
               "Semi-Final Thesis", "Semi-Final Received On", "Final Thesis",
               "Final Received On", "Thesis Forward (COE)", "Status"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2D3290")
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
    for s in students:
        _syn = fmt_stamp(s.get("synopsis_at"))
        _semi = fmt_stamp(s.get("semi_final_thesis_at"))
        _fin = fmt_stamp(s.get("final_thesis_at"))
        ws.append([s.get("name"), s.get("reg_no"), s.get("cnic"),
                   s.get("gender"), s.get("degree"), s.get("session"),
                   s.get("folder_file"), s.get("synopsis"),
                   "" if _syn == "\u2014" else _syn,
                   s.get("semi_final_thesis"),
                   "" if _semi == "\u2014" else _semi,
                   s.get("final_thesis"),
                   "" if _fin == "\u2014" else _fin,
                   parse_coe(s.get("thesis_forward_coe")) or "",
                   s.get("status")])
    widths = [24, 16, 18, 10, 22, 12, 14, 12, 21, 16, 21, 14, 21, 18, 16]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = Response(buf.getvalue(),
                    mimetype="application/vnd.openxmlformats-officedocument."
                             "spreadsheetml.sheet")
    resp.headers["Content-Disposition"] = \
        "attachment; filename=my_students_export.xlsx"
    return resp


@app.route("/dept/viva/bulk", methods=["GET", "POST"])
@dept_required
def dept_viva_bulk():
    """Schedule viva for multiple students at once (this department only)."""
    dept = current_dept()
    db = get_db()
    if request.method == "POST":
        ids = [int(x) for x in request.form.getlist("sel") if x.isdigit()]
        examiner = request.form.get("examiner_name", "").strip()
        affiliation = request.form.get("examiner_affiliation", "").strip()
        viva_date = request.form.get("viva_date", "").strip()
        result = request.form.get("result", "Pending")
        remarks = request.form.get("remarks", "").strip()
        total_marks = request.form.get("total_marks", "120").strip() or "120"
        try:
            total_marks = int(total_marks)
        except ValueError:
            total_marks = 120
        if not ids:
            flash("No students selected.")
            return redirect(url_for("dept_viva_bulk"))
        if not examiner:
            flash("Examiner name is required.")
            return redirect(url_for("dept_viva_bulk"))
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        added, skipped = 0, []
        for sid in ids:
            st = db.fetchone(
                "SELECT * FROM students WHERE id=? AND department=?",
                (sid, dept["name"]))
            if not st:
                continue
            st = dict(st)
            if db.fetchone("SELECT id FROM viva_records WHERE student_id=?",
                           (sid,)):
                skipped.append(f"{st['name']} ({st['reg_no']})")
                continue
            db.execute(
                "INSERT INTO viva_records (student_id, examiner_name,"
                " examiner_affiliation, viva_date, result, remarks,"
                " updated_by, created_at, total_marks)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, examiner, affiliation, viva_date or None, result,
                 remarks, dept["name"], now, total_marks))
            notify_student(st["id"], "viva", "Viva scheduled",
                           f"Your viva has been scheduled — examiner"
                           f" {examiner}"
                           + (f" on {viva_date}" if viva_date else "")
                           + f". Result: {result}",
                           url_for("student_viva"))
            added += 1
        db.commit()
        if added:
            log_activity(f"dept:{dept['name']}", "viva_bulk",
                         f"{added} viva record(s) scheduled")
            notify_dept(dept["id"], "viva",
                        f"Viva scheduled for {added} student(s)",
                        f"Examiner {examiner}"
                        + (f" on {viva_date}" if viva_date else ""),
                        url_for("dept_viva"))
            notify_admin("viva", f"Viva entry by {dept['name']}",
                         f"{added} student(s) — examiner {examiner}"
                         + (f" on {viva_date}" if viva_date else ""),
                         url_for("admin_viva", department=dept["name"]))
        msg = f"Viva scheduled for {added} student(s)." if added else \
            "No new viva records created."
        if skipped:
            msg += " Already had viva (skipped): " + ", ".join(skipped) + \
                " — edit existing records instead."
        flash(msg)
        return redirect(url_for("dept_viva"))
    students = [dict(r) for r in db.fetchall(
        "SELECT s.id, s.name, s.reg_no, s.degree FROM students s"
        " LEFT JOIN viva_records v ON v.student_id=s.id"
        " WHERE s.department=? AND v.id IS NULL ORDER BY s.name",
        (dept["name"],))]
    return render_template("dept_viva_bulk.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, dept=dept, students=students,
                           results=VIVA_RESULTS)


@app.route("/dept/viva/card/<int:vid>")
@dept_required
def dept_viva_card(vid):
    """View viva result card (department, own students only)."""
    dept = current_dept()
    row = get_db().fetchone(
        "SELECT v.id FROM viva_records v JOIN students s ON s.id=v.student_id"
        " WHERE v.id=? AND s.department=?", (vid, dept["name"]))
    if not row:
        flash("Viva record not found.")
        return redirect(url_for("dept_viva"))
    card = build_viva_card(vid)
    if not card:
        flash("Result card is available once marks are entered.")
        return redirect(url_for("dept_viva"))
    return render_template("viva_card.html", uni=UNI_NAME, title=PORTAL_TITLE,
                           base="dept_base.html", card=card, dept=dept,
                           back_url=url_for("dept_viva"),
                           pdf_url=url_for("dept_viva_card_pdf", vid=vid))


@app.route("/dept/viva/card/<int:vid>/pdf")
@dept_required
def dept_viva_card_pdf(vid):
    """Download viva result card PDF (department, own students only)."""
    dept = current_dept()
    row = get_db().fetchone(
        "SELECT v.id FROM viva_records v JOIN students s ON s.id=v.student_id"
        " WHERE v.id=? AND s.department=?", (vid, dept["name"]))
    if not row:
        flash("Viva record not found.")
        return redirect(url_for("dept_viva"))
    card = build_viva_card(vid)
    if not card:
        flash("Result card is available once marks are entered.")
        return redirect(url_for("dept_viva"))
    buf = build_viva_pdf(card)
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"Viva_Result_Card_{card['reg_no']}.pdf")


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
    widths = [18, 22, 22, 16, 10, 22, 12, 18, 12, 14, 14, 18, 16, 30]
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
        total_marks = request.form.get("total_marks", "120").strip() or "120"
        obtained_raw = request.form.get("obtained_marks", "").strip()
        next_viva_date = request.form.get("next_viva_date", "").strip()
        st = db.fetchone(
            "SELECT * FROM students WHERE id=? AND department=?",
            (sid, dept["name"]))
        if not st or not examiner:
            flash("Select one of your department's students and enter the"
                  " examiner name.")
            return redirect(url_for("dept_viva"))
        existing = db.fetchone("SELECT id FROM viva_records WHERE student_id=?",
                               (sid,))
        if existing:
            st = dict(st)
            flash(f"Viva already exists for {st['name']} ({st['reg_no']}) —"
                  " you can edit it from the list below, but cannot add"
                  " another one.")
            return redirect(url_for("dept_viva"))
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        try:
            total_marks = int(total_marks)
        except ValueError:
            total_marks = 120
        obtained_marks = None
        if obtained_raw:
            try:
                obtained_marks = int(obtained_raw)
            except ValueError:
                pass
        db.execute(
            "INSERT INTO viva_records (student_id, examiner_name,"
            " examiner_affiliation, viva_date, result, remarks, updated_by,"
            " created_at, total_marks, obtained_marks, next_viva_date)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, examiner, affiliation, viva_date or None, result,
             remarks, dept["name"], now, total_marks, obtained_marks,
             next_viva_date or None))
        db.commit()
        st = dict(st)
        log_activity(f"dept:{dept['name']}", "viva_add",
                     f"{st['name']} ({st['reg_no']}): {examiner},"
                     f" {viva_date or 'no date'}, {result}")
        notify_dept(dept["id"], "viva", "Viva scheduled",
                    f"{st['name']} ({st['reg_no']}) — viva recorded.",
                    url_for("dept_viva"))
        notify_admin("viva", f"Viva entry by {dept['name']}",
                     f"{st['name']} ({st['reg_no']}) — examiner {examiner}"
                     + (f" on {viva_date}" if viva_date else ""),
                     url_for("admin_viva", department=dept["name"]))
        notify_student(st["id"], "viva", "Viva scheduled",
                       f"Your viva has been scheduled — examiner {examiner}"
                       + (f" on {viva_date}" if viva_date else "")
                       + f". Result: {result}"
                       + (f" ({obtained_marks}/{total_marks} marks)"
                          if obtained_marks is not None else ""),
                       url_for("student_viva"))
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



@app.route("/dept/viva/<int:vid>/edit", methods=["GET", "POST"])
@dept_required
def dept_viva_edit(vid):
    """Edit a viva record (examiner, date, result, remarks)."""
    dept = current_dept()
    db = get_db()
    row = db.fetchone(
        "SELECT v.*, s.name AS student_name, s.reg_no FROM viva_records v"
        " JOIN students s ON s.id=v.student_id"
        " WHERE v.id=? AND s.department=?", (vid, dept["name"]))
    if not row:
        flash("Viva record not found.")
        return redirect(url_for("dept_viva"))
    v = dict(row)
    if request.method == "POST":
        examiner = request.form.get("examiner_name", "").strip()
        affiliation = request.form.get("examiner_affiliation", "").strip()
        viva_date = request.form.get("viva_date", "").strip()
        result = request.form.get("result", "Pending")
        remarks = request.form.get("remarks", "").strip()
        total_marks = request.form.get("total_marks", "120").strip() or "120"
        obtained_raw = request.form.get("obtained_marks", "").strip()
        next_viva_date = request.form.get("next_viva_date", "").strip()
        if not examiner:
            flash("Examiner name is required.")
            return redirect(url_for("dept_viva_edit", vid=vid))
        try:
            total_marks = int(total_marks)
        except ValueError:
            total_marks = 120
        obtained_marks = None
        if obtained_raw:
            try:
                obtained_marks = int(obtained_raw)
            except ValueError:
                pass
        db.execute(
            "UPDATE viva_records SET examiner_name=?, examiner_affiliation=?,"
            " viva_date=?, result=?, remarks=?, updated_by=?, total_marks=?,"
            " obtained_marks=?, next_viva_date=? WHERE id=?",
            (examiner, affiliation, viva_date or None, result, remarks,
             dept["name"], total_marks, obtained_marks,
             next_viva_date or None, vid))
        db.commit()
        notify_student(v["student_id"], "viva", "Viva record updated",
                       f"Your viva record was updated — result: {result}"
                       + (f" ({obtained_marks}/{total_marks} marks)"
                          if obtained_marks is not None else "")
                       + (f", next viva on {next_viva_date}"
                          if next_viva_date else ""),
                       url_for("student_viva"))
        log_activity(f"dept:{dept['name']}", "viva_edit",
                     f"record #{vid} ({v['student_name']}) updated")
        flash("Viva record updated.")
        return redirect(url_for("dept_viva"))
    return render_template("dept_viva_edit.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, dept=dept, v=v,
                           results=VIVA_RESULTS)


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
               "Female", "MPhil Biochemistry", "F. 23-25", "Completed",
               "Received", "Received", "Not Received", "2026-05-10",
               "Continue", "N/A"])
    widths = [18, 22, 22, 16, 10, 22, 12, 18, 12, 14, 14, 18, 16, 30]
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
        if pick(f.get("status"), STATUS_LIST, "Continue") == "Pass Out":
            f = dict(f)
            f["folder_file"] = "Completed"
            f["synopsis"] = f["semi_final_thesis"] = f["final_thesis"] = "Received"
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
    s["syn_at_disp"] = fmt_stamp(s.get("synopsis_at"))
    s["syn_at_local"] = dt_local(s.get("synopsis_at"))
    s["semi_at_disp"] = fmt_stamp(s.get("semi_final_thesis_at"))
    s["final_at_disp"] = fmt_stamp(s.get("final_thesis_at"))
    s["semi_at_local"] = dt_local(s.get("semi_final_thesis_at"))
    s["final_at_local"] = dt_local(s.get("final_thesis_at"))
    docs = get_docs(sid)
    vivas = [dict(r) for r in get_db().fetchall(
        "SELECT * FROM viva_records WHERE student_id=? ORDER BY id DESC",
        (sid,))]
    return render_template("student_profile.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, s=s, docs=docs, vivas=vivas,
                           status_list=STATUS_LIST, genders=GENDER_OPTS,
                           folders=FOLDER_OPTS, received=RECEIVED_OPTS,
                           departments=dept_names())


@app.route("/admin/student/<int:sid>/save", methods=["POST"])
@admin_required
def admin_student_save(sid):
    s = get_student(sid)
    if not s:
        return "Student not found.", 404
    f = request.form
    nxt = (f.get("next") or "").strip()
    if not nxt.startswith("/admin/"):
        nxt = ""
    name = f.get("name", "").strip()
    reg_no = f.get("reg_no", "").strip()
    cnic = norm_cnic(f.get("cnic", ""))
    if not name or not reg_no or not cnic:
        flash("Name, Registration No. and CNIC are required.")
        return redirect(nxt or url_for("admin_student_profile", sid=sid))
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    syn_status = pick(f.get("synopsis"), RECEIVED_OPTS, "Not Received")
    syn_at = _thesis_stamp(syn_status, f.get("synopsis_at"),
                           s.get("synopsis"), s.get("synopsis_at"))
    semi_status = pick(f.get("semi_final_thesis"), RECEIVED_OPTS, "Not Received")
    final_status = pick(f.get("final_thesis"), RECEIVED_OPTS, "Not Received")
    semi_at = _thesis_stamp(semi_status, f.get("semi_final_thesis_at"),
                            s.get("semi_final_thesis"),
                            s.get("semi_final_thesis_at"))
    final_at = _thesis_stamp(final_status, f.get("final_thesis_at"),
                             s.get("final_thesis"),
                             s.get("final_thesis_at"))
    if pick(f.get("status"), STATUS_LIST, "Continue") == "Pass Out":
        syn_status = semi_status = final_status = "Received"
        syn_at, semi_at, final_at = (s.get("synopsis_at"),
                                     s.get("semi_final_thesis_at"),
                                     s.get("final_thesis_at"))
        f = dict(f)
        f["folder_file"] = "Completed"
    dept = (f.get("department") or "").strip()
    if dept and dept not in set(dept_names()):
        dept = (s.get("department") or "").strip()  # ignore tampered values
    try:
        db.execute(
            """UPDATE students SET cnic=?, name=?, father_name=?, reg_no=?,
               gender=?, degree=?, session=?, folder_file=?, comments_missing=?,
               synopsis=?, synopsis_at=?, semi_final_thesis=?, final_thesis=?,
               semi_final_thesis_at=?, final_thesis_at=?,
               thesis_forward_coe=?, status=?, department=?, updated_at=?
               WHERE id=?""",
            (cnic, name, f.get("father_name", "").strip(), reg_no,
             f.get("gender", "").strip(), f.get("degree", "").strip(),
             f.get("session", "").strip(),
             pick(f.get("folder_file"), FOLDER_OPTS, "Not Completed"),
             f.get("comments_missing", "").strip() or "N/A",
             syn_status, syn_at,
             semi_status, final_status, semi_at, final_at,
             parse_coe(f.get("thesis_forward_coe")) or None,
             pick(f.get("status"), STATUS_LIST, "Continue"),
             dept, now, sid))
        db.commit()
    except Exception as e:
        flash(f"Could not save (maybe duplicate Reg.No or CNIC): {e}")
        return redirect(url_for("admin_student_profile", sid=sid))
    log_activity("admin", "student_edit", f"id={sid} ({name}) record updated")
    try:
        _dep_row = db.fetchone("SELECT department FROM students WHERE id=?", (sid,))
        _dept_name = (_dep_row["department"] if isinstance(_dep_row, dict)
                      else _dep_row[0]) if _dep_row else None
        if _dept_name:
            _d = db.fetchone("SELECT id FROM departments WHERE name=?",
                             (_dept_name,))
            if _d:
                _did = _d["id"] if isinstance(_d, dict) else _d[0]
                notify_dept(_did, "student", "Record updated by DAS",
                            f"{name} ({reg_no}) — the DAS office updated"
                            " this student record.",
                            url_for("dept_student_profile", sid=sid))
    except Exception:
        pass
    flash("Student record updated.")
    return redirect(nxt or url_for("admin_student_profile", sid=sid))


@app.route("/admin/students/delete-selected", methods=["POST"])
@admin_required
def admin_students_delete_selected():
    ids = [int(x) for x in request.form.getlist("sel") if x.isdigit()]
    if not ids:
        flash("No students selected.")
        return redirect(url_for("admin_students"))
    db = get_db()
    ph = ", ".join(["?"] * len(ids))
    db.execute(f"DELETE FROM viva_records WHERE student_id IN ({ph})", ids)
    db.execute(f"DELETE FROM documents WHERE student_id IN ({ph})", ids)
    db.execute(f"DELETE FROM students WHERE id IN ({ph})", ids)
    db.commit()
    log_activity("admin", "student_bulk_delete", f"{len(ids)} student(s) deleted")
    flash(f"Deleted {len(ids)} student(s) and their documents.")
    return redirect(url_for("admin_students"))


@app.route("/admin/student/<int:sid>/json")
@admin_required
def admin_student_json(sid):
    """Student record as JSON for the edit popup."""
    s = get_student(sid)
    if not s:
        return {"ok": False, "error": "Student not found."}, 404
    keys = ("id", "cnic", "name", "father_name", "reg_no", "gender",
            "degree", "session", "department", "status", "folder_file",
            "comments_missing", "synopsis", "semi_final_thesis",
            "final_thesis")
    out = {k: (s.get(k) or "") for k in keys}
    out["coe_iso"] = parse_coe(s.get("thesis_forward_coe"))
    out["synopsis_at"] = dt_local(s.get("synopsis_at"))
    out["semi_final_thesis_at"] = dt_local(s.get("semi_final_thesis_at"))
    out["final_thesis_at"] = dt_local(s.get("final_thesis_at"))
    out["ok"] = True
    return out


@app.route("/admin/students/search.json")
@admin_required
def admin_students_search():
    """Live search for the student list."""
    rows = query_students(request.args)
    out = []
    for s in rows[:200]:
        st = s.get("status") or ""
        out.append({
            "id": s["id"], "name": s.get("name") or "",
            "reg_no": s.get("reg_no") or "", "gender": s.get("gender") or "",
            "degree": s.get("degree") or "", "session": s.get("session") or "",
            "department": s.get("department") or "",
            "folder_file": s.get("folder_file") or "",
            "comments_missing": s.get("comments_missing") or "",
            "synopsis": s.get("synopsis") or "",
            "semi_final_thesis": s.get("semi_final_thesis") or "",
            "final_thesis": s.get("final_thesis") or "",
            "coe_display": fmt_coe(s.get("thesis_forward_coe")),
            "syn_at": fmt_stamp(s.get("synopsis_at")),
            "semi_at": fmt_stamp(s.get("semi_final_thesis_at")),
            "final_at": fmt_stamp(s.get("final_thesis_at")),
            "status": st, "pill": status_pill_class(st),
        })
    return {"ok": True, "total": len(rows), "students": out}


@app.route("/admin/students/bulk-update", methods=["POST"])
@admin_required
def admin_students_bulk_update():
    """Bulk-set status and/or COE date for the selected students."""
    ids = [int(x) for x in request.form.getlist("sel") if x.isdigit()]
    if not ids:
        flash("No students selected — tick the boxes first.")
        return redirect(url_for("admin_students"))
    status = (request.form.get("bulk_status") or "").strip()
    coe_raw = (request.form.get("bulk_coe") or "").strip()
    sets, vals, done = [], [], []
    if status:
        vals.append(pick(status, STATUS_LIST, "Continue"))
        sets.append("status=?")
        done.append(f"status '{vals[-1]}'")
        if vals[-1] == "Pass Out":
            sets += ["folder_file=?", "synopsis=?",
                     "semi_final_thesis=?", "final_thesis=?"]
            vals += ["Completed", "Received", "Received", "Received"]
            done.append("folder + synopsis + theses marked complete")
    if coe_raw:
        coe = parse_coe(coe_raw)
        if coe:
            sets.append("thesis_forward_coe=?")
            vals.append(coe)
            done.append(f"COE date {coe}")
    if not sets:
        flash("Choose a status or a COE date to apply first.")
        return redirect(url_for("admin_students"))
    db = get_db()
    ph = ", ".join(["?"] * len(ids))
    db.execute(
        f"UPDATE students SET {', '.join(sets)}, updated_at=?"
        f" WHERE id IN ({ph})",
        (*vals, datetime.now().strftime("%Y-%m-%d %H:%M"), *ids))
    db.commit()
    log_activity("admin", "student_bulk_update",
                 f"{len(ids)} student(s): " + "; ".join(done))
    flash(f"Updated {len(ids)} student(s): " + "; ".join(done) + ".")
    return redirect(url_for("admin_students"))


@app.route("/admin/students/delete-all", methods=["POST"])
@admin_required
def admin_students_delete_all():
    db = get_db()
    db.execute("DELETE FROM viva_records")
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
    db.execute("DELETE FROM viva_records WHERE student_id=?", (sid,))
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
    vivas = [dict(r) for r in get_db().fetchall(
        "SELECT examiner_name, examiner_affiliation, viva_date, result,"
        " remarks, total_marks, obtained_marks, next_viva_date"
        " FROM viva_records WHERE student_id=? ORDER BY id DESC",
        (s["id"],))]
    timeline = [
        {"label": "Synopsis", "done": s.get("synopsis") == "Received"},
        {"label": "Semi-Final Thesis",
         "done": s.get("semi_final_thesis") == "Received"},
        {"label": "Final Thesis", "done": s.get("final_thesis") == "Received"},
        {"label": "Viva Scheduled", "done": bool(vivas)},
        {"label": "Viva Passed",
         "done": any(v.get("result") == "Pass" for v in vivas)},
    ]
    return render_template("student_dashboard.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, s=s, docs=docs, vivas=vivas,
                           timeline=timeline)


@app.route("/student/record")
@student_required
def student_record():
    """Dedicated academic record page."""
    s = current_student()
    if not s:
        session.pop("student_id", None)
        return redirect(url_for("student_login"))
    s["coe_display"] = fmt_coe(s.get("thesis_forward_coe"))
    return render_template("student_record.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, s=s)


@app.route("/student/viva")
@student_required
def student_viva():
    """Dedicated viva details page — always visible, with an empty state."""
    s = current_student()
    if not s:
        session.pop("student_id", None)
        return redirect(url_for("student_login"))
    vivas = [dict(r) for r in get_db().fetchall(
        "SELECT id, examiner_name, examiner_affiliation, viva_date, result,"
        " remarks, total_marks, obtained_marks, next_viva_date"
        " FROM viva_records WHERE student_id=? ORDER BY id DESC",
        (s["id"],))]
    return render_template("student_viva.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, s=s, vivas=vivas)


@app.route("/student/notifications")
@student_required
def student_notifications():
    """In-portal notifications for the student."""
    s = current_student()
    db = get_db()
    notes = [dict(r) for r in db.fetchall(
        "SELECT * FROM student_notifications WHERE student_id=?"
        " ORDER BY id DESC LIMIT 50", (s["id"],))]
    db.execute("UPDATE student_notifications SET is_read=1 WHERE student_id=?",
               (s["id"],))
    db.commit()
    return render_template("student_notifications.html", uni=UNI_NAME,
                           title=PORTAL_TITLE, s=s, notes=notes)


@app.route("/student/viva/card/<int:vid>")
@student_required
def student_viva_card(vid):
    """View own viva result card."""
    s = current_student()
    row = get_db().fetchone(
        "SELECT id FROM viva_records WHERE id=? AND student_id=?",
        (vid, s["id"]))
    if not row:
        flash("Viva record not found.")
        return redirect(url_for("student_viva"))
    card = build_viva_card(vid)
    if not card:
        flash("Your result card will be available once the department"
              " enters your marks.")
        return redirect(url_for("student_viva"))
    return render_template("viva_card.html", uni=UNI_NAME, title=PORTAL_TITLE,
                           base="student_base.html", card=card, s=s,
                           back_url=url_for("student_viva"),
                           pdf_url=url_for("student_viva_card_pdf", vid=vid))


@app.route("/student/viva/card/<int:vid>/pdf")
@student_required
def student_viva_card_pdf(vid):
    """Download own viva result card PDF."""
    s = current_student()
    row = get_db().fetchone(
        "SELECT id FROM viva_records WHERE id=? AND student_id=?",
        (vid, s["id"]))
    if not row:
        flash("Viva record not found.")
        return redirect(url_for("student_viva"))
    card = build_viva_card(vid)
    if not card:
        flash("Your result card will be available once the department"
              " enters your marks.")
        return redirect(url_for("student_viva"))
    buf = build_viva_pdf(card)
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"Viva_Result_Card_{card['reg_no']}.pdf")


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


STUDENT_DOC_TYPES = ["Complete File (Merged PDF)", "Synopsis", "Thesis"]


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
        doc_type = request.form.get("doc_type", "Complete File (Merged PDF)")
        if doc_type not in STUDENT_DOC_TYPES:
            doc_type = "Complete File (Merged PDF)"
        if not file or not file.filename:
            flash("Please choose a PDF file to upload.")
        elif not file.filename.lower().endswith(".pdf"):
            flash("Only PDF files are allowed.")
        else:
            data = file.read()
            if not data.startswith(b"%PDF"):
                flash("This file does not look like a valid PDF.")
            elif len(data) > 25 * 1024 * 1024:
                flash("File is too large — maximum 25 MB.")
            else:
                db = get_db()
                db.execute(
                    """INSERT INTO documents (student_id, doc_label, file_name,
                       mime_type, file_data, uploaded_by, uploaded_at)
                       VALUES (?, ?, ?, 'application/pdf', ?, 'student', ?)""",
                    (s["id"], doc_type, secure_filename(file.filename), data,
                     datetime.now().strftime("%Y-%m-%d %H:%M")))
                db.commit()
                log_activity(f"student:{s['reg_no']}", "doc_upload",
                             f"{doc_type} uploaded by student")
                flash(f"Your {doc_type.lower()} has been uploaded and saved"
                      " to your office record. You can upload again if"
                      " needed \u2014 old versions are kept.")
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
