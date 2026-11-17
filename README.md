# CAFirm — Chartered Accountant Enquiry & Client Service Management System

A local-first web application for registering, assigning, tracking and reporting
**chartered accountant (CA) practice enquiries** — GST registration & returns,
income tax, company & firm registration, accounting & bookkeeping, audit &
assurance. Built with Flask and PostgreSQL, designed to run completely offline
on a single machine (no CDN calls, no cloud services).

Every functionality is exposed twice:

1. a **REST API** under `/api/v1` (JSON), and
2. a **dedicated, professionally-named PostgreSQL stored procedure** (`usp_*`)
   that the API and the web layer call through a single gateway.

One endpoint ↔ one procedure — see the [API reference](#8-rest-api-reference-api-v1--usp_-procedures).

---

## 1. Features

**Public (clients & visitors — no account needed)**
- Submit an enquiry through a public form (spam-protected with a honeypot).
- Receive an **Enquiry Reference Number** (`CAF-YYYY-NNNNNN`) and a private
  **Tracking Token** shown exactly once.
- Track enquiry status anytime with **Enquiry Number + Tracking Token**
  (the token is stored only as a SHA-256 hash).
- **Forgot the number or token?** The tracking page offers a professional
  three-tab flow (see §7):
  1. **Find by Mobile Number** — enter your mobile number to list your enquiries.
  2. **Re-issue Token** — confirm enquiry number + mobile number to receive a
     fresh token (the old token is rotated out immediately).
  3. **Track by Reference** — the classic number + token lookup.

**Staff portal (login required)**
- **Admin (Administrator)**
  - Dashboard with KPI cards and Chart.js charts (status, category, source,
    monthly trend, employee-wise assigned/resolved).
  - Create enquiries on behalf of clients (source `ADMIN`).
  - Assign / reassign enquiries to staff, with full assignment history.
  - **Manage Enquiry** — one unified panel (details popup + detail-page modal
    and rail card): assignment, status change and a single shared remark,
    applied together or partially, always atomically.
  - Manage user accounts (create, activate / deactivate).
  - Manage enquiry categories (create, activate / deactivate).
  - Browse and filter the activity log (user, action, date, IP).
  - Full access to all enquiries and all status changes.
- **Staff (role "User")**
  - Personal dashboard scoped to their own records (created by them or assigned
    to them) — enforced server-side to prevent IDOR; mirrors the admin
    dashboard design (welcome band, rich KPI tiles, charts, paginated widgets).
  - Create enquiries (source `USER`), edit and progress the status of **only
    the enquiries they created**, add remarks, upload attachments.
  - An enquiry merely **assigned by Admin is read-only** for the assignee.
  - A status of **Resolved / Closed / Rejected locks the enquiry** for the
    employee — only Admin can still edit it or change its status.
  - Cannot set **Closed / Rejected / Reopened** (admin-only statuses).

**Shared**
- Enquiry list with filters: free-text search, status, category, source,
  assigned staff, date range; pagination.
- Status history timeline with remarks; separate assignment history.
- File attachments (extension + size validated, stored with opaque names).
- 8 report types with **Excel (.xlsx)** and **PDF** export:
  Total, Date-wise, Status-wise, Category-wise, Employee-wise, Source-wise,
  Pending, Resolved.
- Activity logging of every important operation (login, logout, enquiry
  lifecycle, assignment, status change, visitor tracking, attachments…).
- Local rate limiting: failed logins (5 / 15 min), public submissions
  (10 / hour), tracking attempts (30 / 10 min) — per IP, in memory.

### Enquiry workflow

```
New ──▶ Pending / Assigned / In Progress / Resolved / Closed / Rejected
Pending ──▶ In Progress / Resolved / Closed / Rejected
Assigned ──▶ In Progress / Pending / Resolved / Closed / Rejected
In Progress ──▶ Pending / Resolved / Closed / Rejected
Resolved ──▶ Closed / Reopened
Closed ──▶ Reopened
Rejected ──▶ Reopened
Reopened ──▶ In Progress / Pending / Resolved / Rejected / Closed
```

Closed, Rejected and Reopened are **admin-only**. Reopening requires a reason.

---

## 2. Technology stack

| Layer      | Technology                                             |
|------------|--------------------------------------------------------|
| Language   | **Python 3.14**                                        |
| Web        | **Flask 3.1.3** (app-factory + blueprints, MVC layout) |
| Database   | **PostgreSQL 18** (PL/pgSQL stored procedures)         |
| ORM        | SQLAlchemy 2.0.57 + Flask-SQLAlchemy 3.1.1 (Flask-Migrate / Alembic) |
| DB driver  | psycopg 3.3.9 (psycopg 3)                              |
| Procedures | 32 API-facing `usp_*` stored functions (PL/pgSQL; 42 total incl. helpers), installed via one migration |
| Auth       | Flask-Login 0.6.3, Werkzeug 3.1.9 password hashing     |
| Forms      | Flask-WTF 1.3.0 (CSRF protection on every web POST)    |
| Frontend   | Bootstrap 5.3.3, Bootstrap Icons 1.11.3, Chart.js 4.4.9 — **all stored locally** under `app/static/vendor` (no CDN at runtime) |
| Exports    | openpyxl 3.1.5 (.xlsx), reportlab 5.1.0 (.pdf)         |
| Tests      | pytest 9.2.0 (**146 tests**, separate `cafarm_test` database) |

---

## 3. Project structure

```
CAFirm/
├── run.py                     # entry point (python run.py)
├── config.py                  # env-driven configuration (dev / test / prod)
├── requirements.txt
├── .env                       # live configuration (NOT committed)
├── .env.example               # template
├── migrations/                # Alembic migrations (schema + usp_* procedures)
├── wheelhouse/                # offline package cache (built separately)
├── uploads/                   # enquiry attachments (runtime)
├── logs/                      # rotating app log (runtime)
├── app/
│   ├── __init__.py            # app factory, seed CLI, error pages, logging
│   ├── extensions.py          # db / migrate / login_manager / csrf
│   ├── database/
│   │   └── procedures.py      # SINGLE SOURCE of every usp_* SQL statement
│   ├── models/                # 9 SQLAlchemy models
│   ├── controllers/           # blueprints: auth, admin, user, enquiry, public,
│   │                          #   report + api (the /api/v1 blueprint)
│   ├── services/
│   │   └── procedure_service.py  # call_procedure gateway + typed wrappers
│   ├── utils/                 # decorators, validators, helpers (rate limiter, audit)
│   ├── views/                 # Jinja2 templates (layouts, auth, admin, user,
│   │                          #   enquiries, public, reports, errors)
│   └── static/                # css/, js/, images/, vendor/ (local libraries)
└── tests/                     # pytest suite (conftest + test modules incl. test_api)
```

---

## 4. Quick start (machine with internet)

Prerequisites: **Python 3.14** and **PostgreSQL 18** installed and running.
The examples below use PowerShell — replace `yourpassword` with your own
PostgreSQL superuser password everywhere.

```powershell
# 1 — create the virtual environment and install dependencies
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2 — configure the environment
Copy-Item .env.example .env      # then edit SECRET_KEY and the two DATABASE_URL lines

# 3 — create the two databases
$env:PGPASSWORD = "yourpassword"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -c "CREATE DATABASE cafarm_db;"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -c "CREATE DATABASE cafarm_test;"

# 4 — apply the schema, install the usp_* procedures and seed baseline data
.\.venv\Scripts\python.exe -m flask --app run.py db upgrade
.\.venv\Scripts\python.exe -m flask --app run.py seed

# 5 — start the application
.\.venv\Scripts\python.exe run.py
# open http://127.0.0.1:5000/
```

`flask seed` is idempotent and creates: roles **Admin** and **User**,
accounts **admin / user1 / user2**, and six CA-practice categories
(GST Registration & Returns, Income Tax, Company & Firm Registration,
Accounting & Bookkeeping, Audit & Assurance, General Enquiry).
Re-run with `--force` to reset the default passwords.

The `db upgrade` step also installs every `usp_*` stored procedure
(migration `7f3b2a91c5d8_cafarm_business_procedures`), so the API works
immediately after a fresh install.

### Default accounts

| Username | Password    | Role  |
|----------|-------------|-------|
| admin    | `Admin@123` | Admin |
| user1    | `User@123`  | User  |
| user2    | `User@123`  | User  |

**Change these passwords before any real use** (Admin → Manage Users; add a
password-change feature or update the accounts directly in the database).

### .env reference

| Variable                                      | Purpose                                   |
|-----------------------------------------------|-------------------------------------------|
| `FLASK_CONFIG`                                | development / testing / production        |
| `SECRET_KEY`                                  | session signing key (set your own!)       |
| `DATABASE_URL`                                | `postgresql+psycopg://user:pass@host:5432/cafarm_db` |
| `TEST_DATABASE_URL`                           | same for `cafarm_test`                    |
| `SESSION_LIFETIME_HOURS`                      | login session lifetime (default 8)        |
| `TRACKING_TOKEN_TTL_DAYS`                     | tracking token validity (default 90)      |
| `LOGIN_RATE_LIMIT` / `LOGIN_RATE_WINDOW`      | brute-force protection (5 / 900 s)        |
| `PUBLIC_SUBMIT_RATE_LIMIT` / `..._WINDOW`     | public form throttling (10 / 3600 s)      |
| `TRACK_RATE_LIMIT` / `TRACK_RATE_WINDOW`      | tracking throttling (30 / 600 s)          |
| `MAX_FILE_SIZE_MB`                            | per-attachment limit (default 5)          |
| `ITEMS_PER_PAGE`                              | list pagination size (default 10)         |

---

## 5. Offline installation plan (air-gapped machine)

The app runs fully offline at runtime (all frontend libraries are vendored,
the database is local). To install it on a machine **without internet**:

1. **On an internet-connected machine** (any machine, same OS is safest):

   ```powershell
   .\.venv\Scripts\python.exe -m pip download -r requirements.txt -d wheelhouse
   ```

   This fills `wheelhouse/` with every package needed by `requirements.txt`.

2. **Copy the whole project folder** — including `wheelhouse/`,
   `app/static/vendor/`, `migrations/` and `.env.example` — to the offline
   machine (USB drive, secure file transfer, etc.).

3. **On the offline machine**, install strictly from the local cache:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install --no-index --find-links wheelhouse -r requirements.txt
   ```

4. Continue with steps 2–5 of the quick start (`.env`, create databases,
   `db upgrade`, `seed`, `run.py`). No internet is needed at any point.

---

## 6. Running the tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

- Tests use the **`cafarm_test`** database (TestingConfig) — never `cafarm_db`.
- CSRF and rate limiting are neutralised in test mode.
- The suite creates the schema, **installs every `usp_*` procedure**, seeds
  baseline accounts, and drops everything at the end.
- **146 tests** cover auth, enquiry CRUD + scoping, status workflow and
  permission rules (creator-only status updates, terminal-state lock),
  assignment, the unified admin Manage panel, visitor tracking,
  reports/exports, activity logging, plus the REST API (`tests/test_api.py`):
  submit, track by reference, mobile lookup, token re-issue rotation,
  staff/admin endpoints, dashboards and role guards.

If the test database is left in a dirty state (e.g. a run was killed):

```powershell
$env:PGPASSWORD = "yourpassword"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost `
    -c "DROP DATABASE IF EXISTS cafarm_test;" -c "CREATE DATABASE cafarm_test;"
```

---

## 7. Tracking an enquiry (professional client flow)

The tracking page (`/public/track`) is organised in three tabs. The
**Submit Enquiry** and **Track Enquiry** links were deliberately removed from
the header — both actions live on the home page as large action cards, and the
header stays clean.

| Situation | What to do |
|-----------|------------|
| You have the enquiry number **and** token | **Track by Reference** (tab 1) — enter both, see full status + history. The enquiry number can also be pre-filled via `/public/track/enquiry?number=CAF-2026-000001`. |
| You forgot the token | **Re-issue Token** (tab 2) — enter the enquiry number + your mobile number; a fresh token is issued and the old one is invalidated instantly. |
| You forgot both the number and the token | **Find by Mobile Number** (tab 3) — enter your mobile number to list every enquiry you raised (number, masked subject, status); then re-issue the token from tab 2. |

Security notes: tokens are stored hashed, the re-issue flow only works when
the mobile number matches the enquiry record, and every lookup / re-issue is
rate-limited and activity-logged.

---

## 8. REST API reference (`/api/v1` ↔ `usp_*` procedures)

All API responses use one uniform JSON envelope:

```json
{"success": true,  "data": { ... }}
{"success": false, "code": "VALIDATION|NOT_FOUND|FORBIDDEN|CONFLICT|ERROR", "error": "message"}
```

Error codes map to HTTP status: `VALIDATION`→400, `NOT_FOUND`→404,
`FORBIDDEN`→401/403, `CONFLICT`→409, everything else→500.

`GET /api/v1/` lists the full endpoint→procedure map at runtime.

### Public (no login)

| Method | Endpoint | Procedure | Purpose |
|--------|----------|-----------|---------|
| POST | `/api/v1/public/enquiries` | `usp_submit_visitor_enquiry` | Submit a visitor enquiry (returns number + token once) |
| GET | `/api/v1/public/categories` | `usp_list_active_categories` | Active enquiry categories for the form |
| GET | `/api/v1/public/statistics` | `usp_get_public_stats` | Public counters for the home page |
| POST | `/api/v1/public/tracking/by-reference` | `usp_track_by_reference` | Track by enquiry number + token |
| POST | `/api/v1/public/tracking/by-mobile` | `usp_lookup_enquiries_by_mobile` | Find enquiries by mobile number |
| POST | `/api/v1/public/tracking/token-reissue` | `usp_reissue_tracking_token` | Re-issue a token (rotates the old one) |

### Staff (login required; scoped to own records)

| Method | Endpoint | Procedure | Purpose |
|--------|----------|-----------|---------|
| GET | `/api/v1/staff/enquiries` | `usp_list_enquiries` | Filterable, paginated enquiry list |
| POST | `/api/v1/staff/enquiries` | `usp_create_staff_enquiry` | Create an enquiry on behalf of a client |
| GET | `/api/v1/staff/enquiries/<id>` | `usp_get_enquiry` | Full detail incl. history, remarks, attachments |
| PUT | `/api/v1/staff/enquiries/<id>` | `usp_update_enquiry` | Update editable fields |
| POST | `/api/v1/staff/enquiries/<id>/assignment` | `usp_assign_enquiry` | Assign / reassign (admin) |
| POST | `/api/v1/staff/enquiries/<id>/status` | `usp_update_enquiry_status` | Status transition with workflow rules |
| POST | `/api/v1/staff/enquiries/<id>/remarks` | `usp_add_enquiry_remark` | Add an internal remark |
| POST | `/api/v1/staff/enquiries/<id>/attachments` | `usp_add_enquiry_attachment` | Register an uploaded attachment |
| DELETE | `/api/v1/staff/enquiries/attachments/<id>` | `usp_delete_enquiry_attachment` | Delete an attachment |
| GET | `/api/v1/staff/dashboard/kpis` | `usp_get_dashboard_kpis` | Dashboard KPI cards |
| GET | `/api/v1/staff/dashboard/charts` | `usp_get_dashboard_charts` | Dashboard chart datasets |

### Admin only

| Method | Endpoint | Procedure | Purpose |
|--------|----------|-----------|---------|
| GET | `/api/v1/admin/users` | `usp_list_users` | User accounts (search) |
| POST | `/api/v1/admin/users` | `usp_create_user` | Create a staff account |
| PATCH | `/api/v1/admin/users/<id>/status` | `usp_toggle_user_status` | Activate / deactivate a user |
| GET | `/api/v1/admin/categories` | `usp_list_categories` | All categories (incl. inactive) |
| POST | `/api/v1/admin/categories` | `usp_create_category` | Create a category |
| PATCH | `/api/v1/admin/categories/<id>/status` | `usp_toggle_category_status` | Activate / deactivate a category |

### Reports (admin; one route per report type)

| Method | Endpoint | Procedure |
|--------|----------|-----------|
| POST | `/api/v1/reports/total` | `usp_report_total_enquiries` |
| POST | `/api/v1/reports/date-wise` | `usp_report_date_wise` |
| POST | `/api/v1/reports/status-wise` | `usp_report_status_wise` |
| POST | `/api/v1/reports/category-wise` | `usp_report_category_wise` |
| POST | `/api/v1/reports/employee-wise` | `usp_report_employee_wise` |
| POST | `/api/v1/reports/source-wise` | `usp_report_source_wise` |
| POST | `/api/v1/reports/pending` | `usp_report_pending_enquiries` |
| POST | `/api/v1/reports/resolved` | `usp_report_resolved_enquiries` |

**CSRF note:** the `/api/v1` blueprint is CSRF-exempt (`csrf.exempt(api_bp)`)
because it authenticates through the Flask-Login session (browser clients) and
is consumed by the SPA-style dashboards; all *web* form POSTs still require the
CSRF token. The API is additionally protected by rate limiting and role guards
(401 unauthenticated, 403 non-admin on admin routes).

---

## 9. Security notes

- **Passwords** are hashed with Werkzeug (scrypt/pbkdf2); never stored plain.
- **CSRF** tokens are required on every state-changing web POST (Flask-WTF);
  the `/api/v1` blueprint is exempt as described above.
- **Access control**: staff can only view/modify enquiries they created or are
  assigned to; admin pages and admin API routes are admin-only (server-side
  checks, 401/403).
- **Visitor tokens**: only the SHA-256 hash is stored; comparison uses
  `hmac.compare_digest`; tokens expire after `TRACKING_TOKEN_TTL_DAYS`; the
  re-issue flow verifies the mobile number and rotates the token.
- **Uploads**: extension allow-list, size limit, UUID storage names, download
  only for users who may view the parent enquiry.
- **Rate limiting**: local, in-memory, per-IP sliding windows on login,
  public submissions and tracking (resets on server restart).
- **Honeypot** field on the public form silently rejects bots.
- **Activity log** records user, action, description, enquiry and IP for
  every significant operation.
- Run behind HTTPS / a production WSGI server (e.g. waitress) for any
  multi-user deployment; set `FLASK_CONFIG=production` so cookies get the
  `Secure` flag and debug mode is off.

---

## 10. Machine-specific notes (this workstation)

- **PostgreSQL 18** is installed (spec said 17; 18 is fully compatible).
- This machine's **Application Control policy blocks compiled `.pyd` extension
  modules**, so the dependency set is deliberately pure-Python:
  - `psycopg` **without** the `[binary]` extra — it talks to PostgreSQL through
    the local `libpq.dll`; `config.py` prepends
    `C:\Program Files\PostgreSQL\18\bin` to `PATH` so the DLL is found.
  - `SQLAlchemy` pinned to `>=2.0.41,<2.1` (2.1's Cython modules are blocked).
  - `pandas` was removed — the Excel export writes `.xlsx` directly with
    **openpyxl**.
- On this machine always invoke pip as `python -m pip` (a blocked `pip.exe`
  wrapper exists on PATH).
- Never kill the python process belonging to **pgAdmin 4**
  (`...\PostgreSQL\18\pgAdmin 4\python\python.exe`) when cleaning up the dev
  server; match on `run.py` in the command line instead.

## 11. Troubleshooting

| Symptom                                  | Fix                                                              |
|------------------------------------------|------------------------------------------------------------------|
| `relation "users" does not exist`        | Run `flask --app run.py db upgrade` against the right DB.        |
| API returns `ERROR` / `UndefinedFunction`| Re-run `db upgrade` (or reinstall procedures) so all `usp_*` functions exist. |
| Login always says "Too many attempts"    | Rate limiter memory — restart the dev server, or wait out the window. |
| Port 5000 already in use                 | Stop the old server: kill every python process whose command line contains `run.py`. |
| Tests fail with duplicate role errors    | Recreate `cafarm_test` (see §6), then re-run pytest.             |
| Tracking says "No matching enquiry"      | Wrong token, expired token, typo in the enquiry number — or use **Find by Mobile Number**. |

---

## 12. Recent enhancements (November 2026 changelog)

- **Unified admin "Manage Enquiry" panel** — assignment, status change and a
  single shared remark in one place: details-popup view, detail-page modal and
  right-rail card, all posting to a new atomic endpoint
  `POST /enquiries/<id>/manage` (assign-first ordering, partial applies,
  in-popup validation errors that preserve typed values).
- **Employee permission hardening**
  - Employees progress the status of **only enquiries they created**;
    an enquiry merely assigned to them is read-only (`TERMINAL_STATUSES`,
    `allowed_statuses_for`, ownership guard in `update_status`, plus matching
    guards in `usp_update_enquiry_status` / `usp_update_enquiry`).
  - Terminal states (**Resolved / Closed / Rejected**) lock the enquiry for
    employees — edit forms, status popups and API all return **403**.
- **Professional dashboards** — shared anchored-rail shell (fixed sidebar,
  topbar, sole-scroller page body); the employee dashboard mirrors the admin
  design: welcome band, five rich KPI tiles, Chart.js charts and 4-per-page
  widget cards with pill pagers; every chart card has a styled empty state
  instead of a blank canvas.
- **Employee sidebar parity** — the staff rail now includes the scoped
  **Insights → Reports** section, and active-state highlighting is
  route-accurate on both roles.
- **Suite at 146 passing tests** (was 60) — covering the new permission rules,
  the manage panel, dashboards, popup flows and the REST API.
