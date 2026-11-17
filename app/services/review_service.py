"""Review execution tracker: the live peer review cycle, its two fifteen-day
clocks, the findings and the firm's responses (spec sections 6 and 9, Screen 5,
acceptance tests 7 and 10).

The deadlines
-------------
The spec gives exactly two statutory clocks, both 15 days:
  * from the reviewer's consent: the completed questionnaire, the full list of
    assurance clients with services and fees, notes on independence, staff
    supervision and second-partner review policies, and disclosure of any
    proceedings relating to deficiencies in attest functions;
  * from the findings of non-compliance: the firm's written response.
Both are computed from recorded dates. A due date can be overridden by hand (the
reviewer may allow more time); the response then says the basis is "manual". A
computed due date follows its start date if that date is corrected.
Other stage lengths in the backward renewal plan (clock.py) are planning
allowances, not deadlines, and are not tracked here.

Escalation (acceptance test 7): a clock turns "due_soon" ESCALATE_BEFORE_DAYS
before its due date, not on it.

Rules that keep the record honest
---------------------------------
  * Milestone dates cannot be cleared once recorded, only corrected.
  * A cycle is closed by recording the final report date and its outcome
    together, and only when the firm has responded and every finding is resolved.
  * A finding is resolved when the firm's response, the resolution and the
    corrective action are all recorded. A response or resolution can be
    replaced, never cleared.
  * A closed cycle and its findings are not edited further.
  * Closing a cycle updates the profile's last report; a modified report sets
    the follow-on date (6 to 12 months, default 12) the same way the Standing
    screen does (acceptance test 10).
"""
import re
from datetime import date, timedelta

from psycopg2 import errors as pgerr

from app.services import profile_service
from app.services.registers_service import Conflict, NotFound  # noqa: F401  (re-exported)

CLOCK_DAYS = 15
ESCALATE_BEFORE_DAYS = 5

# (key, label, date field). Stage = the latest one reached.
STAGES = (
    ("selected", "Selected by the Board", "intimation_at"),
    ("consented", "Reviewer's consent", "consent_at"),
    ("questionnaire_sent", "Questionnaire and client list sent", "questionnaire_sent"),
    ("on_site", "Reviewer's on-site work", "visit_at"),
    ("findings", "Findings of non-compliance received", "findings_at"),
    ("response_sent", "Firm's response sent", "response_sent"),
    ("preliminary_report", "Preliminary report", "preliminary_report_at"),
    ("final_report", "Final report", "final_report_at"),
)
MILESTONES = ("intimation_at", "consent_at", "questionnaire_sent", "visit_at", "findings_at",
              "response_sent", "preliminary_report_at", "final_report_at")
NOT_FUTURE = tuple(m for m in MILESTONES if m != "visit_at")   # a visit can be scheduled ahead
CYCLE_FIELDS = ("period_from", "period_to", "reviewer", "intimation_at", "consent_at", "questionnaire_due",
                "questionnaire_sent", "visit_at", "findings_at", "response_due", "response_sent",
                "preliminary_report_at", "final_report_at", "outcome")
SEVERITIES = ("high", "medium", "low")
QUESTIONNAIRE_CONTENTS = (
    "The completed questionnaire",
    "A full list of assurance clients with services rendered and fees",
    "Notes on the firm's independence, staff supervision and second-partner review policies",
    "Disclosure of any proceedings against the firm relating to deficiencies in attest functions",
)
RANK = {"overdue": 3, "due_soon": 2, "on_track": 1}


def _d(v) -> str:
    return "—" if v is None else f"{v.day:02d} {v:%b %Y}"


# ================================================================== clocks
def clock_state(label: str, rule: str, start: date | None, due: date | None, done: date | None,
                today: date, contents: tuple = ()) -> dict:
    out = {"label": label, "rule": rule, "starts_on": start, "due": due,
           "due_basis": None if due is None else ("computed" if start and due == start + timedelta(days=CLOCK_DAYS) else "manual"),
           "escalate_from": due - timedelta(days=ESCALATE_BEFORE_DAYS) if due else None,
           "done_on": done, "days_remaining": None, "days_late": 0, "contents": list(contents)}
    if start is None and done is None:
        out["status"] = "not_started"
    elif done is not None:
        out["status"] = "completed"
        out["days_late"] = max((done - due).days, 0) if due else 0
    elif due is None:
        out["status"] = "not_started"
    else:
        remaining = (due - today).days
        out["days_remaining"] = remaining
        out["status"] = "overdue" if remaining < 0 else "due_soon" if remaining <= ESCALATE_BEFORE_DAYS else "on_track"
    return out


