# DAS Student Data Management System

Simple student-record portal for a university **Advanced Studies Office**.
Students' complete data lives **online** so it can be pulled anytime for official use.

- **Admin portal** (`/admin`): students list with the 12 office columns
  (Name, Reg.No, Gender, Degree, Session, Folder/File, Comments (Missing),
  Synopsis, Semi-Final, Final Thesis, Thesis Forward (COE), Status), filters
  (Session, Degree, Status, Gender, Folder/File) + search (Name / Reg.No / CNIC),
  complete student profiles, Excel import/export, document management.
- **Student portal** (`/student`): login with Registration No. + password
  (default password = CNIC digits), view complete profile, change password,
  and upload the complete file as **one merged PDF** following the on-screen
  checklist (MPhil: 7 items, PhD: 18 items). Uploads save straight into the
  admin's record of that student for future use.

Built with the same pattern as the CUVAS result portal: **Flask + Jinja + plain
CSS, SQLite locally, Postgres on Vercel** via `DATABASE_URL`. File uploads are
never written to disk (stored as BYTEA/BLOB), so it is serverless-safe.

## How to run (on any computer with Python 3.10+)

```bash
cd das_student_portal
pip install -r requirements.txt
python3 seed.py      # loads 6 fictional demo students (optional, for testing)
python3 app.py
```

Then open:

- Home: http://localhost:5000
- Admin: http://localhost:5000/admin  (default password: `admin123`)
- Student: http://localhost:5000/student

Change the admin password by setting the `ADMIN_PASSWORD` environment variable,
e.g. on Windows: `set ADMIN_PASSWORD=<your-password>`

## Deploy on Vercel

Vercel's servers are stateless, so the portal uses **Postgres** there instead of
the local SQLite file. The free Neon Postgres one-click integration is the easiest:

1. Push this folder to a GitHub repo (see commands below).
2. On [vercel.com](https://vercel.com) → Add New → Project → Import the repo.
3. Storage tab → Connect → **Neon Postgres** (free) → Create. This sets `DATABASE_URL` for you.
4. Environment Variables → add:
   - `ADMIN_PASSWORD` = your chosen admin password
   - `PORTAL_SECRET` = any long random string
5. Deploy. Tables are created automatically on first visit.

Local runs keep using SQLite (`students.db`) — no `DATABASE_URL` needed.

## Push to GitHub

```bash
cd das_student_portal
git init
git add .
git commit -m "DAS student data management system"
# create an empty repo on github.com, then:
git remote add origin https://github.com/YOUR-USERNAME/das-student-portal.git
git branch -M main
git push -u origin main
```

## Import file format

Excel (.xlsx). Column headers are matched flexibly ("Registration No.", "Reg No",
"Reg.No" all work). A ready-made template can be downloaded from the import page.

| CNIC | Name | Father Name | Registration No. | Gender | Degree | Session | Folder/File (With GS-Forms) | Comments (Missing) | Synopsis | Semi-Final Thesis | Final Thesis | Thesis Forward (COE) | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 3520212345671 | Ayesha Raza | Muhammad Raza | 24-DAS-0101 | Female | MPhil Biochemistry | F. 23-25 | Completed | N/A | Received | Received | Not Received | 2026-05-10 | Continue |

- **Status** must be one of: Continue, Freeze, Left, Admission Cancelled,
  Viva Done, Viva Pending, Defense Pending, Pass Out.
- **Folder/File:** Completed / Not Completed. **Synopsis / Semi-Final / Final Thesis:** Received / Not Received.
- Re-importing the same Registration No. **updates** that student.
- New students get a portal password automatically: their CNIC digits.
- Rows missing Name / Reg.No / CNIC are skipped and reported.

## Entry points

- `/` — home with both portal links.
- `/admin` — admin login (direct link only; not linked from student pages).
- `/student` — student login.
