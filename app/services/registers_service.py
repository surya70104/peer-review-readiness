"""Assurance engagement register and SQC 1 policy register (Pass 1 standing registers).

The assurance register is kept so the full client / service / fee list can be
produced at any moment (acceptance test 6): the summary below is computed from
the live rows on every request, never assembled by hand.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from psycopg2 import errors as pgerr
from psycopg2.extras import RealDictCursor


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def dict_cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


# --------------------------------------------------------------------------
# Assurance register
# --------------------------------------------------------------------------
ENTITY_CATEGORIES = ("listed", "level1_pie", "large_unlisted_public", "raised_50cr", "psb_branch", "other")
ASSURANCE_COLS = ["engagement", "client", "service", "period_from", "period_to",
                  "partner", "fee", "report_date", "udin", "entity_category"]
ASSURANCE_NOT_NULL = {"engagement", "client", "service", "partner"}


def _check_assurance(row: dict) -> None:
    if row.get("entity_category") is not None and row["entity_category"] not in ENTITY_CATEGORIES:
        raise ValueError("entity_category must be one of: " + ", ".join(ENTITY_CATEGORIES))
    if row["fee"] is not None and row["fee"] < 0:
        raise ValueError("fee must be >= 0")
    if row["period_from"] and row["period_to"] and row["period_to"] < row["period_from"]:
        raise ValueError("period_to must be on or after period_from")


def create_assurance(cur, data: dict) -> dict:
    row = {c: data.get(c) for c in ASSURANCE_COLS}
    _check_assurance(row)
    try:
        cur.execute(
            f"INSERT INTO assurance_register ({', '.join(ASSURANCE_COLS)}) "
            f"VALUES ({', '.join(['%s'] * len(ASSURANCE_COLS))}) RETURNING *",
            [row[c] for c in ASSURANCE_COLS],
        )
    except pgerr.UniqueViolation:
        raise Conflict(f"engagement '{row['engagement']}' already exists in the register")
    return cur.fetchone()


def update_assurance(cur, row_id: int, changes: dict) -> dict:
    for k, v in changes.items():
        if v is None and k in ASSURANCE_NOT_NULL:
            raise ValueError(f"{k} cannot be null")
    if not changes:
        raise ValueError("no fields to update")
    cur.execute("SELECT * FROM assurance_register WHERE id = %s FOR UPDATE", (row_id,))
    existing = cur.fetchone()
    if existing is None:
        raise NotFound(f"assurance engagement {row_id} not found")
    merged = {c: existing[c] for c in ASSURANCE_COLS}
    merged.update(changes)
    _check_assurance(merged)
    cols = list(changes)
    try:
        cur.execute(
            f"UPDATE assurance_register SET {', '.join(c + ' = %s' for c in cols)}, updated_at = now() "
            "WHERE id = %s RETURNING *",
            [changes[c] for c in cols] + [row_id],
        )
    except pgerr.UniqueViolation:
        raise Conflict(f"engagement '{changes.get('engagement')}' already exists in the register")
    return cur.fetchone()


def list_assurance(cur, partner: str | None = None, service: str | None = None) -> dict:
    """Filtered rows, plus summaries over the WHOLE register (not just the filter)."""
    where, params = [], []
    if partner:
        where.append("lower(partner) = lower(%s)")
        params.append(partner.strip())
    if service:
        where.append("lower(service) = lower(%s)")
        params.append(service.strip())
    cur.execute(
        "SELECT * FROM assurance_register"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY client, engagement",
        params,
    )
    engagements = cur.fetchall()

    cur.execute("SELECT * FROM assurance_register ORDER BY client, engagement")
    everything = cur.fetchall()

    per_member: dict[str, dict] = {}
    clients: dict[str, dict] = {}
    for r in everything:
        fee = r["fee"] or Decimal("0")
        # Group case/space-insensitively, consistent with the partner filter, so
        # "A. Rao" and "a. rao " are one member rather than two.
        m = per_member.setdefault(r["partner"].strip().casefold(),
                                  {"partner": r["partner"].strip(), "engagements": 0, "total_fees": Decimal("0")})
        m["engagements"] += 1
        m["total_fees"] += fee
        c = clients.setdefault(r["client"], {"client": r["client"], "total_fee": Decimal("0"), "services": []})
        c["total_fee"] += fee
        c["services"].append({
            "engagement": r["engagement"], "service": r["service"], "partner": r["partner"],
            "period_from": r["period_from"], "period_to": r["period_to"],
            "fee": r["fee"], "report_date": r["report_date"], "udin": r["udin"],
            "entity_category": r["entity_category"],
        })

    return {
        "filters": {"partner": partner, "service": service},
        "filtered_count": len(engagements),
        "engagements": engagements,
        "summary": {
            "total_engagements": len(everything),
            "total_fees": sum((r["fee"] or Decimal("0") for r in everything), Decimal("0")),
            "engagements_missing_fee": sum(1 for r in everything if r["fee"] is None),
            "audits_per_member": sorted(per_member.values(), key=lambda m: (-m["engagements"], m["partner"])),
        },
        # Questionnaire-ready list (spec section 6): every client, the services
        # rendered to them, and the fees. Always current, no manual assembly.
        "client_list": list(clients.values()),
    }


# --------------------------------------------------------------------------
# SQC 1 policy register
# --------------------------------------------------------------------------
SQC_COLS = ["area", "policy_version", "effective_from",
            "acknowledged_by", "acknowledged_at", "evidence_of_operation"]
SQC_NOT_NULL = {"area", "policy_version", "effective_from"}


def _reject_future(ts: datetime | None) -> None:
    if ts is not None and ts.astimezone(timezone.utc) > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("acknowledged_at cannot be in the future")


def create_sqc(cur, data: dict) -> dict:
    row = {c: data.get(c) for c in SQC_COLS}
    _reject_future(row["acknowledged_at"])
    if row["acknowledged_by"] and not row["acknowledged_at"]:
        row["acknowledged_at"] = datetime.now(timezone.utc)
    if bool(row["acknowledged_by"]) != bool(row["acknowledged_at"]):
        raise ValueError("acknowledged_by and acknowledged_at must be set together")
    try:
        cur.execute(
            f"INSERT INTO sqc_policy ({', '.join(SQC_COLS)}) "
            f"VALUES ({', '.join(['%s'] * len(SQC_COLS))}) RETURNING *",
            [row[c] for c in SQC_COLS],
        )
    except pgerr.UniqueViolation:
        raise Conflict(f"policy '{row['area']}' version '{row['policy_version']}' already exists")
    return cur.fetchone()


def update_sqc(cur, policy_id: int, changes: dict) -> dict:
    if not changes:
        raise ValueError("no fields to update")
    for k, v in changes.items():
        if v is None and k in SQC_NOT_NULL:
            raise ValueError(f"{k} cannot be null")
    cur.execute("SELECT * FROM sqc_policy WHERE id = %s FOR UPDATE", (policy_id,))
    existing = cur.fetchone()
    if existing is None:
        raise NotFound(f"SQC policy {policy_id} not found")

    # An acknowledgement is a record of something that happened: it can be
    # replaced by a later one but not erased.
    if ("acknowledged_by" in changes and not changes["acknowledged_by"]) or \
       ("acknowledged_at" in changes and not changes["acknowledged_at"]):
        raise ValueError("an acknowledgement cannot be cleared; record a new one instead")
    _reject_future(changes.get("acknowledged_at"))
    if "acknowledged_at" in changes and "acknowledged_by" not in changes and not existing["acknowledged_by"]:
        raise ValueError("acknowledged_at requires acknowledged_by")
    if changes.get("acknowledged_by") and "acknowledged_at" not in changes:
        changes["acknowledged_at"] = datetime.now(timezone.utc)

    cols = list(changes)
    try:
        cur.execute(
            f"UPDATE sqc_policy SET {', '.join(c + ' = %s' for c in cols)} WHERE id = %s RETURNING *",
            [changes[c] for c in cols] + [policy_id],
        )
    except pgerr.UniqueViolation:
        raise Conflict("that area and policy_version already exist")
    return cur.fetchone()


def list_sqc(cur, area: str | None = None) -> dict:
    cur.execute("SELECT * FROM sqc_policy ORDER BY area, effective_from DESC, id DESC")
    everything = cur.fetchall()
    policies = [p for p in everything if not area or p["area"].lower() == area.strip().lower()]
    return {
        "filters": {"area": area},
        "policies": policies,
        "summary": {
            "total_policies": len(everything),
            "acknowledged": sum(1 for p in everything if p["acknowledged_by"]),
            "with_evidence_of_operation": sum(1 for p in everything if p["evidence_of_operation"]),
        },
    }
