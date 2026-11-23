"""Department intake engine for the Central System (Phase 2).

Pure policy functions used by app.py when a department file arrives:
row validation, duplicate matching against the master student list,
and duplicate detection inside the file itself. Nothing here creates,
merges or overwrites a student record — matching only *flags* rows;
a DAS officer decides Import / Link / Skip for every flagged row.

The workbook itself is parsed by app.py's ``parse_import_records``
(which owns the header mapping shared with the admin import), so the
record dicts seen here use the canonical field names:
cnic, name, father_name, reg_no, gender, degree, session, folder_file,
comments_missing, synopsis, semi_final_thesis, final_thesis,
thesis_forward_coe, status, department.
"""

# The current system template (TEMPLATE_HEADERS in app.py) is version 1.
# When DAS issues a revised official template, bump this and keep the
# old version readable — every batch records the version it used.
TEMPLATE_VERSION = "DAS-INTAKE v1.0"

DECISIONS = ("import", "link", "skip", "pending")


def validate_record(rec):
    """Return (errors, warnings) for one parsed record dict.

    Errors block import (missing identity facts). Warnings never block;
    they tell DAS and the department what to double-check. No CUVAS
    policy is invented here — only structural completeness is checked.
    """
    errors, warnings = [], []
    name = (rec.get("name") or "").strip()
    reg_no = (rec.get("reg_no") or "").strip()
    cnic = (rec.get("cnic") or "").strip()
    if not name and not reg_no:
        errors.append("Blank row — no Name or Registration No.")
        return errors, warnings
    if not name:
        errors.append("Name is missing")
    if not reg_no:
        errors.append("Registration No. is missing")
    if not cnic:
        errors.append("CNIC is missing")
    elif len(cnic) != 13 or not cnic.isdigit():
        errors.append(f"CNIC must be 13 digits (found {len(cnic)})")
    if not (rec.get("father_name") or "").strip():
        warnings.append("Father Name is missing")
    if not (rec.get("degree") or "").strip():
        warnings.append("Degree is missing — department cannot be derived")
    if not (rec.get("session") or "").strip():
        warnings.append("Session is missing")
    gender = (rec.get("gender") or "").strip()
    if gender and gender.lower() not in ("male", "female"):
        warnings.append(f"Gender value '{gender}' is not Male/Female")
    if (rec.get("degree") or "").strip() and not (
            rec.get("department") or "").strip():
        warnings.append("Department could not be derived from the degree")
    return errors, warnings


def build_index(students):
    """Index master students for matching. ``students`` is a list of
    dicts with id, name, father_name, reg_no, cnic, status."""
    index = {"reg": {}, "cnic": {}, "namekey": {}}
    for s in students:
        reg = (s.get("reg_no") or "").strip()
        if reg:
            index["reg"].setdefault(reg, []).append(s)
        cnic = (s.get("cnic") or "").strip()
        if cnic:
            index["cnic"].setdefault(cnic, []).append(s)
        key = ((s.get("name") or "").strip().lower(),
               (s.get("father_name") or "").strip().lower())
        if key[0] and key[1]:
            index["namekey"].setdefault(key, []).append(s)
    return index


def find_matches(rec, index):
    """Possible duplicates of ``rec`` already in the master list.

    Exact Registration No. and exact CNIC matches are strong; a
    same-name + same-father-name record with different identifiers is
    'probable'. A student already matched exactly is not repeated as
    probable.
    """
    matches, seen = [], set()

    def _add(s, kind):
        if s["id"] in seen:
            return
        seen.add(s["id"])
        matches.append({"student_id": s["id"], "name": s.get("name") or "",
                        "reg_no": s.get("reg_no") or "",
                        "status": s.get("status") or "", "match": kind})

    reg = (rec.get("reg_no") or "").strip()
    if reg:
        for s in index["reg"].get(reg, []):
            _add(s, "reg_no")
    cnic = (rec.get("cnic") or "").strip()
    if cnic:
        for s in index["cnic"].get(cnic, []):
            _add(s, "cnic")
    key = ((rec.get("name") or "").strip().lower(),
           (rec.get("father_name") or "").strip().lower())
    if key[0] and key[1]:
        for s in index["namekey"].get(key, []):
            _add(s, "probable")
    return matches


def infile_flags(records):
    """Flag rows that duplicate another row of the SAME file.

    ``records`` is a list of (row_no, rec). Returns {row_no: [notes]}.
    """
    by_reg, by_cnic = {}, {}
    for row_no, rec in records:
        reg = (rec.get("reg_no") or "").strip()
        if reg:
            by_reg.setdefault(reg, []).append(row_no)
        cnic = (rec.get("cnic") or "").strip()
        if cnic:
            by_cnic.setdefault(cnic, []).append(row_no)
    flags = {}
    for reg, rows in by_reg.items():
        if len(rows) > 1:
            for r in rows:
                others = ", ".join(str(x) for x in rows if x != r)
                flags.setdefault(r, []).append(
                    f"Same Registration No. as row(s) {others} in this file")
    for cnic, rows in by_cnic.items():
        if len(rows) > 1:
            for r in rows:
                others = ", ".join(str(x) for x in rows if x != r)
                flags.setdefault(r, []).append(
                    f"Same CNIC as row(s) {others} in this file")
    return flags
