"""Workflow foundation for the CUVAS Postgraduate Central System (Phase 1).

Keeps the student's lifecycle in SEPARATE status dimensions (spec section
3) instead of one compressed status field, and provides the workflow
event timeline + task helpers every later phase builds on.

Design rules:
- The legacy ``students.status`` column stays authoritative for all
  existing pages. Dimensions are derived from the same facts (legacy
  status, submission states, COE date, viva records) and are only ever
  written by :func:`sync_student_dimensions`, so the two can never drift
  apart silently.
- Every dimension change is recorded in ``workflow_events`` with actor
  and timestamp. Nothing here invents an office action: a dimension only
  moves when the underlying recorded facts move.
- All functions take the app's DB wrapper (``?`` placeholders) and never
  commit on their own except where noted; callers own transactions.
"""
from datetime import datetime

# column -> (label, allowed values)
DIMENSIONS = {
    "academic_status": ("Academic", ["Active", "Freeze", "Resumed",
                                     "Completed", "Withdrawn", "Cancelled"]),
    "das_status": ("DAS Processing", ["Not Received", "Received",
                                      "Under Verification", "Returned",
                                      "Verified", "Forwarded",
                                      "Action Required", "Closed"]),
    "thesis_status": ("Thesis", ["Not Due", "In Preparation", "Submitted",
                                 "Under Scrutiny", "Forwarded",
                                 "Under Evaluation", "Corrections Required",
                                 "Accepted"]),
    "viva_status": ("Viva", ["Not Scheduled", "Scheduled", "Conducted",
                             "Result Awaited", "Passed", "Failed",
                             "Deferred"]),
    "result_status": ("Result", ["Awaited", "Received", "Under Verification",
                                 "Approved", "Notified"]),
    "degree_status": ("Degree", ["Not Eligible", "Pending Requirements",
                                 "Eligible", "Approved", "Issued"]),
}

TASK_ROLES = ["DAS", "Department", "Supervisor", "Controller", "Student"]
TASK_STATUSES = ["Pending", "Done", "Cancelled"]


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _received(v):
    return (v or "").strip().lower() == "received"


def _completed(v):
    return (v or "").strip().lower() == "completed"


_THESIS_ORDER = ["Not Due", "In Preparation", "Submitted",
                 "Under Scrutiny", "Forwarded", "Under Evaluation",
                 "Corrections Required", "Accepted"]
_RESULT_ORDER = ["Awaited", "Received", "Under Verification",
                 "Approved", "Notified"]


def _at_least(current, floor, order):
    try:
        return order[max(order.index(current), order.index(floor))]
    except ValueError:
        return current


