"""Demo seeder: a realistic CA firm, inspired by the spec's "shape of done".

    python scripts\\seed_demo.py            # idempotent: adds what is missing, changes nothing else
    python scripts\\seed_demo.py --reset    # first removes ONLY the demo rows listed below, then seeds

Demo rows are identified by key, not by "everything in the table":
  * the profile whose firm is DEMO_FIRM (its history goes with it)
  * assurance engagements whose code is in ENGAGEMENTS
  * SQC policies whose (area, version) is in POLICIES
  * staff compliance records whose (person, month) is in STAFF
  * the review cycle whose reviewer is DEMO_REVIEWER (its findings go with it)
Anything else in the database is never touched. Note that ENG-2026-001 is one of
the demo engagements, so --reset removes and re-adds that row.

The profile is built through the real service layer with back-dated effective
dates, so the database trigger writes the genuine applicability history.

The demo review cycle is dated relative to the day the seeder runs, so its
clocks look alive whenever it is seeded (the response clock is a few days from
due). It is skipped if any other cycle is open, so a real cycle is never touched.
"""
import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg2  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402

from app.services import profile_service, review_service  # noqa: E402

DEMO_FIRM = "Whhoohh Path LLP"
DEMO_REVIEWER = "CA Venkat Subramanian"
CYCLE_YEAR = 2027

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

# Demo entity categories (the sample selector reads these). Set only where still blank.
CATEGORIES = {"ENG-2026-002": "listed", "ENG-2026-005": "listed", "ENG-2026-004": "level1_pie",
              "ENG-2026-007": "raised_50cr", "ENG-2026-001": "large_unlisted_public",
              "ENG-2026-003": "other", "ENG-2026-008": "other", "ENG-2026-006": "other",
              "ENG-2026-009": "other", "ENG-2026-010": "other"}

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


# Staff compliance. Months are fixed so the demo reads the same on any day.
# (person, role, registration_no, month, working_days, attended, diary, stipend, paid_on, mode, trained, required, notes)
OCT, SEP, AUG = "2026-10-01", "2026-09-01", "2026-08-01"
STAFF = [
    ("Arjun Menon",   "articled_assistant", "ART-2024-0117", AUG, 26, 25, True,  12000, "2026-09-02", "bank_transfer", 8, 8, None),
    ("Arjun Menon",   "articled_assistant", "ART-2024-0117", SEP, 26, 24, True,  12000, None,         None,            8, 8, "Stipend not yet released."),
    ("Arjun Menon",   "articled_assistant", "ART-2024-0117", OCT, 27, 26, True,  12000, None,         None,            6, 8, None),
    ("Kavya Rao",     "articled_assistant", "ART-2025-0042", AUG, 26, 26, True,  10000, "2026-09-01", "bank_transfer", 8, 8, None),
    ("Kavya Rao",     "articled_assistant", "ART-2025-0042", SEP, 26, 25, True,  10000, "2026-10-01", "bank_transfer", 9, 8, None),
    ("Kavya Rao",     "articled_assistant", "ART-2025-0042", OCT, 27, 27, True,  10000, "2026-11-02", "bank_transfer", 8, 8, None),
    ("Imran Sheikh",  "audit_assistant",    "EMP-0231",      SEP, 26, 23, True,  15000, "2026-10-01", "bank_transfer", 5, 8, "Missed the technical update session."),
    ("Imran Sheikh",  "audit_assistant",    "EMP-0231",      OCT, 27, 27, False, 15000, "2026-11-02", "bank_transfer", 8, 8, None),
    ("Lakshmi Devi",  "audit_staff",        "EMP-0107",      SEP, 26, 26, False, None,  None,         None,            4, 4, None),
    ("Lakshmi Devi",  "audit_staff",        "EMP-0107",      OCT, 27, 26, False, None,  None,         None,            4, 4, None),
]


def udin(partner_idx: int, serial: int) -> str:
    # 18 characters, and plainly a demo value.
    return f"26{210000 + partner_idx:06d}DEMO{serial:04d}XX"


def partner_idx(name: str) -> int:
    return [RAMESH, ANITHA, SURESH, PRIYA].index(name) + 1


def _table(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (name,))
    return cur.fetchone()["ok"]


