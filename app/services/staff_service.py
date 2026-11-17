"""Staff compliance register: articled and audit assistants' attendance, work
diaries, stipends and training, one record per person per month (spec section 4,
acceptance test 9).

Compliance is computed from the stored facts on every request and never stored,
so it cannot drift from the records it describes.

Rules, all visible in the flags returned with each record:
  * Trainees (articled and audit assistants) need a submitted work diary and a
    recorded, paid stipend. Salaried audit staff are held to training only.
  * A stipend with an amount above zero and no payment date is "unpaid"; a
    trainee record with no amount at all is "not recorded".
  * Training hours below the hours required for that month is a shortfall.
Attendance is validated (never above working days) but carries no threshold,
because the spec sets none.
"""
import re
from datetime import date
from decimal import Decimal

from psycopg2 import errors as pgerr

from app.services.registers_service import Conflict, NotFound  # noqa: F401  (re-exported)

ROLES = ("articled_assistant", "audit_assistant", "audit_staff")
TRAINEE_ROLES = {"articled_assistant", "audit_assistant"}
MODES = ("bank_transfer", "cheque", "cash", "upi", "other")

COLS = ["person", "role", "registration_no", "month", "working_days", "attendance_days",
        "diary_submitted", "stipend_amount", "stipend_paid_at", "stipend_mode",
        "training_hours", "training_required", "notes"]
NOT_NULL = {"person", "role", "month", "working_days", "attendance_days",
            "diary_submitted", "training_hours", "training_required"}

FLAG_LABELS = {
    "missing_work_diary": "Work diary not submitted",
    "stipend_unpaid": "Stipend not paid",
    "stipend_not_recorded": "Stipend not recorded",
    "training_below_requirement": "Training hours below requirement",
}


def parse_month(value) -> date:
    """Accept 'YYYY-MM', 'YYYY-MM-DD' or a date; always return the first of the month."""
    if isinstance(value, date):
        return value.replace(day=1)
    s = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}", s):
        s += "-01"
    try:
        return date.fromisoformat(s).replace(day=1)
    except ValueError:
        raise ValueError("month must look like 2026-10")


def _validate(row: dict, today: date | None = None) -> None:
    today = today or date.today()
    if row["role"] not in ROLES:
        raise ValueError("role must be one of: " + ", ".join(ROLES))
    if row["stipend_mode"] is not None and row["stipend_mode"] not in MODES:
        raise ValueError("stipend_mode must be one of: " + ", ".join(MODES))
    if not 0 <= row["working_days"] <= 31:
        raise ValueError("working_days must be between 0 and 31")
    if row["attendance_days"] < 0:
        raise ValueError("attendance_days must be >= 0")
    if row["attendance_days"] > row["working_days"]:
        raise ValueError("attendance_days cannot exceed working_days")
    if row["training_hours"] < 0 or row["training_required"] < 0:
        raise ValueError("training hours must be >= 0")
    if row["stipend_amount"] is not None and row["stipend_amount"] < 0:
        raise ValueError("stipend_amount must be >= 0")
    if row["month"] > today.replace(day=1):
        raise ValueError("month cannot be in the future")
    if row["stipend_paid_at"] is not None:
        if row["stipend_paid_at"] > today:
            raise ValueError("stipend_paid_at cannot be in the future")
        if row["stipend_amount"] is None:
            raise ValueError("a stipend payment needs the stipend_amount")
        if row["stipend_mode"] is None:
            raise ValueError("a stipend payment needs the mode of payment")


def flags_for(r: dict) -> list[dict]:
    codes = []
    if r["role"] in TRAINEE_ROLES:
        if not r["diary_submitted"]:
            codes.append("missing_work_diary")
        if r["stipend_amount"] is None:
            codes.append("stipend_not_recorded")
        elif r["stipend_amount"] > 0 and r["stipend_paid_at"] is None:
            codes.append("stipend_unpaid")
    if r["training_hours"] < r["training_required"]:
        codes.append("training_below_requirement")
    return [{"code": c, "label": FLAG_LABELS[c]} for c in codes]


def _decorate(r: dict) -> dict:
    flags = flags_for(r)
    return {**r, "flags": flags, "compliant": not flags}


