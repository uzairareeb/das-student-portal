"""Viva Voce Result Card PDF builder.

Exact replica of the web card (templates/viva_card.html + .vcard CSS):
gradient accents and section bars, light field panels with blue labels
and dashed separators, result table, gradient PASS/FAIL stamp, remarks,
note and two-line footer — so the downloaded PDF looks exactly like the
card shown on the admin / department / student portals.
"""
import io
import os


def build_viva_pdf(card):
    """Viva result card PDF — exact replica of the web card.

    Mirrors templates/viva_card.html + the .vcard CSS one-to-one: gradient
    top accent, header, blue rule, gradient section bars, light field
    panels with blue labels and dashed row separators, result table,
    gradient PASS (blue) / FAIL (red) stamp, remarks, note and the
    two-line footer — so the downloaded PDF looks exactly like the card
    shown on the admin / department / student portals.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Paragraph

    PRIMARY = colors.HexColor("#2d3290")
    PRIMARY2 = colors.HexColor("#4a52b8")
    ACCENT2 = colors.HexColor("#5b61c9")
    INK = colors.HexColor("#1a1a1a")
    LBL_BG = colors.HexColor("#eef0fb")
    PANEL_BG = colors.HexColor("#f8f9fe")
    PANEL_LINE = colors.HexColor("#e4e7f5")
    DASH = colors.HexColor("#dfe3f2")
    LINE = colors.HexColor("#333333")
    RED = colors.HexColor("#b3261e")
    RED2 = colors.HexColor("#d43a2f")
    MUTED = colors.HexColor("#3c4257")
    WHITE = colors.white

    def lerp(c1, c2, t):
        return colors.Color(c1.red + (c2.red - c1.red) * t,
                            c1.green + (c2.green - c1.green) * t,
                            c1.blue + (c2.blue - c1.blue) * t)

    def grad_color(stops, t):
        for i in range(len(stops) - 1):
            p0, col0 = stops[i]
            p1, col1 = stops[i + 1]
            if p0 <= t <= p1:
                return lerp(col0, col1, (t - p0) / (p1 - p0) if p1 > p0 else 0)
        return stops[-1][1]

    W = 780
    MX = 16                            # border inset
    IX0, IX1 = MX + 30, W - MX - 30    # card padding (30) -> content edges
    IW = IX1 - IX0
    CX = W / 2

    # ---------- measure remarks (wrapped like the web paragraph) ----------
    rp = None
    remarks_h = 0
    if card.get("remarks"):
        rstyle = ParagraphStyle("prem", fontName="Helvetica", fontSize=11.5,
                                leading=16, textColor=INK)
        safe = (str(card["remarks"]).replace("&", "&amp;")
                .replace("<", "&lt;").replace(">", "&gt;"))
        rp = Paragraph(safe, rstyle)
        _, remarks_h = rp.wrap(IW, 600)

    # ---------- layout (top-down offsets, pt) ----------
    t = MX
    accent_t = t;            t += 6
    t += 14
    head_t = t;              t += 52          # logo block
    t += 14
    rule_t = t;              t += 3
    t += 16
    title_base = t + 17;     t += 26
    deg_base = t + 16;       t += 24
    t += 12
    sec1_t = t;              t += 30 + 10
    panel1_t = t;            t += 16 + 3 * 31 + 14
    t += 20
    sec2_t = t;              t += 30 + 10
    panel2_t = t;            t += 16 + 2 * 31 + 14
    t += 20
    sec3_t = t;              t += 30 + 6
    table_t = t;             t += 68
    t += 20
    stamp_t = t;             t += 46
    rem_sec_t = rem_txt_t = None
    if rp:
        t += 20
        rem_sec_t = t;       t += 30 + 10
        rem_txt_t = t;       t += remarks_h
    t += 18
    note_t = t;              t += 28
    t += 18
    foot_t = t;              t += 46
    H = t + MX

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))

    def Y(top):
        return H - top

    def hgrad(x, y, w, h, stops, radius=0):
        """Horizontal linear-gradient fill (optionally rounded-clipped)."""
        c.saveState()
        if radius:
            p = c.beginPath()
            p.roundRect(x, y, w, h, radius)
            c.clipPath(p, stroke=0, fill=0)
        n = 72
        for i in range(n):
            tt = (i + 0.5) / n
            c.setFillColor(grad_color(stops, tt))
            c.rect(x + w * i / n - 0.6, y, w / n + 1.2, h, stroke=0, fill=1)
        c.restoreState()


    def draw_spaced(x, y, text, font, size, space, centered=False):
        """Text with letter-spacing (web: letter-spacing), drawn char by
        char — a TextObject's Tc would leak into later drawString calls."""
        c.setFont(font, size)
        total = (c.stringWidth(text, font, size)
                 + space * max(0, len(text) - 1))
        if centered:
            x = x - total / 2
        for ch in text:
            c.drawString(x, y, ch)
            x += c.stringWidth(ch, font, size) + space

    # ---------- card border + top accent ----------
    c.setStrokeColor(INK)
    c.setLineWidth(1.5)
    c.roundRect(MX, MX, W - 2 * MX, H - 2 * MX, 4, stroke=1, fill=0)
    hgrad(MX, Y(accent_t + 6), W - 2 * MX, 6,
          [(0, PRIMARY), (0.5, ACCENT2), (1, PRIMARY)])

    # ---------- header ----------
    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "static", "img", "cuvas-logo.png")
    if os.path.exists(logo_path):
        c.drawImage(logo_path, IX0, Y(head_t + 52), 93.6, 52,
                    preserveAspectRatio=True)
    tx = IX0 + 93.6 + 16
    uni = ("Cholistan University of Veterinary & Animal Sciences,"
           " Bahawalpur")
    fs = 13.5
    while c.stringWidth(uni, "Helvetica-Bold", fs) > IX1 - tx and fs > 9:
        fs -= 0.5
    c.setFont("Helvetica-Bold", fs)
    c.setFillColor(INK)
    c.drawString(tx, Y(head_t + 24), uni)
    c.setFillColor(PRIMARY)
    draw_spaced(tx, Y(head_t + 42), "DIRECTORATE OF ADVANCED STUDIES",
                "Helvetica-Bold", 10, 0.5)

    # ---------- rule + title ----------
    c.setFillColor(PRIMARY)
    c.rect(IX0, Y(rule_t + 3), IW, 3, stroke=0, fill=1)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 17)
    c.drawCentredString(CX, Y(title_base), "Viva Voce Result Card")
    deg = card["degree"] or ""
    if card["session"]:
        deg += f" \u00b7 Session {card['session']}"
    c.setFillColor(PRIMARY)
    c.setFont("Helvetica-Bold", 11.5)
    c.drawCentredString(CX, Y(deg_base), deg)

    # ---------- section bars + field panels ----------
    def section_bar(tt, text):
        hgrad(IX0, Y(tt + 30), IW, 30,
              [(0, PRIMARY), (1, PRIMARY2)], radius=6)
        c.setFillColor(WHITE)
        c.roundRect(IX0 + 14, Y(tt + 23), 4, 16, 2, stroke=0, fill=1)
        draw_spaced(IX0 + 28, Y(tt + 20), text.upper(),
                    "Helvetica-Bold", 11.5, 1)

    def field(x, row_t, label, value):
        base = Y(row_t + 20)
        c.setFont("Helvetica-Bold", 11)
        c.setFillColor(PRIMARY)
        c.drawString(x, base, label)
        lw = c.stringWidth(label, "Helvetica-Bold", 11)
        val = str(value) if value else "\u2014"
        c.setFont("Helvetica", 11)
        max_w = (IX1 - 18) - (x + lw + 6)
        while c.stringWidth(val, "Helvetica", 11) > max_w and len(val) > 8:
            val = val[:-4] + "..."
        c.setFillColor(INK)
        c.drawString(x + lw + 6, base, val)

    def fields_panel(tt, rows):
        ph = 16 + len(rows) * 31 + 14
        c.setFillColor(PANEL_BG)
        c.setStrokeColor(PANEL_LINE)
        c.setLineWidth(1)
        c.roundRect(IX0, Y(tt + ph), IW, ph, 8, stroke=1, fill=1)
        col1 = IX0 + 18
        col2 = IX0 + IW / 2 + 8
        col1_end = IX0 + IW / 2 - 8
        for ri, (f1, f2) in enumerate(rows):
            row_t = tt + 16 + ri * 31
            field(col1, row_t, *f1)
            if f2:
                field(col2, row_t, *f2)
            dy = Y(row_t + 25)
            c.setStrokeColor(DASH)
            c.setLineWidth(0.7)
            c.setDash(3, 3)
            c.line(col1, dy, col1_end, dy)
            if f2:
                c.line(col2, dy, IX1 - 18, dy)
            c.setDash()

    section_bar(sec1_t, "Student Information")
    fields_panel(panel1_t, [
        (("Name:", card["name"]), ("Father's Name:", card["father_name"])),
        (("Registration No:", card["reg_no"]),
         ("Department:", card["department"])),
        (("Degree Program:", card["degree"]), ("Session:", card["session"])),
    ])

    section_bar(sec2_t, "Viva Details")
    fields_panel(panel2_t, [
        (("Examiner:", card["examiner_name"]),
         ("Affiliation:", card["examiner_affiliation"])),
        (("Viva Date:", card["viva_date"]),
         ("Conducted By:",
          (card["department"] or "\u2014") + " Department")),
    ])

    # ---------- result table ----------
    section_bar(sec3_t, "Result")
    rows = [
        ("Total Marks", str(card["total_marks"]),
         "Obtained Marks", str(card["obtained_marks"])),
        ("Percentage", f"{card['percentage']}%",
         "Result", card["result"]),
    ]
    rh = 34
    cw = IW / 4
    for ri, (l1, v1, l2, v2) in enumerate(rows):
        yy = table_t + ri * rh
        for ci, (lab, val) in enumerate([(l1, v1), (l2, v2)]):
            x0 = IX0 + ci * 2 * cw
            c.setFillColor(LBL_BG)
            c.rect(x0, Y(yy + rh), cw, rh, stroke=0, fill=1)
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", 11)
            c.drawString(x0 + 12, Y(yy + 22), lab)
            if lab == "Result":
                c.setFillColor(PRIMARY if val == "Pass" else RED)
                c.setFont("Helvetica-Bold", 11)
            else:
                c.setFont("Helvetica-Bold", 11)
                c.setFillColor(INK)
            c.drawString(x0 + cw + 12, Y(yy + 22), val)
    c.setStrokeColor(LINE)
    for ri in range(3):
        c.setLineWidth(1.5 if ri in (0, 2) else 1)
        yy = Y(table_t + ri * rh)
        c.line(IX0, yy, IX1, yy)
    for ci in range(5):
        c.setLineWidth(1.5 if ci in (0, 4) else 1)
        x0 = IX0 + ci * cw
        c.line(x0, Y(table_t), x0, Y(table_t + 2 * rh))

    # ---------- result stamp ----------
    is_pass = card["result"] == "Pass"
    stamp_w = (c.stringWidth((card["result"] or "").upper(),
                             "Helvetica-Bold", 20)
               + 2 * len(card["result"] or "") + 88)
    stamp_w = max(stamp_w, 190)
    if is_pass:
        hgrad(CX - stamp_w / 2, Y(stamp_t + 46), stamp_w, 46,
              [(0, PRIMARY), (1, PRIMARY2)], radius=10)
    else:
        hgrad(CX - stamp_w / 2, Y(stamp_t + 46), stamp_w, 46,
              [(0, RED), (1, RED2)], radius=10)
    c.setFillColor(WHITE)
    draw_spaced(CX, Y(stamp_t + 30), (card["result"] or "").upper(),
                "Helvetica-Bold", 20, 2, centered=True)

    # ---------- remarks ----------
    if rp:
        section_bar(rem_sec_t, "Remarks")
        rp.drawOn(c, IX0, Y(rem_txt_t + remarks_h))

    # ---------- note ----------
    note = ("NOTE: This is a system generated result card. Errors and "
            "omissions are excepted. For any query, please contact the "
            "Directorate of Advanced Studies.")
    nstyle = ParagraphStyle("pnote", fontName="Helvetica-Oblique",
                            fontSize=9.5, leading=13.5, textColor=MUTED,
                            alignment=TA_CENTER)
    np = Paragraph(note, nstyle)
    _, nh = np.wrap(IW - 16, 100)
    np.drawOn(c, IX0 + 8, Y(note_t + nh))

    # ---------- footer ----------
    c.setFillColor(PRIMARY)
    c.rect(MX, MX, W - 2 * MX, 46, stroke=0, fill=1)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawCentredString(
        CX, Y(foot_t + 20),
        f"\u00a9 {card['year']} Cholistan University of Veterinary & Animal"
        " Sciences, Bahawalpur")
    c.setFont("Helvetica", 8.5)
    c.drawCentredString(
        CX, Y(foot_t + 34),
        f"Directorate of Advanced Studies  \u00b7  Generated:"
        f" {card['generated_on']}")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf
