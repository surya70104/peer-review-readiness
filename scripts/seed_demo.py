"""Demo seeder: a realistic CA firm, inspired by the spec's "shape of done".

    python scripts\\seed_demo.py            # idempotent: adds what is missing, changes nothing else
    python scripts\\seed_demo.py --reset    # first removes ONLY the demo rows listed below, then seeds

Demo rows are identified by key, not by "everything in the table":
  * the profile whose firm is DEMO_FIRM (its history goes with it)
  * assurance engagements whose code is in ENGAGEMENTS
  * SQC policies whose (area, version) is in POLICIES
Anything else in the database is never touched. Note that ENG-2026-001 is one of
the demo engagements, so --reset removes and re-adds that row.

The profile is built through the real service layer with back-dated effective
dates, so the database trigger writes the genuine applicability history.
"""
import argparse
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg2  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402

from app.services import profile_service  # noqa: E402

DEMO_FIRM = "Whhoohh Path LLP"

# (code, client, service, partner, period_from, period_to, fee, report_date, udin_serial)
RAMESH, ANITHA, SURESH, PRIYA = "CA Ramesh Kumar", "CA Anitha Reddy", "CA Suresh Varma", "CA Priya Nair"
ENGAGEMENTS = [
    ("ENG-2026-001", "Deccan Infrastructure Ltd",      "Statutory Audit",  RAMESH, "2025-04-01", "2026-03-31", 450000,  None,         None),
    ("ENG-2026-002", "Godavari Pharma Ltd",            "Statutory Audit",  ANITHA, "2025-04-01", "2026-03-31", 780000,  "2026-05-28", 2),
    ("ENG-2026-003", "Charminar Textiles Pvt Ltd",     "Tax Audit",        SURESH, "2025-04-01", "2026-03-31", 165000,  "2026-09-24", 3),
    ("ENG-2026-004", "Nizam Co-operative Bank",        "Statutory Audit",  PRIYA,  "2025-04-01", "2026-03-31", 1250000, "2026-06-12", 4),
    ("ENG-2026-005", "Krishna Agro Industries Ltd",    "Statutory Audit",  RAMESH, "2025-04-01", "2026-03-31", 520000,  "2026-07-30", 5),
    ("ENG-2026-006", "Musi Logistics Pvt Ltd",         "Limited Review",   ANITHA, "2026-04-01", "2026-06-30", 95000,   "2026-08-14", 6),
    ("ENG-2026-007", "Golconda Hotels Ltd",            "Statutory Audit",  SURESH, "2025-04-01", "2026-03-31", 610000,  "2026-06-27", 7),
    ("ENG-2026-008", "Hussain Sagar Realty LLP",       "Tax Audit",        PRIYA,  "2025-04-01", "2026-03-31", 140000,  "2026-09-30", 8),
    ("ENG-2026-009", "Secunderabad Cements Ltd",       "Internal Audit",   RAMESH, "2026-04-01", "2026-09-30", None,    None,         None),
    ("ENG-2026-010", "Warangal Rural Finance Ltd",     "Certification",    ANITHA, "2025-04-01", "2026-03-31", 85000,   "2026-04-18", 10),
]

# (area, version, effective_from, acknowledged_by, acknowledged_at, evidence)
POLICIES = [
    ("Independence", "v2.0", "2025-04-01", PRIYA, "2025-04-10T11:00:00+05:30",
     "Annual independence declarations collected from all partners and staff, April 2026."),
    ("Maintenance of professional skills and standards", "v2.0", "2025-04-01", PRIYA, "2025-04-10T11:05:00+05:30",
     "CPE hours tracked per person; quarterly technical updates minuted."),
    ("Outside consultation", "v1.1", "2025-04-01", RAMESH, "2025-04-12T16:30:00+05:30",
     "Consultation log: 6 queries referred to the technical panel in FY 2025-26."),
    ("Staff recruitment, supervision and development", "v1.0", "2025-04-01", ANITHA, "2025-04-14T10:15:00+05:30", None),
    ("Office administration", "v1.0", "2025-04-01", None, None, None),
    ("Client acceptance and continuance", "v1.2", "2026-01-01", None, None,
     "Acceptance checklist completed for 4 new clients in Q1 2026."),
]