# ================================================================== findings
def finding_view(f: dict, cycle: dict, today: date) -> dict:
    status = "resolved" if f["closed_at"] else "responded" if f["pu_response"] else "open"
    due = f["due_at"] or cycle["response_due"]
    return {**f, "status": status, "due": due,
            "due_basis": "finding" if f["due_at"] else "cycle response clock",
            "overdue": status != "resolved" and due is not None and today > due,
            "response_late": bool(f["responded_at"] and due and f["responded_at"] > due)}


def _counts(findings: list[dict]) -> dict:
    return {"total": len(findings),
            "open": sum(1 for f in findings if f["status"] == "open"),
            "responded": sum(1 for f in findings if f["status"] == "responded"),
            "resolved": sum(1 for f in findings if f["status"] == "resolved"),
            "overdue": sum(1 for f in findings if f["overdue"])}


# ================================================================== cycles
def cycle_view(c: dict, findings: list[dict], profile: dict | None, today: date) -> dict:
    fv = [finding_view(f, c, today) for f in findings]
    clocks = {
        "questionnaire": clock_state("Questionnaire and assurance client list", "15 days from the reviewer's consent",
                                     c["consent_at"], c["questionnaire_due"], c["questionnaire_sent"], today,
                                     QUESTIONNAIRE_CONTENTS),
        "response": clock_state("Firm's written response to the findings", "15 days from the findings of non-compliance",
                                c["findings_at"], c["response_due"], c["response_sent"], today),
    }
    stages, current = [], STAGES[0]
    for key, label, field in STAGES:
        v = c[field] if field != "intimation_at" else (c["intimation_at"] or c["created_at"].date())
        reached = v is not None and (field != "visit_at" or v <= today)
        stages.append({"key": key, "label": label, "date": c[field] if field != "intimation_at" else c["intimation_at"],
                       "done": reached, "scheduled": field == "visit_at" and v is not None and v > today})
        if reached:
            current = (key, label, field)
    completed = c["final_report_at"] is not None
    open_states = [k["status"] for k in clocks.values() if k["status"] in RANK]
    deadline = "completed" if completed else (max(open_states, key=RANK.get) if open_states else "on_track")
    out = {**c, "stage": current[0], "stage_label": current[1], "stages": stages, "clocks": clocks,
           "deadline_status": deadline, "completed": completed,
           "findings": fv, "finding_counts": _counts(fv)}
    if completed and c["outcome"] == "modified":
        linked = bool(profile and profile["last_report_date"] == c["final_report_at"])
        out["follow_on"] = {"linked_to_profile": linked, "due": profile["follow_on_due"] if linked else None}
    return out


def _load_findings(cur, cycle_ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list] = {i: [] for i in cycle_ids}
    if cycle_ids:
        cur.execute("SELECT * FROM pr_finding WHERE cycle_id = ANY(%s) ORDER BY id", (cycle_ids,))
        for f in cur.fetchall():
            out[f["cycle_id"]].append(f)
    return out


def list_cycles(cur, today: date | None = None) -> dict:
    today = today or date.today()
    cur.execute("SELECT * FROM pr_cycle ORDER BY selection_year DESC, id DESC")
    cycles = cur.fetchall()
    findings = _load_findings(cur, [c["id"] for c in cycles])
    profile = profile_service.get_profile(cur)
    views = [cycle_view(c, findings[c["id"]], profile, today) for c in cycles]
    return {"as_of": today, "escalate_before_days": ESCALATE_BEFORE_DAYS, "clock_days": CLOCK_DAYS,
            "open_cycle": next((v["id"] for v in views if not v["completed"]), None),
            "summary": {"total": len(views), "open": sum(1 for v in views if not v["completed"]),
                        "completed": sum(1 for v in views if v["completed"]),
                        "overdue": sum(1 for v in views if v["deadline_status"] == "overdue"),
                        "due_soon": sum(1 for v in views if v["deadline_status"] == "due_soon"),
                        "findings_open": sum(v["finding_counts"]["open"] + v["finding_counts"]["responded"] for v in views)},
            "cycles": views}


