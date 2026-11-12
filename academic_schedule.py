"""Academic Schedule Generator — statutory deadline rules and date math.

Pure date logic (no Flask, no I/O): given a semester's commencement and
termination dates, compute every document's scheduled submission deadline
from the CUVAS statutes, then apply the weekend rule — a date falling on
Saturday or Sunday moves to the following Monday. The original scheduled
date is always preserved; the Adjusted columns show ONLY the dates that
were actually shifted (an em dash when nothing moved).

Rows are dicts of real dates:
    {"document": str, "statute": str,
     "scheduled": [date] or [date, date],   # 2 entries = a date range
     "adjusted":  [date] or [date, date]}
`display_row()` turns a row into the text form used by the page and all
exports. `serialize_rows()` / `deserialize_rows()` convert rows to and
from JSON-safe dicts so a schedule can be saved in the database.

Conventions (matching the approved reference schedule):
- "Nth week after commencement" = commencement + N weeks.
- Course-work on GS-2 ("4th week before end of 2nd semester"): in a
  standard 16-week semester, the fourth-from-last week begins 12 weeks
  after commencement.
- All arithmetic uses plain `date` objects — no time zones involved.
"""
from datetime import date, timedelta

# (document, submission deadline according to statutes, rule)
# rule = (anchor, weeks, days) resolved against the commencement (C) or
# termination (T) date; the Semi-final Thesis rule is a range of two rules.
RULES = [
    ("Enrollment (GS-1 Form with Normal Fee)",
     "Within 10 days after commencement of 1st semester",
     ("commencement", 0, 10)),
    ("Notification of Supervisory Committee",
     "Within 8th week after commencement of 1st semester",
     ("commencement", 8, 0)),
    ("Course-work on GS-2 Form",
     "4th week before end of 2nd semester",
     ("commencement", 12, 0)),
    ("Research Title",
     "6th week after commencement of 3rd semester",
     ("commencement", 6, 0)),
    ("Synopsis on GS-5 Form",
     "14th week after commencement of 3rd semester",
     ("commencement", 14, 0)),
    ("Submission of progress report (GS-03 form)",
     "End of 3rd semester and every semester till thesis submission",
     ("termination", 0, 0)),
    ("Semi-final Thesis",
     "60th day to 2 weeks after last semester",
     ("range", ("commencement", 0, 60), ("termination", 2, 0))),
    ("Final Thesis",
     "Within 30 days after termination of last semester",
     ("termination", 0, 30)),
]


def _resolve(rule, commencement, termination):
    anchor, weeks, days = rule
    base = commencement if anchor == "commencement" else termination
    return base + timedelta(weeks=weeks, days=days)


def adjust_weekend(d):
    """Saturday -> following Monday (+2 days); Sunday -> following Monday
    (+1 day); Monday–Friday unchanged."""
    if d.weekday() == 5:
        return d + timedelta(days=2)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def fmt_date(d):
    """date -> '19 Feb 2026'."""
    return d.strftime("%d %b %Y")


def make_row(document, statute, scheduled_dates):
    """Build a row from scheduled date(s); adjusted dates are derived with
    the weekend rule."""
    scheduled = list(scheduled_dates)
    return {"document": document, "statute": statute,
            "scheduled": scheduled,
            "adjusted": [adjust_weekend(d) for d in scheduled]}


def build_schedule(commencement, termination):
    """The full statutory schedule for a semester, as raw-date rows."""
    rows = []
    for document, statute, rule in RULES:
        if rule[0] == "range":
            dates = [_resolve(rule[1], commencement, termination),
                     _resolve(rule[2], commencement, termination)]
        else:
            dates = [_resolve(rule, commencement, termination)]
        rows.append(make_row(document, statute, dates))
    return rows


def display_row(row):
    """Text form of a row for the page and exports. The Adjusted columns
    contain ONLY the dates that were actually shifted — '—' when the
    weekend rule moved nothing."""
    sched, adj = row["scheduled"], row["adjusted"]
    shifted = [i for i in range(len(sched)) if adj[i] != sched[i]]
    return {
        "document": row["document"], "statute": row["statute"],
        "is_range": len(sched) == 2,
        "scheduled_iso": [d.isoformat() for d in sched],
        "scheduled_text": " – ".join(fmt_date(d) for d in sched),
        "day_text": " – ".join(d.strftime("%A") for d in sched),
        "adjusted_text": (" – ".join(fmt_date(adj[i]) for i in shifted)
                          if shifted else "—"),
        "adjusted_day_text": (" – ".join(adj[i].strftime("%A")
                                         for i in shifted)
                              if shifted else "—"),
        "shifted": bool(shifted),
    }


def serialize_rows(rows):
    """Rows -> JSON-safe dicts (ISO date strings)."""
    return [{"document": r["document"], "statute": r["statute"],
             "scheduled": [d.isoformat() for d in r["scheduled"]],
             "adjusted": [d.isoformat() for d in r["adjusted"]]}
            for r in rows]


def deserialize_rows(data):
    """JSON-safe dicts -> rows. A missing/mismatched adjusted list is
    re-derived with the weekend rule."""
    rows = []
    for item in data or []:
        sched = [date.fromisoformat(v) for v in item.get("scheduled", [])]
        raw_adj = item.get("adjusted") or []
        if len(raw_adj) == len(sched):
            adj = [date.fromisoformat(v) for v in raw_adj]
        else:
            adj = [adjust_weekend(d) for d in sched]
        rows.append({"document": item.get("document", ""),
                     "statute": item.get("statute", ""),
                     "scheduled": sched, "adjusted": adj})
    return rows