def reset(cur) -> None:
    n_cycle = 0
    if _table(cur, "pr_cycle"):
        cur.execute("SELECT id FROM pr_cycle WHERE reviewer = %s", (DEMO_REVIEWER,))
        ids = [r["id"] for r in cur.fetchall()]
        if ids:
            cur.execute("DELETE FROM pr_finding WHERE cycle_id = ANY(%s)", (ids,))
            cur.execute("DELETE FROM pr_cycle WHERE id = ANY(%s)", (ids,))
            n_cycle = len(ids)
    codes = [e[0] for e in ENGAGEMENTS]
    if _table(cur, "eng_later_addition"):
        cur.execute("SELECT count(*) AS n FROM eng_later_addition WHERE engagement = ANY(%s)", (codes,))
        if cur.fetchone()["n"]:
            sys.exit("[reset] a demo engagement has documented later additions. They are an append-only "
                     "record and cannot be removed, so --reset will not delete those engagements.")
    if _table(cur, "eng_score"):
        cur.execute("DELETE FROM eng_score WHERE engagement = ANY(%s)", (codes,))   # derived; recomputed on demand
    cur.execute("DELETE FROM pr_profile WHERE firm = %s", (DEMO_FIRM,))
    n_profile = cur.rowcount
    cur.execute("DELETE FROM assurance_register WHERE engagement = ANY(%s)", ([e[0] for e in ENGAGEMENTS],))
    n_eng = cur.rowcount
    n_sqc = 0
    for area, version, *_ in POLICIES:
        cur.execute("DELETE FROM sqc_policy WHERE area = %s AND policy_version = %s", (area, version))
        n_sqc += cur.rowcount
    n_staff = 0
    for person, role, reg, month, *_ in STAFF:
        cur.execute("DELETE FROM staff_compliance WHERE lower(btrim(person)) = lower(%s) AND month = %s",
                    (person, month))
        n_staff += cur.rowcount
    print(f"[reset] removed {n_profile} profile, {n_eng} engagements, {n_sqc} SQC policies, {n_staff} staff records, "
          f"{n_cycle} review cycle")


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
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'assurance_register' "
                "AND column_name = 'entity_category'")
    if cur.fetchone():
        for code, cat in CATEGORIES.items():
            cur.execute("UPDATE assurance_register SET entity_category = %s "
                        "WHERE engagement = %s AND entity_category IS NULL", (cat, code))


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


def seed_staff(cur) -> None:
    cur.execute("SELECT to_regclass('staff_compliance') IS NOT NULL AS ok")
    if not cur.fetchone()["ok"]:
        print("[seed] staff_compliance table missing (run scripts\\migrate.py); skipped")
        return
    added = 0
    for (person, role, reg, month, wd, att, diary, amount, paid, mode, done, req, notes) in STAFF:
        cur.execute(
            """INSERT INTO staff_compliance
               (person, role, registration_no, month, working_days, attendance_days, diary_submitted,
                stipend_amount, stipend_paid_at, stipend_mode, training_hours, training_required, notes)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (lower(btrim(person)), month) DO NOTHING""",
            (person, role, reg, month, wd, att, diary, amount, paid, mode, done, req, notes),
        )
        added += cur.rowcount
    print(f"[seed] staff compliance: {added} added, {len(STAFF) - added} already present")


def seed_cycle(cur) -> None:
    """One in-progress review: questionnaire sent on time, findings in, one resolved and one open."""
    if not _table(cur, "pr_cycle"):
        print("[seed] pr_cycle table missing (run scripts\\migrate.py); skipped")
        return
    cur.execute("SELECT selection_year, reviewer, final_report_at FROM pr_cycle")
    rows = cur.fetchall()
    if any(r["reviewer"] == DEMO_REVIEWER for r in rows):
        print("[seed] review cycle: already present")
        return
    if any(r["final_report_at"] is None for r in rows):
        print("[seed] review cycle: another cycle is open, so the demo cycle was not added")
        return
    if any(r["selection_year"] == CYCLE_YEAR for r in rows):
        print(f"[seed] review cycle: a {CYCLE_YEAR} cycle already exists, so the demo cycle was not added")
        return
    cur.execute("SELECT engagement FROM assurance_register WHERE engagement = 'ENG-2026-002'")
    eng = "ENG-2026-002" if cur.fetchone() else None

    day = lambda n: date.today() - timedelta(days=n)   # noqa: E731
    cycle = review_service.create_cycle(cur, {
        "selection_year": CYCLE_YEAR, "reviewer": DEMO_REVIEWER,
        "intimation_at": day(40),          # the firm applied early; the Board selected it
        "consent_at": day(28),             # clock 1 starts: questionnaire due 15 days later
        "questionnaire_sent": day(14),     # sent a day before it was due
        "visit_at": day(13),               # reviewer's on-site work
        "findings_at": day(11),            # clock 2 starts: response due in 4 days
    })
    f1 = review_service.create_finding(cur, cycle["id"], {
        "control_area": "Independence", "engagement": eng, "severity": "medium", "owner": ANITHA,
        "description": "Annual independence declarations were not on file for two staff members "
                       "engaged on the Godavari Pharma audit."})
    review_service.update_finding(cur, f1["id"], {
        "pu_response": "Declarations obtained from both staff members and filed. A dated declaration is now "
                       "collected when an engagement is accepted.",
        "responded_at": day(4),
        "corrective_action": "Declaration added to the engagement acceptance checklist; partner sign-off is "
                             "required before fieldwork starts.",
        "resolution": "Closed. Checked on the three most recent engagements.",
        "evidence_ref": "DOC-IND-2026-11", "resolved": True, "resolved_on": day(3)})
    review_service.create_finding(cur, cycle["id"], {
        "control_area": "Staff recruitment, supervision and development", "severity": "high", "owner": PRIYA,
        "description": "Work diaries of two articled assistants were not reviewed and countersigned by the "
                       "supervising partner in August and September."})
    print("[seed] review cycle: 2027 cycle in progress (questionnaire sent on time, 1 finding resolved, 1 open)")


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
            seed_staff(cur)
            seed_cycle(cur)
        conn.commit()
    except psycopg2.errors.ForeignKeyViolation as exc:
        conn.rollback()
        sys.exit(f"[seed] stopped: other records refer to demo rows ({exc.diag.constraint_name}). Nothing was changed.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print("[seed] done")


if __name__ == "__main__":
    main()