def _get_cycle_view(cur, cycle_id: int, today: date) -> dict:
    cur.execute("SELECT * FROM pr_cycle WHERE id = %s", (cycle_id,))
    c = cur.fetchone()
    findings = _load_findings(cur, [cycle_id])[cycle_id]
    return cycle_view(c, findings, profile_service.get_profile(cur), today)


def _validate_cycle(row: dict, today: date) -> None:
    if row["period_from"] and row["period_to"] and row["period_to"] < row["period_from"]:
        raise ValueError("period_to must be on or after period_from")
    for f in NOT_FUTURE:
        if row[f] is not None and row[f] > today:
            raise ValueError(f"{f} cannot be in the future")
    pairs = (("consent_at", "intimation_at"), ("questionnaire_sent", "consent_at"), ("visit_at", "consent_at"),
             ("findings_at", "consent_at"), ("response_sent", "findings_at"),
             ("preliminary_report_at", "findings_at"), ("final_report_at", "preliminary_report_at"))
    for later, earlier in pairs:
        if row[later] is not None and row[earlier] is not None and row[later] < row[earlier]:
            raise ValueError(f"{later} cannot be before {earlier}")
    if row["questionnaire_sent"] is not None and row["consent_at"] is None:
        raise ValueError("record the reviewer's consent date (consent_at) before the questionnaire is sent")
    if row["response_sent"] is not None and row["findings_at"] is None:
        raise ValueError("record the date the findings were received (findings_at) before the response is sent")
    if (row["final_report_at"] is None) != (row["outcome"] is None):
        raise ValueError("final_report_at and outcome must be recorded together")
    if row["outcome"] not in (None, "clean", "modified"):
        raise ValueError("outcome must be 'clean' or 'modified'")


def _derive_due(row: dict, existing: dict | None, changes: dict) -> None:
    """Fill a clock's due date from its start date, unless it was set by hand."""
    for start, due in (("consent_at", "questionnaire_due"), ("findings_at", "response_due")):
        if due in changes or row[start] is None:
            continue
        old_start = existing[start] if existing else None
        was_computed = existing is not None and old_start is not None and existing[due] == old_start + timedelta(days=CLOCK_DAYS)
        if row[due] is None or was_computed:
            row[due] = row[start] + timedelta(days=CLOCK_DAYS)


def create_cycle(cur, data: dict, today: date | None = None) -> dict:
    today = today or date.today()
    profile = profile_service.get_profile(cur)
    if profile is None:
        raise Conflict("Create the firm profile on the Standing screen before starting a review cycle.")
    cur.execute("SELECT selection_year FROM pr_cycle WHERE final_report_at IS NULL")
    open_cycle = cur.fetchone()
    if open_cycle:
        raise Conflict(f"The {open_cycle['selection_year']} cycle is still open. Close it before starting another.")
    sy = data["selection_year"]
    row = {f: data.get(f) for f in CYCLE_FIELDS}
    row["period_from"] = row["period_from"] or date(sy - 3, 1, 1)
    row["period_to"] = row["period_to"] or date(sy - 1, 12, 31)
    if data.get("final_report_at") or data.get("outcome"):
        raise ValueError("a new cycle cannot start with a final report; record it when the review ends")
    _derive_due(row, None, data)
    _validate_cycle(row, today)
    cols = ["profile_id", "selection_year"] + list(CYCLE_FIELDS)
    try:
        cur.execute(f"INSERT INTO pr_cycle ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
                    [profile["id"], sy] + [row[f] for f in CYCLE_FIELDS])
    except pgerr.UniqueViolation:
        raise Conflict(f"A cycle for selection year {sy} already exists.")
    return _get_cycle_view(cur, cur.fetchone()["id"], today)