def derive_dimensions(s, has_viva_pass=False, has_viva_record=False,
                      case=None):
    """Derive the six status dimensions from a student row dict.

    ``s`` needs: status, synopsis, semi_final_thesis, final_thesis,
    folder_file, thesis_forward_coe (already stringified or None).
    The mapping is deliberately conservative: it never claims an office
    action that the underlying records do not show (e.g. a Pass Out
    student is degree *Eligible*, never *Issued* — issuance needs an
    authorized Phase 6 record).
    """
    status = (s.get("status") or "Continue").strip()
    coe = s.get("thesis_forward_coe")
    coe_set = bool(coe and str(coe).strip() not in ("", "None"))

    # --- academic ---
    if status == "Freeze":
        academic = "Freeze"
    elif status == "Left":
        academic = "Withdrawn"
    elif status == "Admission Cancelled":
        academic = "Cancelled"
    elif status == "Pass Out":
        academic = "Completed"
    else:
        academic = "Active"

    # --- DAS processing ---
    if status == "Pass Out":
        das = "Closed"
    elif coe_set or status == "Sent to COE":
        das = "Forwarded"
    elif status == "Complete":
        das = "Verified"
    elif (_received(s.get("synopsis")) or _received(s.get("semi_final_thesis"))
          or _received(s.get("final_thesis")) or _completed(s.get("folder_file"))):
        das = "Received"
    else:
        das = "Not Received"

    # --- thesis ---
    if status == "Pass Out":
        thesis = "Accepted"
    elif coe_set or status == "Sent to COE":
        thesis = "Forwarded"
    elif _received(s.get("final_thesis")) or _received(s.get("semi_final_thesis")):
        thesis = "Submitted"
    elif _received(s.get("synopsis")):
        thesis = "In Preparation"
    else:
        thesis = "Not Due"

    # --- viva ---
    if has_viva_pass or status == "Viva Done":
        viva = "Passed" if has_viva_pass else "Conducted"
    elif status in ("Viva Pending", "Defense Pending"):
        viva = "Scheduled"
    elif has_viva_record:
        viva = "Conducted"
    else:
        viva = "Not Scheduled"

    # --- result ---
    if status == "Pass Out" or has_viva_pass:
        result = "Approved"
    elif has_viva_record:
        result = "Received"
    else:
        result = "Awaited"

    # --- thesis case (Phase 5): the case is a recorded fact of its own;
    # the dimensions must reflect it, otherwise a declared Controller
    # result would leave Thesis/Result looking untouched until some
    # unrelated legacy field moves. The case only ever RAISES a
    # dimension to match what the offices recorded.
    if case:
        cs = (case.get("case_status") or "").strip()
        if cs in ("Result Declared", "Closed"):
            thesis = "Accepted"
        elif cs in ("Corrections Required", "Returned with Objections"):
            thesis = "Corrections Required"
        elif cs in ("Under Evaluation", "Corrections Submitted",
                    "Evaluation Complete"):
            thesis = _at_least(thesis, "Under Evaluation", _THESIS_ORDER)
        elif cs in ("Forwarded to Controller", "Received by Controller"):
            thesis = _at_least(thesis, "Forwarded", _THESIS_ORDER)
        elif cs == "Submitted to DAS":
            thesis = _at_least(thesis, "Submitted", _THESIS_ORDER)
        if cs not in ("", "Not Started", "Submitted to DAS") \
                and das in ("Not Received", "Received",
                            "Under Verification", "Returned", "Verified"):
            das = "Forwarded"
        rs = (case.get("result_status") or "").strip()
        if rs.startswith("Declared"):
            if "Pass" in rs:
                result = "Approved"
            else:
                result = _at_least(result, "Received", _RESULT_ORDER)

    # --- degree ---
    if status == "Pass Out":
        degree = "Eligible"
    elif coe_set or status == "Sent to COE":
        degree = "Pending Requirements"
    else:
        degree = "Not Eligible"

    return {
        "academic_status": academic,
        "das_status": das,
        "thesis_status": thesis,
        "viva_status": viva,
        "result_status": result,
        "degree_status": degree,
    }


def record_event(db, student_id, dimension, from_value, to_value,
                 actor, actor_role="", remarks=""):
    """Append one row to the workflow timeline. Caller commits."""
    db.execute(
        "INSERT INTO workflow_events (student_id, dimension, from_value,"
        " to_value, actor, actor_role, remarks, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (student_id, dimension, from_value or "", to_value or "",
         actor or "", actor_role or "", (remarks or "")[:500], _now()))


def sync_student_dimensions(db, sid, actor="system", actor_role="System",
                            write_events=True):
    """Recompute a student's dimensions from the recorded facts and
    persist any changes (with timeline events). Returns the list of
    (dimension, old, new) changes. Caller commits."""
    row = db.fetchone("SELECT * FROM students WHERE id=?", (sid,))
    if not row:
        return []
    s = dict(row)
    viva = db.fetchone(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN result='Pass' THEN 1 ELSE 0"
        " END) AS p FROM viva_records WHERE student_id=?", (sid,))
    has_record = bool(viva and viva["n"])
    has_pass = bool(viva and viva["p"])
    # Phase 5 thesis case (table may not exist on a half-migrated DB).
    case = None
    try:
        crow = db.fetchone(
            "SELECT * FROM thesis_cases WHERE student_id=?", (sid,))
        case = dict(crow) if crow else None
    except Exception:
        case = None
    # Synopsis reconciliation: a synopsis record with a submission date
    # (Phase 4) IS the fact that the synopsis was submitted; the legacy
    # submission field must not keep claiming "Not Received" on the
    # Overview / student journey. Only ever fills an empty legacy field.
    try:
        syn = db.fetchone(
            "SELECT * FROM synopsis_records WHERE student_id=?"
            " ORDER BY id DESC", (sid,))
        if syn and (syn["submitted_date"] or "").strip() \
                and not _received(s.get("synopsis")):
            old_syn = s.get("synopsis") or ""
            db.execute(
                "UPDATE students SET synopsis='Received' WHERE id=?",
                (sid,))
            if not (s.get("synopsis_at") or "").strip():
                db.execute(
                    "UPDATE students SET synopsis_at=? WHERE id=?",
                    (syn["submitted_date"], sid))
            s["synopsis"] = "Received"
            if write_events:
                record_event(db, sid, "synopsis", old_syn,
                             "Received", actor, actor_role,
                             "Submission field reconciled from the"
                             " synopsis record")
    except Exception:
        pass
    coe = s.get("thesis_forward_coe")
    s["thesis_forward_coe"] = "" if coe is None else str(coe)
    target = derive_dimensions(s, has_viva_pass=has_pass,
                               has_viva_record=has_record, case=case)
    changes = []
    for col, new_val in target.items():
        old_val = s.get(col)
        if col == "degree_status" and old_val == "Issued":
            # Issuance is an authorized Phase 6 record, not a derived
            # fact — derivation never un-issues a degree.
            continue
        if old_val != new_val:
            db.execute(f"UPDATE students SET {col}=? WHERE id=?",
                       (new_val, sid))
            changes.append((col, old_val, new_val))
            if write_events:
                record_event(db, sid, col, old_val, new_val, actor,
                             actor_role,
                             "Derived from recorded facts (Phase 1 sync)")
    return changes


