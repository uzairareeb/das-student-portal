"""'Dhyaan Dein' pending-actions computation for the admin dashboard.

Kept out of app.py (which is near the GitHub push size limit); the date
helpers are passed in to avoid a circular import.
"""
from datetime import datetime, timedelta


def compute_pending(db, now, parse_coe, parse_dt, fmt_coe, fmt_stamp):

    inactive = ("Pass Out", "Left", "Admission Cancelled", "Freeze")
    rows = [dict(r) for r in db.fetchall(
        "SELECT id, name, reg_no, status, final_thesis,"
        " thesis_forward_coe, semi_final_thesis, synopsis_at FROM students")]
    pass_vivas = {}
    for v in db.fetchall(
            "SELECT student_id, MAX(viva_date) AS vd FROM viva_records"
            " WHERE result='Pass' GROUP BY student_id"):
        v = dict(v)
        pass_vivas[v["student_id"]] = v.get("vd")
    now = datetime.now()
    cutoff = now - timedelta(days=182)
    coe_missing, viva_pending, syn_old, sent_coe = [], [], [], []

    def base(s):
        return {"id": s["id"], "name": s.get("name") or "",
                "reg_no": s.get("reg_no") or ""}

    for s in rows:
        st = s.get("status") or "Continue"
        if st == "Sent to COE":
            d = base(s)
            iso = parse_coe(s.get("thesis_forward_coe"))
            if iso:
                sent = datetime.strptime(iso, "%Y-%m-%d").date()
                d["detail"] = (f"Sent on {fmt_coe(iso)} · "
                               f"{(now.date() - sent).days} days waiting")
                d["sort"] = iso
            else:
                d["detail"] = "Pass Out not marked yet"
                d["sort"] = ""
            sent_coe.append(d)
        if st in inactive:
            continue
        if (s.get("final_thesis") == "Received"
                and not parse_coe(s.get("thesis_forward_coe"))):
            d = base(s)
            d["detail"] = "Final Thesis received · COE date not set"
            coe_missing.append(d)
        if (s["id"] in pass_vivas
                and st in ("Continue", "Viva Pending", "Defense Pending")):
            d = base(s)
            vd = pass_vivas[s["id"]]
            d["detail"] = (f"Viva passed {fmt_coe(vd)} · "
                           f"status is still '{st}'")
            viva_pending.append(d)
        syn = parse_dt(s.get("synopsis_at"))
        if syn and s.get("semi_final_thesis") != "Received":
            dt = datetime.strptime(syn, "%Y-%m-%d %H:%M")
            if dt <= cutoff:
                months = ((now.year - dt.year) * 12 + (now.month - dt.month)
                          - (1 if now.day < dt.day else 0))
                d = base(s)
                d["detail"] = (f"Synopsis received {fmt_stamp(syn)} · "
                               f"{months} months ago · Semi-Final pending")
                d["sort"] = syn
                syn_old.append(d)
    coe_missing.sort(key=lambda d: d["name"].lower())
    viva_pending.sort(key=lambda d: d["name"].lower())
    syn_old.sort(key=lambda d: d["sort"])
    sent_coe.sort(key=lambda d: d["sort"])
    groups = []
    for key, title, items in (
            ("coe", "Final Thesis received — COE date missing", coe_missing),
            ("viva", "Viva passed — status not updated", viva_pending),
            ("syn", "Synopsis old — Semi-Final Thesis pending", syn_old),
            ("sent", "Sent to COE — Pass Out pending", sent_coe)):
        if items:
            groups.append({"key": key, "title": title, "count": len(items),
                           "rows": items[:10],
                           "more": max(len(items) - 10, 0)})
    return {"total": sum(g["count"] for g in groups), "groups": groups}
