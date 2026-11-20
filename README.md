# Salary Intelligence — Department Wise Salary Distribution

A secure, database-driven salary analytics platform for banking HR and finance teams, built with **Python + Streamlit + PostgreSQL**.

Every number on screen is computed from verified records in PostgreSQL. The application ships with **no sample data**: until an administrator imports a verified payroll dataset, every page shows *"No verified salary records available."*

---

## Features

| Area | What it does |
|---|---|
| **Secure login** | Email + password verified against bcrypt hashes in PostgreSQL. Per-account lockout after repeated failures, progressive per-browser throttling, idle and absolute session timeouts, timing-safe checks for unknown accounts, and audit logging of every attempt. |
| **Role-based access** | Three roles (Admin, HR Manager, Data Analyst) mapped to fine-grained permissions. The navigation only shows pages a role may open, and every page re-checks permission on the server side. |
| **Dashboard** | Total employees, departments, monthly payroll, average, highest and lowest salary, all from SQL aggregates, with a status filter, department charts and a payroll trend. |
| **Department analytics** | Headcount, total, average, minimum, maximum, annual expenditure and contribution % per department. Charts for payroll, share, average vs organisation average, headcount, min–max range and trend, plus a per-department drill-down by designation. |
| **Analyst workspace (EDA)** | Dataset overview, missing values, duplicate detection (exact, ID, probable), data types, descriptive statistics (mean, median, mode, std, variance, quartiles, IQR, skewness, kurtosis, CV), IQR outliers with adjustable *k*, histogram, box plots, department comparison and Pearson correlation. Runs on the current employee master or on any historical pay period. |
| **Employee register** | Server-side search, filters (department, status, salary range, joining date), whitelisted sorting and pagination. Names are pseudonymised for Data Analysts, and employee-level export is limited to Admin and HR. |
| **Reports** | Department salary report, employee salary summary, monthly expenditure report and EDA report, as **PDF** (ReportLab) or **Excel** (OpenPyXL). Every export is audited. |
| **Data import** | CSV or XLSX upload, then byte-level checks, header mapping, row-by-row validation, an error report, a database impact preview, explicit confirmation and a single-transaction upsert. |
| **Administration** | User management (create, change role, deactivate, unlock, reset password), a last-admin safeguard and a searchable audit log viewer with CSV export. |

---

## Role permissions

| Permission | Admin | HR Manager | Data Analyst |
|---|:-:|:-:|:-:|
| Dashboard, department analytics, visualizations | ✓ | ✓ | ✓ |
| Employee register | ✓ | ✓ | ✓ (names pseudonymised) |
| Employee-level export | ✓ | ✓ | — |
| Analyst workspace (EDA) and EDA reports | ✓ | — | ✓ |
| Department, employee and monthly reports | ✓ | ✓ | ✓ (names pseudonymised) |
| Data import | ✓ | — | — |
| User management, audit logs | ✓ | — | — |

Edit `ROLE_PERMISSIONS` in `utils/security.py` to change this.

---

## Project structure

```
salary_intelligence/
├── app.py                  # Entry point: infrastructure checks, login, role-aware navigation shell
├── config.py               # Settings from environment variables (.env)
├── database.py             # SQLAlchemy engine, transactional sessions, parameterised query helpers
├── auth.py                 # Authentication, sessions, timeouts, user management
├── models.py               # ORM tables: users, departments, employees, salary_records, audit_logs
├── manage.py               # CLI: init-db, create-user, reset-password, list-users
├── requirements.txt
├── .env.example
├── .streamlit/config.toml  # Dark theme, upload limit, XSRF protection, no error-detail leakage
├── pages/
│   ├── dashboard.py        departments.py   employees.py   eda.py
│   ├── visualizations.py   reports.py       audit_logs.py
│   ├── data_import.py      users.py         account.py
├── utils/
│   ├── data_cleaning.py    # Upload parsing, validation, import transaction
│   ├── analytics.py        # SQL aggregates and EDA functions
│   ├── charts.py           # Plotly theme and chart factory
│   ├── security.py         # RBAC, bcrypt, password policy, audit logging, sanitisation, upload checks
│   ├── report_generator.py # PDF and Excel reports
│   └── ui.py               # Header, KPI cards, empty states, footer
└── assets/
    ├── style.css           # Futuristic glassmorphism theme
    └── logo.svg
```