def udin(partner_idx: int, serial: int) -> str:
    # 18 characters, and plainly a demo value.
    return f"26{210000 + partner_idx:06d}DEMO{serial:04d}XX"


def partner_idx(name: str) -> int:
    return [RAMESH, ANITHA, SURESH, PRIYA].index(name) + 1


def reset(cur) -> None:
    cur.execute("DELETE FROM pr_profile WHERE firm = %s", (DEMO_FIRM,))
    n_profile = cur.rowcount
    cur.execute("DELETE FROM assurance_register WHERE engagement = ANY(%s)", ([e[0] for e in ENGAGEMENTS],))
    n_eng = cur.rowcount
    n_sqc = 0
    for area, version, *_ in POLICIES:
        cur.execute("DELETE FROM sqc_policy WHERE area = %s AND policy_version = %s", (area, version))
        n_sqc += cur.rowcount
    print(f"[reset] removed {n_profile} profile, {n_eng} engagements, {n_sqc} SQC policies")


def seed_profile(cur) -> None:
    existing = profile_service.get_profile(cur)
    if existing:
        if existing["firm"] != DEMO_FIRM:
            sys.exit(f"[seed] a different firm profile ('{existing['firm']}') already exists; "
                     "this module holds one firm. Not touching it.")
        print("[seed] profile exists, left as is")
        return
    save = profile_service.save_profile
    # 2 partners: Phase I-IV not yet triggered.
    save(cur, {
        "firm": DEMO_FIRM, "level": 2, "partner_count": 2,
        "certificate_no": "PR/HYD/2023/0417",
        "effective_from": date(2023, 11, 15), "expires_on": date(2027, 11, 14),
        "last_report_type": "clean", "last_report_date": date(2023, 10, 20),
    }, effective_on=date(2025, 4, 1))
    # Third partner: Phase IV applies from that date.
    save(cur, {"partner_count": 3}, effective_on=date(2025, 10, 1))
    # Fourth partner, March 2026: "four partners since last March".
    save(cur, {"partner_count": 4}, effective_on=date(2026, 3, 1))
    print("[seed] profile created: 2 -> 3 -> 4 partners, Phase IV, certificate expires 14 Nov 2027")


def seed_assurance(cur) -> None:
    added = 0
    for code, client, service, partner, pf, pt, fee, rdate, serial in ENGAGEMENTS:
        cur.execute(
            """INSERT INTO assurance_register
               (engagement, client, service, period_from, period_to, partner, fee, report_date, udin)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (engagement) DO NOTHING""",
            (code, client, service, pf, pt, partner, fee, rdate,
             udin(partner_idx(partner), serial) if serial and rdate else None),
        )
        added += cur.rowcount
    print(f"[seed] assurance: {added} added, {len(ENGAGEMENTS) - added} already present")


def seed_sqc(cur) -> None:
    added = 0
    for area, version, eff, by, at, evidence in POLICIES:
        cur.execute(
            """INSERT INTO sqc_policy (area, policy_version, effective_from, acknowledged_by, acknowledged_at, evidence_of_operation)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (area, policy_version) DO NOTHING""",
            (area, version, eff, by, at, evidence),
        )
        added += cur.rowcount
    print(f"[seed] SQC 1: {added} added, {len(POLICIES) - added} already present")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true", help="remove the demo rows first (only those), then seed")
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set (check .env)")
    conn = psycopg2.connect(url)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT to_regclass('pr_profile') IS NOT NULL AS ok")
            if not cur.fetchone()["ok"]:
                sys.exit("[seed] tables are missing; run scripts\\migrate.py first")
            if args.reset:
                reset(cur)
            seed_profile(cur)
            seed_assurance(cur)
            seed_sqc(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print("[seed] done")


if __name__ == "__main__":
    main()
