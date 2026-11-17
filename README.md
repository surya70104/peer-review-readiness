# OAK Audit · Peer Review Readiness

A module for CA firms preparing for the **ICAI peer review mandate**. It answers the questions a firm faces before the Board writes to it: does a phase apply to us, from when, by what date must the review start, and how ready are our files, policies and staff records. It keeps the evidence standing, day by day, so the firm is never assembling it after the letter arrives.

Everything is rule-based and reproducible. There is no model and no randomness: the same recorded facts and the same date always give the same scores and the same ranking, and every number names the rule behind it.

## Screens

| Screen | What it does |
|---|---|
| **1 · Standing** | Which ICAI phase (I–IV) applies and on what basis, the certificate and its expiry, and the **start-by date planned backward** from expiry through seven stages. A partner-change simulator, the follow-on review date, and an append-only applicability history. |
| **2 · Scorecard** | Firm readiness across three pillars: engagement files, SQC 1 policies and staff compliance. A prioritised list of gaps with what to do, and per-engagement scores that separate scored dimensions from "not assessed" ones. Documented later additions are shown as what they are. |
| **3 · Likely sample** | The engagements a reviewer is likely to examine, ranked out of 100 with every driver named. Choose the selection year and how many to show. |
| **4 · Registers** | The assurance engagement register (fees, audits per member, the client list the questionnaire needs, CSV export), the SQC 1 policy register, and the articled and audit assistants' attendance, work diaries, stipends and training. |
| **5 · Review tracker** | The live review cycle: the two 15-day clocks, an eight-stage track, findings with the firm's responses, closing the cycle, and the **evidence pack download**. |

## Core rules

- **Automatic phase calculation.** The phase is re-evaluated whenever the firm profile changes, using the statutory triggers and the partner ladder (5, 4 and 3 partners for Phases II, III and IV). The basis and date of every change are written to an append-only history by a database trigger.
- **The clock runs backward.** The certificate's own expiry date is read, never derived. The review start-by date is planned backward through every stage, and if the plan would end after expiry the screen states that the firm cannot sign listed company audit reports.
- **Append-only later additions.** A documented later addition to a file whose SA 230 assembly window (60 days after the report date) has closed is kept in an append-only ledger with its date and reason. It cannot be edited, deleted or back-dated, it is shown as a later addition, and it never changes a contemporaneous score.
- **Two 15-day statutory clocks.** The questionnaire and client list are due 15 days from the reviewer's consent. The firm's written response is due 15 days from the findings. Both are computed from recorded dates, can be overridden by hand, and escalate to "due soon" five days before they expire, not on the day.
- **Finding resolution gates.** A finding is resolved only when the firm's response, the corrective action and the resolution are all recorded. A cycle cannot be closed while any finding is open, and closing it as modified sets the follow-on review date on the Standing screen.
- **Honest scoring.** Only dimensions the module has evidence for are scored today (register completeness, SA 700 reporting, UDIN). The rest are listed as not assessed instead of being given a made-up score.
- **One-click evidence pack.** `GET /api/reviews/evidence-pack` streams a ZIP of the whole dossier: profile and history, registers, SQC and staff records, the scorecard and likely sample, and every cycle with its findings. A manifest lists each file with its row count and SHA-256. It is read-only.

## Tech stack

- Python, **FastAPI** and **Uvicorn**
- **PostgreSQL** via psycopg2, with schema changes as numbered SQL migrations
- Server-rendered **Jinja2** HTML, plain CSS and vanilla JavaScript. No build step.

## Local setup (Windows PowerShell)

You need Python 3.10 or newer and a running PostgreSQL server.

```powershell
# 1. Get the code
git clone https://github.com/surya70104/peer-review-readiness.git
cd peer-review-readiness

# 2. Create a virtual environment and install the requirements
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt

# 3. Create your settings, then edit .env and put your PostgreSQL password in DATABASE_URL
Copy-Item .env.example .env

# 4. Create the database (if needed) and apply migrations 0001 to 0007
.\.venv\Scripts\python scripts\migrate.py

# 5. Optional: load demo data for "Whhoohh Path LLP"
.\.venv\Scripts\python scripts\seed_demo.py

# 6. Start the server on http://127.0.0.1:8015
.\scripts\run.ps1
```

`scripts\run.ps1` does steps 2, 3 and 4 for you if they are not done yet, then starts the server, so after editing `.env` you can run it on its own.

Open <http://127.0.0.1:8015/> and use the five screens in the top bar. The health check is at `/health`.

### Notes

- **Migrations** are applied in order and tracked with a checksum. If an applied migration file is edited, the runner reports it instead of ignoring it. Add a new migration to change the schema.
- **The demo seeder** is idempotent and never overwrites your edits. `python scripts\seed_demo.py --reset` first removes only the demo rows it created. It refuses to run if a different firm's profile already exists, and it skips the demo review cycle if another cycle is open.
- **Settings.** The app reads only `DATABASE_URL`. `.env` is git-ignored, so your password is never committed.

## API

Interactive docs are at <http://127.0.0.1:8015/docs> when the server is running. The main groups are `/api/profile`, `/api/registers` (assurance, SQC 1, staff), `/api/readiness` (recompute, scorecard, likely sample, later additions) and `/api/reviews` (cycles, findings, evidence pack).

## Project layout

```
app/            FastAPI app: routers, services (rules and database access), db pool
migrations/     0001 to 0007, applied by scripts/migrate.py
scripts/        migrate.py, run.ps1, seed_demo.py
templates/      Jinja2 pages for the five screens
static/         style.css and the per-screen JavaScript
```

## Scope

This module is peer review only. It serves the practice unit being reviewed, not the reviewer. Quality Review Board and NFRA inspection workflows, AQMM scoring and model-scored files are out of scope.