---

## 1. Prerequisites

* Python **3.10+** (tested on 3.13)
* PostgreSQL **13+** (tested on 16)

## 2. Install and configure PostgreSQL

**Ubuntu / Debian**
```bash
sudo apt update && sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql
```

**macOS (Homebrew)**
```bash
brew install postgresql@16 && brew services start postgresql@16
```

**Windows:** install from <https://www.postgresql.org/download/windows/> and keep the bundled `psql` / pgAdmin.

**Docker (any OS)**
```bash
docker run -d --name salary-pg -p 5432:5432 \
  -e POSTGRES_PASSWORD='choose-a-superuser-password' postgres:16
```

### Create a dedicated database and a least-privilege application role

Open `psql` as a superuser (`sudo -u postgres psql` on Linux, or `psql -U postgres`) and run the following, replacing the password:

```sql
CREATE ROLE salary_app LOGIN PASSWORD 'use-a-long-random-password';
CREATE DATABASE salary_intelligence OWNER salary_app;
REVOKE ALL ON DATABASE salary_intelligence FROM PUBLIC;
\c salary_intelligence
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO salary_app;
```

> The application role owns only this database. It is not a superuser and cannot create roles or databases.

**Optional hardening for production:**

* Require TLS: set `ssl = on` in `postgresql.conf` and use `POSTGRES_SSLMODE=require` (or `verify-full`).
* Allow only the app host in `pg_hba.conf`, using `scram-sha-256` authentication.
* Once the schema exists, you can run the app with a role that has only `SELECT, INSERT, UPDATE` on the tables. Keep the owner role for migrations and `manage.py`.

## 3. Install the application

```bash
cd salary_intelligence
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

## 4. Configure environment variables

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

Edit `.env` and set **either** `DATABASE_URL` **or** the `POSTGRES_*` variables:

```ini
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=salary_intelligence
POSTGRES_USER=salary_app
POSTGRES_PASSWORD=use-a-long-random-password
POSTGRES_SSLMODE=prefer
```

`.env` is listed in `.gitignore`. Never commit it. In production, prefer real environment variables or a secrets manager. The app reads `os.environ` first and `.env` only fills gaps.

Other settings are session timeouts, lockout threshold, bcrypt cost, upload size and row limits, currency symbol and branding. They are documented in `.env.example`.

## 5. Create the schema and the first administrator

```bash
python manage.py init-db
python manage.py create-user --email admin@yourbank.com --name "Full Name" --role Admin
```

You will be prompted for the password, which is hidden and entered twice. Passwords must be at least 12 characters with upper case, lower case, a digit and a symbol. For automation, pipe the password in with `--password-stdin`. Passwords are never accepted as command-line arguments.

Other commands:
```bash
python manage.py list-users
python manage.py reset-password --email someone@yourbank.com   # also clears a lockout
```

After the first admin exists, create the other users from **Administration → User Management** in the app.

## 6. Run

```bash
streamlit run app.py
```

Open <http://localhost:8501> and sign in.

---

## Importing payroll data

**Administration → Data Import** (Admin only).

Required columns (headers are case-insensitive, and common aliases are recognised):

| Column | Rules |
|---|---|
| `employee_id` | 1–32 characters: letters, digits, `- _ /`. Unique per employee. |
| `employee_name` | Letters, spaces, `. ' -` |
| `department` | Created automatically if new. Matching is case-insensitive. |
| `designation` | Free text, max 120 characters |
| `monthly_salary` | Positive number with up to 2 decimals. `₹`, `Rs.`, `INR` and thousands separators are stripped. |
| `joining_date` | Any common date format. Day-first is toggleable. Cannot be in the future. |
| `employment_status` | Active, On Leave, Probation, Resigned, Terminated, Retired |
| `pay_period` *(optional)* | `YYYY-MM`. If it is absent, you choose the month in the UI. |