def create_task(db, student_id, responsible_role, action, title,
                due_date="", priority="Normal", created_by="system",
                responsible_ref=""):
    """Create a pending workflow task. Caller commits. Returns task id
    when the backend supports it, else None."""
    if responsible_role not in TASK_ROLES:
        responsible_role = "DAS"
    cur = db.execute(
        "INSERT INTO workflow_tasks (student_id, responsible_role,"
        " responsible_ref, action, title, due_date, priority, status,"
        " created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending',"
        " ?, ?)",
        (student_id, responsible_role, responsible_ref or "", action,
         title, due_date or "", priority, created_by, _now()))
    try:
        return cur.lastrowid
    except Exception:
        return None


def complete_task(db, task_id, completed_by):
    """Mark a task done. Caller commits."""
    db.execute(
        "UPDATE workflow_tasks SET status='Done', completed_at=?,"
        " completed_by=? WHERE id=? AND status='Pending'",
        (_now(), completed_by, task_id))


def open_tasks_for(db, student_id):
    rows = db.execute(
        "SELECT * FROM workflow_tasks WHERE student_id=? AND"
        " status='Pending' ORDER BY due_date, id", (student_id,)).fetchall()
    return [dict(r) for r in rows]


def student_timeline(db, student_id, limit=100):
    rows = db.execute(
        "SELECT * FROM workflow_events WHERE student_id=?"
        " ORDER BY id DESC LIMIT ?", (student_id, limit)).fetchall()
    return [dict(r) for r in rows]


def ensure_task(db, student_id, responsible_role, action, title,
                due_date="", priority="Normal", created_by="system"):
    """Create a task unless an identical open one already exists
    (Advanced #1 auto-tasks). Caller commits."""
    if student_id is None:
        row = db.execute(
            "SELECT id FROM workflow_tasks WHERE student_id IS NULL"
            " AND action=? AND status='Pending'",
            (action,)).fetchone()
    else:
        row = db.execute(
            "SELECT id FROM workflow_tasks WHERE student_id=?"
            " AND action=? AND status='Pending'",
            (student_id, action)).fetchone()
    if row:
        return row["id"] if isinstance(row, dict) else row[0]
    return create_task(db, student_id, responsible_role, action, title,
                       due_date=due_date, priority=priority,
                       created_by=created_by)


def complete_open_tasks(db, student_id, action, completed_by):
    """Complete every pending task with this action for the student
    (or for general, student-less tasks when student_id is None)."""
    if student_id is None:
        db.execute(
            "UPDATE workflow_tasks SET status='Done', completed_at=?,"
            " completed_by=? WHERE student_id IS NULL AND action=?"
            " AND status='Pending'", (_now(), completed_by, action))
    else:
        db.execute(
            "UPDATE workflow_tasks SET status='Done', completed_at=?,"
            " completed_by=? WHERE student_id=? AND action=?"
            " AND status='Pending'",
            (_now(), completed_by, student_id, action))
