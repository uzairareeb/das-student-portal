"""Academic Schedule Generator — statutory deadline rules and date math.

Pure date logic (no Flask, no I/O): given a semester's commencement and
termination dates, compute every document's scheduled submission deadline
from the CUVAS statutes, then apply the weekend rule — a date falling on
Saturday or Sunday moves to the following Monday. The original scheduled
date is always preserved alongside the adjusted one.

Conventions (matching the approved reference schedule):
- "Nth week after commencement" = commencement + N weeks.
- Course-work on GS-2 ("4th week before end of 2nd semester"): in a
  standard 16-week semester, the fourth-from-last week begins 12 weeks
  after commencement.
- All arithmetic uses plain `date` objects — no time zones involved.
"""
from datetime import timedelta

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


def build_schedule(commencement, termination):
    """One row dict per document: document, statute, scheduled, day,
    adjusted, adjusted_day, shifted — all display-ready strings except
    `shifted` (True when the weekend rule moved the date)."""
    rows = []
    for document, statute, rule in RULES:
        if rule[0] == "range":
            start = _resolve(rule[1], commencement, termination)
            end = _resolve(rule[2], commencement, termination)
            adj_start, adj_end = adjust_weekend(start), adjust_weekend(end)
            rows.append({
                "document": document, "statute": statute,
                "scheduled": f"{fmt_date(start)} – {fmt_date(end)}",
                "day": f"{start.strftime('%A')} – {end.strftime('%A')}",
                "adjusted": f"{fmt_date(adj_start)} – {fmt_date(adj_end)}",
                "adjusted_day": (f"{adj_start.strftime('%A')} – "
                                 f"{adj_end.strftime('%A')}"),
                "shifted": adj_start != start or adj_end != end,
            })
        else:
            d = _resolve(rule, commencement, termination)
            adj = adjust_weekend(d)
            rows.append({
                "document": document, "statute": statute,
                "scheduled": fmt_date(d), "day": d.strftime("%A"),
                "adjusted": fmt_date(adj),
                "adjusted_day": adj.strftime("%A"),
                "shifted": adj != d,
            })
    return rows