def create_staff(cur, data: dict) -> dict:
    row = {c: data.get(c) for c in COLS}
    row["month"] = parse_month(row["month"]) if row["month"] is not None else None
    if row["month"] is None:
        raise ValueError("month is required")
    row["diary_submitted"] = bool(row["diary_submitted"])
    row["training_hours"] = row["training_hours"] if row["training_hours"] is not None else Decimal("0")
    row["training_required"] = row["training_required"] if row["training_required"] is not None else Decimal("0")
    for k in ("person", "role", "working_days", "attendance_days"):
        if row[k] is None:
            raise ValueError(f"{k} is required")
    _validate(row)
    try:
        cur.execute(
            f"INSERT INTO staff_compliance ({', '.join(COLS)}) "
            f"VALUES ({', '.join(['%s'] * len(COLS))}) RETURNING *",
            [row[c] for c in COLS],
        )
    except pgerr.UniqueViolation:
        raise Conflict(f"{row['person']} already has a record for {row['month']:%B %Y}")
    return _decorate(cur.fetchone())


def update_staff(cur, staff_id: int, changes: dict) -> dict:
    if not changes:
        raise ValueError("no fields to update")
    for k, v in changes.items():
        if v is None and k in NOT_NULL:
            raise ValueError(f"{k} cannot be null")
    cur.execute("SELECT * FROM staff_compliance WHERE id = %s FOR UPDATE", (staff_id,))
    existing = cur.fetchone()
    if existing is None:
        raise NotFound(f"staff record {staff_id} not found")
    if "month" in changes:
        changes["month"] = parse_month(changes["month"])
    merged = {c: existing[c] for c in COLS}
    merged.update(changes)
    _validate(merged)
    cols = list(changes)
    try:
        cur.execute(
            f"UPDATE staff_compliance SET {', '.join(c + ' = %s' for c in cols)} "
            "WHERE id = %s RETURNING *",
            [changes[c] for c in cols] + [staff_id],
        )
    except pgerr.UniqueViolation:
        raise Conflict(f"{merged['person']} already has a record for {merged['month']:%B %Y}")
    return _decorate(cur.fetchone())


def list_staff(cur, role: str | None = None, period: str | None = None) -> dict:
    """Filtered records, plus compliance summaries over the WHOLE register."""
    if role is not None and role not in ROLES:
        raise ValueError("role must be one of: " + ", ".join(ROLES))
    month = None
    if period:
        if not re.fullmatch(r"\d{4}-\d{2}", period.strip()):
            raise ValueError("period must look like 2026-10")
        month = parse_month(period)

    cur.execute("SELECT * FROM staff_compliance ORDER BY month DESC, lower(person), id")
    everything = [_decorate(r) for r in cur.fetchall()]
    records = [r for r in everything
               if (role is None or r["role"] == role) and (month is None or r["month"] == month)]

    people: dict[str, dict] = {}
    flag_counts = {code: 0 for code in FLAG_LABELS}
    for r in everything:
        p = people.setdefault(r["person"].strip().casefold(), {
            "person": r["person"].strip(), "role": r["role"], "records": 0, "flagged_records": 0,
            "latest_month": r["month"]})
        p["records"] += 1
        if not r["compliant"]:
            p["flagged_records"] += 1
        for f in r["flags"]:
            flag_counts[f["code"]] += 1
    person_list = sorted(people.values(), key=lambda p: (-p["flagged_records"], p["person"].lower()))
    for p in person_list:
        p["compliant"] = p["flagged_records"] == 0

    total = len(person_list)
    compliant = sum(1 for p in person_list if p["compliant"])
    return {
        "filters": {"role": role, "period": period},
        "filtered_count": len(records),
        "records": records,
        "summary": {
            # People: one non-compliant record makes the person non-compliant until it is fixed.
            "total_staff": total,
            "compliant_count": compliant,
            "non_compliant_count": total - compliant,
            "compliance_rate": round(compliant * 100 / total, 1) if total else None,
            "total_records": len(everything),
            "flagged_records": sum(1 for r in everything if not r["compliant"]),
            "flag_counts": flag_counts,
            "people": person_list,
        },
    }
