"""Profile persistence: merge input, run the engines, write one UPDATE/INSERT.

All derived columns (phase_applicable, phase_basis, start_by, follow_on_due)
are written in the same statement as the inputs, so the Stage 1 trigger sees
a single consistent change and logs it in pr_applicability_history.
"""
from datetime import date

from psycopg2.extras import RealDictCursor

from app.services import applicability, clock

INPUT_COLUMNS = [
    "firm", "level", "partner_count",
    "audits_listed_entities", "audits_large_unlisted_public",
    "audits_entities_raised_50cr", "audits_psb_branches", "renders_attestation",
    "certificate_no", "effective_from", "expires_on",
    "last_report_type", "last_report_date",
]
NULLABLE = {"certificate_no", "effective_from", "expires_on", "last_report_type", "last_report_date"}
DEFAULTS = {
    "level": 2, "partner_count": 0,
    "audits_listed_entities": False, "audits_large_unlisted_public": False,
    "audits_entities_raised_50cr": False, "audits_psb_branches": False,
    "renders_attestation": True,
}
DERIVED_COLUMNS = ["phase_applicable", "phase_basis", "start_by", "follow_on_due"]


def dict_cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def get_profile(cur) -> dict | None:
    cur.execute("SELECT * FROM pr_profile ORDER BY id LIMIT 1")
    return cur.fetchone()


def get_history(cur, profile_id: int) -> list[dict]:
    cur.execute(
        """SELECT id, old_phase, new_phase, old_basis, basis, partner_count, changed_at
           FROM pr_applicability_history WHERE profile_id = %s
           ORDER BY changed_at, id""",
        (profile_id,),
    )
    return cur.fetchall()


def save_profile(cur, changes: dict, *, follow_on_months: int | None = None,
                 effective_on: date | None = None) -> dict:
    """Create or update the (single) firm profile. Raises ValueError on bad input."""
    for k, v in changes.items():
        if v is None and k not in NULLABLE:
            raise ValueError(f"{k} cannot be null")

    existing = get_profile(cur)
    if existing is None:
        if not changes.get("firm"):
            raise ValueError("firm is required to create the profile")
        merged = {**DEFAULTS, **{c: None for c in NULLABLE}, **changes}
    else:
        merged = {c: existing[c] for c in INPUT_COLUMNS}
        merged.update(changes)

    if merged["effective_from"] and merged["expires_on"] and merged["expires_on"] <= merged["effective_from"]:
        raise ValueError("expires_on must be after effective_from")
    if merged["last_report_type"] and not merged["last_report_date"]:
        raise ValueError("last_report_date is required when last_report_type is set")
    if follow_on_months is not None and merged["last_report_type"] != "modified":
        raise ValueError("follow_on_months only applies when last_report_type is 'modified'")

    result = applicability.evaluate(merged)

    # Follow-on (acceptance test 10): modified report -> 6..12 months (default 12).
    report_changed = existing is None or "last_report_type" in changes or "last_report_date" in changes
    if merged["last_report_type"] != "modified":
        follow_on = None
    elif follow_on_months is None and not report_changed and existing["follow_on_due"]:
        follow_on = existing["follow_on_due"]  # keep a previously chosen period
    else:
        follow_on = clock.follow_on_due(merged["last_report_date"], follow_on_months)

    derived = {
        "phase_applicable": result["phase"],
        "phase_basis": result["basis"],
        "start_by": clock.compute_start_by(merged["expires_on"]),  # read from certificate
        "follow_on_due": follow_on,
    }
    row = {**merged, **derived}

    if effective_on is not None:
        # Transaction-local: tells pr_change_ts() when the change took effect.
        cur.execute("SELECT set_config('app.change_at', %s, true)", (effective_on.isoformat(),))

    cols = INPUT_COLUMNS + DERIVED_COLUMNS
    if existing is None:
        cur.execute(
            f"INSERT INTO pr_profile ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING *",
            [row[c] for c in cols],
        )
    else:
        cur.execute(
            f"UPDATE pr_profile SET {', '.join(c + ' = %s' for c in cols)} WHERE id = %s RETURNING *",
            [row[c] for c in cols] + [existing["id"]],
        )
    return cur.fetchone()


def build_view(cur, profile: dict, as_of: date | None = None) -> dict:
    """Profile + live engine output + history, in the shape the API returns."""
    as_of = as_of or date.today()
    due = profile["follow_on_due"]
    return {
        "profile": profile,
        "applicability": applicability.evaluate(profile, as_of),
        "clock": clock.assess(profile["expires_on"], as_of),
        "follow_on": {
            "due": due,
            "days_remaining": (due - as_of).days if due else None,
            "last_report_type": profile["last_report_type"],
            "last_report_date": profile["last_report_date"],
        },
        "history": get_history(cur, profile["id"]),
    }