def update_cycle(cur, cycle_id: int, changes: dict, today: date | None = None) -> dict:
    today = today or date.today()
    follow_on_months = changes.pop("follow_on_months", None)
    if not changes:
        raise ValueError("no fields to update")
    cur.execute("SELECT * FROM pr_cycle WHERE id = %s FOR UPDATE", (cycle_id,))
    existing = cur.fetchone()
    if existing is None:
        raise NotFound(f"review cycle {cycle_id} not found")
    if existing["final_report_at"] is not None:
        raise Conflict("This cycle is closed. A closed cycle is a record of what happened and is not edited.")
    for k, v in changes.items():
        if v is None and existing[k] is not None and k in MILESTONES + ("period_from", "period_to"):
            raise ValueError(f"{k} is already recorded and cannot be cleared; correct it to the right date")
        if v is None and k in ("period_from", "period_to"):
            raise ValueError(f"{k} cannot be null")
    row = {f: existing[f] for f in CYCLE_FIELDS}
    row.update(changes)
    _derive_due(row, existing, changes)
    _validate_cycle(row, today)

    closing = row["final_report_at"] is not None
    if follow_on_months is not None and not (closing and row["outcome"] == "modified"):
        raise ValueError("follow_on_months only applies when closing a cycle with a modified report")
    if closing:
        if row["findings_at"] is not None and row["response_sent"] is None:
            raise Conflict("The findings were received but the firm's response date is not recorded (response_sent).")
        if row["findings_at"] is not None and row["preliminary_report_at"] is None:
            raise Conflict("Record the preliminary report date (preliminary_report_at) before the final report.")
        cur.execute("SELECT ref FROM pr_finding WHERE cycle_id = %s AND closed_at IS NULL ORDER BY id", (cycle_id,))
        unresolved = [r["ref"] for r in cur.fetchall()]
        if unresolved:
            raise Conflict("The cycle cannot be closed while findings are unresolved: " + ", ".join(unresolved) + ".")

    cols = [c for c in CYCLE_FIELDS if row[c] != existing[c]]
    if cols:
        cur.execute(f"UPDATE pr_cycle SET {', '.join(c + ' = %s' for c in cols)} WHERE id = %s",
                    [row[c] for c in cols] + [cycle_id])

    note = None
    if closing:
        profile = profile_service.get_profile(cur)
        if profile["last_report_date"] is not None and profile["last_report_date"] > row["final_report_at"]:
            note = "The profile already holds a more recent report, so it was left as it is."
        else:
            profile_service.save_profile(
                cur, {"last_report_type": row["outcome"], "last_report_date": row["final_report_at"]},
                follow_on_months=follow_on_months if row["outcome"] == "modified" else None)
            note = ("Profile updated: follow-on review date set." if row["outcome"] == "modified"
                    else "Profile updated: last report recorded as clean.")
    view = _get_cycle_view(cur, cycle_id, today)
    if note:
        view["profile_note"] = note
    return view


# ================================================================== findings
def _check_engagement(cur, code):
    if code is None:
        return
    cur.execute("SELECT 1 FROM assurance_register WHERE engagement = %s", (code,))
    if cur.fetchone() is None:
        raise ValueError(f"engagement '{code}' is not in the assurance register")


def _next_ref(cur, cycle_id: int) -> str:
    cur.execute("SELECT ref FROM pr_finding WHERE cycle_id = %s", (cycle_id,))
    nums = [int(m.group(1)) for r in cur.fetchall() if (m := re.fullmatch(r"F-(\d+)", r["ref"]))]
    return f"F-{max(nums, default=0) + 1:03d}"