**How the import works:**

1. The file type, size, magic bytes, encoding, macro content and zip-bomb ratio are checked. Then each row is validated.
2. Invalid rows are listed with the column and reason, and you can download them as an error report.
3. Duplicate (employee, pay period) rows and conflicting identities within the file are rejected.
4. The preview shows new and existing employees, new departments, pay periods and name changes.
5. After you confirm, everything is written in **one transaction**. If any statement fails, nothing changes.
6. Re-importing the same pay period replaces those salary figures (an idempotent upsert). Employee details follow the most recent pay period.

Load several months, either with a `pay_period` column or one file per month, to activate the trend charts and the monthly expenditure report.

---

## Security design

* **Passwords:** bcrypt with a configurable cost (default 12) and a strong password policy. Hashes are never shown or exported.
* **Login protection:** a generic error message, so it does not reveal whether an account exists. Unknown emails take the same bcrypt time as real ones. Accounts lock after `MAX_FAILED_LOGINS` failures for `LOCKOUT_MINUTES`, and the browser session is throttled progressively.
* **Sessions:** state is held on the server (Streamlit session state). A fresh session ID is issued at login. Sessions end after `SESSION_IDLE_TIMEOUT_MINUTES` of inactivity and after `SESSION_ABSOLUTE_TIMEOUT_MINUTES` in total. The account is re-checked on every interaction, so deactivation or a role change takes effect immediately. Reloading the browser ends the session.
* **Authorisation:** permissions are checked on the server in every page's `render()`, not only in the navigation.
* **SQL:** all queries use SQLAlchemy bound parameters. `IN` lists use expanding parameters, `ORDER BY` columns come from a whitelist, and `LIKE` wildcards in search text are escaped.
* **Database constraints:** primary and foreign keys, unique employee codes, case-insensitive unique department names, one record per employee and pay period, positive salaries, valid status and role values, and a first-of-month pay period.
* **Data minimisation:** Data Analysts see pseudonymised names. Employee-level exports are restricted. The application never collects banking PINs, card numbers or customer account credentials.
* **Exports:** cells are protected against CSV/Excel formula injection, and every export is written to the audit log.
* **Audit trail:** logins, failures, lockouts, logouts, session expiry, denied access, user changes, password changes, validations, imports and exports are recorded with the user, a JSON payload, the session ID and the client IP (when available).
* **Secrets:** no credentials appear in the source code. They come from environment variables only.
* **Streamlit hardening:** XSRF protection is on, error details are hidden from users, usage statistics are off, and the upload limit is enforced both by Streamlit and by the app.

**For production deployment:** put the app behind a TLS-terminating reverse proxy (Nginx, Caddy or a cloud load balancer), restrict network access to PostgreSQL, rotate the database password, and back up the database (`pg_dump`) on a schedule.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| *"Database is not configured"* | `.env` is missing or incomplete. Copy it from `.env.example`. |
| *"Database connection failed"* | Check that PostgreSQL is running, the host and port are right, the `pg_hba.conf` rules allow the app host, and the password is correct. Run `psql -h HOST -U salary_app salary_intelligence` to test. |
| *"First-time setup"* screen | Run `python manage.py create-user ... --role Admin`. |
| Upload limit in the uploader differs from the app’s limit | Keep `server.maxUploadSize` in `.streamlit/config.toml` in sync with `MAX_UPLOAD_MB`. |
| Locked out | `python manage.py reset-password --email you@yourbank.com`, or have another admin use **Clear lockout**. |
| You need tracebacks while developing | Set `showErrorDetails = "full"` in `.streamlit/config.toml`, and only locally. |
#   H C L _ s a l a r y _ w i s e _ d i s t r i b u t i o n  
 