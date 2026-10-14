"""
Seed the DAS Student Data Management System with clearly-marked DEMO data.
Run:  python3 seed.py
All 6 students are FICTIONAL. Delete or replace with a real import before use.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import DB, init_db, parse_coe  # noqa: E402

SAMPLES = [
    # cnic, name, father, reg_no, gender, degree, session, folder, comments,
    # synopsis, semi, final, coe, status
    ("3520212345671", "Ayesha Raza", "Muhammad Raza", "24-DAS-0101", "Female",
     "MPhil Biochemistry", "F. 23-25", "Completed", "N/A",
     "Received", "Received", "Not Received", "2026-05-10", "Continue"),
    ("3620212345678", "Bilal Ahmed", "Ahmed Ali", "23-DAS-0098", "Male",
     "MPhil Zoology", "F. 22-24", "Not Completed", "GS-01 form 1st semester missing",
     "Received", "Not Received", "Not Received", "", "Viva Pending"),
    ("3630212345679", "Sana Tariq", "Tariq Mehmood", "22-DAS-0055", "Female",
     "PhD Chemistry", "F. 21-23", "Completed", "N/A",
     "Received", "Received", "Received", "2026-03-15", "Pass Out"),
    ("3520298765432", "Usman Khalid", "Khalid Mehmood", "24-DAS-0110", "Male",
     "MPhil Physics", "F. 23-25", "Not Completed", "N/A",
     "Not Received", "Not Received", "Not Received", "", "Freeze"),
    ("3610312345673", "Hina Shahid", "Shahid Iqbal", "23-DAS-0105", "Female",
     "PhD Botany", "F. 22-24", "Completed", "N/A",
     "Received", "Received", "Not Received", "", "Continue"),
    ("3520287654321", "Danish Ali", "Ali Raza", "24-DAS-0112", "Male",
     "MPhil Biochemistry", "F. 23-25", "Completed", "N/A",
     "Received", "Received", "Received", "2026-06-20", "Viva Done"),
]


def minimal_pdf(title):
    """Build a tiny but valid one-page PDF in memory."""
    safe = title.replace("(", "[").replace(")", "]")
    stream = f"BT /F1 18 Tf 72 720 Td ({safe}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode("latin-1") + o + b"\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objs) + 1}\n".encode("latin-1")
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n".encode("latin-1")
    pdf += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF").encode("latin-1")
    return pdf


def main():
    init_db()
    db = DB()
    db.execute("DELETE FROM documents")
    db.execute("DELETE FROM students")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    first_id = None
    for (cnic, name, father, reg_no, gender, degree, session, folder, comments,
         syn, semi, final, coe, status) in SAMPLES:
        db.execute(
            """INSERT INTO students (cnic, name, father_name, reg_no, gender,
               degree, session, folder_file, comments_missing, synopsis,
               semi_final_thesis, final_thesis, thesis_forward_coe, status,
               password_hash, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cnic, name, father, reg_no, gender, degree, session, folder,
             comments, syn, semi, final, parse_coe(coe) or None, status,
             generate_password_hash(cnic), now, now))
        if first_id is None:
            row = db.fetchone("SELECT id FROM students WHERE reg_no=?", (reg_no,))
            first_id = row["id"] if isinstance(row, dict) else row[0]
    pdf = minimal_pdf("DAS Demo Document - Ayesha Raza - Complete File")
    db.execute(
        """INSERT INTO documents (student_id, doc_label, file_name, mime_type,
           file_data, uploaded_by, uploaded_at)
           VALUES (?, 'Complete File (Merged PDF)', 'demo_complete_file.pdf',
           'application/pdf', ?, 'student', ?)""",
        (first_id, pdf, now))
    db.commit()
    n = db.fetchone("SELECT COUNT(*) AS n FROM students")
    print(f"Seeded {(n['n'] if isinstance(n, dict) else n[0])} demo students + 1 demo PDF.")
    print("Admin: /admin  (default password: admin123)")
    print("Student demo login: reg_no 24-DAS-0101, password = CNIC digits 3520212345671")
    db.close()


if __name__ == "__main__":
    main()