def create_finding(cur, cycle_id: int, data: dict, today: date | None = None) -> dict:
    today = today or date.today()
    cur.execute("SELECT * FROM pr_cycle WHERE id = %s FOR UPDATE", (cycle_id,))
    cycle = cur.fetchone()
    if cycle is None:
        raise NotFound(f"review cycle {cycle_id} not found")
    if cycle["final_report_at"] is not None:
        raise Conflict("This cycle is closed; findings cannot be added to it.")
    if cycle["findings_at"] is None:
        raise Conflict("Record the date the findings were received (findings_at) on the cycle first, "
                       "so the 15-day response clock starts from the right date.")
    if data.get("severity") is not None and data["severity"] not in SEVERITIES:
        raise ValueError("severity must be one of: " + ", ".join(SEVERITIES))
    _check_engagement(cur, data.get("engagement"))
    ref = data.get("ref") or _next_ref(cur, cycle_id)
    try:
        cur.execute(
            """INSERT INTO pr_finding (cycle_id, ref, control_area, description, engagement, severity, due_at, owner)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (cycle_id, ref, data["control_area"], data["description"], data.get("engagement"),
             data.get("severity"), data.get("due_at"), data.get("owner")))
    except pgerr.UniqueViolation:
        raise Conflict(f"finding '{ref}' already exists in this cycle")
    return finding_view(cur.fetchone(), cycle, today)


FINDING_FIELDS = ("control_area", "description", "engagement", "severity", "due_at", "owner", "pu_response",
                  "responded_at", "resolution", "corrective_action", "evidence_ref", "closed_at")
FINDING_NOT_NULL = ("control_area", "description")
NO_CLEAR = ("pu_response", "responded_at", "closed_at")


def update_finding(cur, finding_id: int, changes: dict, today: date | None = None) -> dict:
    today = today or date.today()
    resolved = changes.pop("resolved", None)
    resolved_on = changes.pop("resolved_on", None)
    if resolved is False:
        raise ValueError("a resolved finding is not reopened; a finding that is still open is simply left open")
    if resolved_on is not None and not resolved:
        raise ValueError("resolved_on is only used together with resolved")
    if resolved:
        changes["closed_at"] = resolved_on or today
    if not changes:
        raise ValueError("no fields to update")
    cur.execute("SELECT * FROM pr_finding WHERE id = %s FOR UPDATE", (finding_id,))
    existing = cur.fetchone()
    if existing is None:
        raise NotFound(f"finding {finding_id} not found")
    cur.execute("SELECT * FROM pr_cycle WHERE id = %s", (existing["cycle_id"],))
    cycle = cur.fetchone()
    if cycle["final_report_at"] is not None:
        raise Conflict("This cycle is closed; its findings are not edited.")

    for k, v in changes.items():
        if v is None and k in FINDING_NOT_NULL:
            raise ValueError(f"{k} cannot be null")
        if v is None and k in NO_CLEAR and existing[k] is not None:
            raise ValueError(f"{k} is already recorded and cannot be cleared; replace it instead")
    if "severity" in changes and changes["severity"] is not None and changes["severity"] not in SEVERITIES:
        raise ValueError("severity must be one of: " + ", ".join(SEVERITIES))
    if "engagement" in changes:
        _check_engagement(cur, changes["engagement"])
    if changes.get("pu_response") and "responded_at" not in changes and existing["responded_at"] is None:
        changes["responded_at"] = today
    if changes.get("responded_at") and not (changes.get("pu_response") or existing["pu_response"]):
        raise ValueError("responded_at needs the firm's response (pu_response)")

    row = {f: existing[f] for f in FINDING_FIELDS}
    row.update(changes)
    for f in ("responded_at", "closed_at"):
        if row[f] is not None and row[f] > today:
            raise ValueError(f"{f} cannot be in the future")
    if row["responded_at"] and cycle["findings_at"] and row["responded_at"] < cycle["findings_at"]:
        raise ValueError("the response cannot be dated before the findings were received")
    if row["closed_at"] is not None:
        missing = [n for n, v in (("the firm's written response", row["pu_response"]),
                                  ("the resolution", row["resolution"]),
                                  ("the corrective action", row["corrective_action"])) if not v]
        if missing:
            raise ValueError("a finding is resolved only when these are recorded: " + ", ".join(missing))
        if row["closed_at"] < row["responded_at"]:
            raise ValueError("resolved date cannot be before the response date")

    cols = list(changes)
    cur.execute(f"UPDATE pr_finding SET {', '.join(c + ' = %s' for c in cols)} WHERE id = %s RETURNING *",
                [changes[c] for c in cols] + [finding_id])
    return finding_view(cur.fetchone(), cycle, today)
