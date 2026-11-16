"""Backward-planned renewal clock (spec section 2) and follow-on rule (section 6).

expires_on is ALWAYS read from the certificate. It is never derived from
effective_from. The clock plans backward from it through every stage and
returns the date by which the process must start.

Stage lengths are planning assumptions, kept as plain constants so they are
easy to read and change. Only the two 15-day clocks (questionnaire; firm's
response) are ICAI deadlines; the other lengths are the firm's planning
allowance. They sum to 157 days, which reproduces the spec's worked example:
certificate expiring 14 Nov -> must start by 10 Jun.
"""
import calendar
from datetime import date, timedelta

# (key, description, days) in FORWARD order. The final stage must END on or
# before certificate expiry, because a final clean report submitted before
# expiry makes the new certificate run seamlessly from the next day.
STAGES: tuple[tuple[str, str, int], ...] = (
    ("application_selection", "Application, Board selection and reviewer appointment", 45),
    ("reviewer_consent", "Reviewer consent", 7),
    ("questionnaire", "Questionnaire window (15-day clock from reviewer's consent)", 15),
    ("sample_onsite", "Reviewer's sample selection and on-site work", 30),
    ("findings_response", "Findings and firm's written response (15-day clock from findings)", 15),
    ("preliminary_report", "Preliminary report", 30),
    ("final_report", "Final clean report submission", 15),
)
TOTAL_LEAD_DAYS: int = sum(d for _, _, d in STAGES)  # 157

FOLLOW_ON_MIN_MONTHS = 6
FOLLOW_ON_MAX_MONTHS = 12


def add_months(d: date, months: int) -> date:
    idx = d.month - 1 + months
    year, month = d.year + idx // 12, idx % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _schedule_backward(expires_on: date) -> list[dict]:
    """Stage k ends where stage k+1 starts; the last stage ends on expires_on."""
    out: list[dict] = []
    end = expires_on
    for key, label, days in reversed(STAGES):
        start = end - timedelta(days=days)
        out.append({"key": key, "stage": label, "days": days, "start": start, "end": end})
        end = start
    out.reverse()
    return out


def _schedule_forward(begin: date) -> list[dict]:
    out: list[dict] = []
    start = begin
    for key, label, days in STAGES:
        end = start + timedelta(days=days)
        out.append({"key": key, "stage": label, "days": days, "start": start, "end": end})
        start = end
    return out


def compute_start_by(expires_on: date | None) -> date | None:
    if expires_on is None:
        return None
    return expires_on - timedelta(days=TOTAL_LEAD_DAYS)


def assess(expires_on: date | None, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    base = {
        "as_of": as_of,
        "lead_time_days": TOTAL_LEAD_DAYS,
        "stages_total_days": TOTAL_LEAD_DAYS,
    }
    if expires_on is None:
        return {**base, "status": "no_certificate", "expires_on": None, "start_by": None,
                "schedule": [], "projected_schedule": _schedule_forward(as_of),
                "projected_final_report": as_of + timedelta(days=TOTAL_LEAD_DAYS),
                "has_gap_warning": False, "gap_days": 0, "warning_message": None,
                "note": "No certificate recorded; enter the certificate's own expiry date to plan the renewal."}

    start_by = compute_start_by(expires_on)
    projected_final = as_of + timedelta(days=TOTAL_LEAD_DAYS)
    gap_days = max((projected_final - expires_on).days, 0)
    has_gap = projected_final > expires_on  # equivalent to as_of > start_by
    expired = as_of > expires_on

    message = None
    if has_gap:
        restriction = "the firm cannot sign listed company audit reports"
        if expired:
            message = (f"The certificate expired on {expires_on:%d %b %Y}. Even if the review starts today "
                       f"({as_of:%d %b %Y}) the final clean report cannot be submitted before "
                       f"{projected_final:%d %b %Y}. The new certificate runs only from the date of submission, "
                       f"so from {expires_on + timedelta(days=1):%d %b %Y} until then {restriction}.")
        else:
            message = (f"The review had to start by {start_by:%d %b %Y}. Starting today ({as_of:%d %b %Y}) "
                       f"puts the final clean report on {projected_final:%d %b %Y}, {gap_days} days after the "
                       f"certificate expires on {expires_on:%d %b %Y}. The new certificate would run only from "
                       f"submission, so for that gap ({expires_on + timedelta(days=1):%d %b %Y} to "
                       f"{projected_final:%d %b %Y}) {restriction}.")

    return {
        **base,
        "status": "expired" if expired else ("gap_warning" if has_gap else "on_track"),
        "expires_on": expires_on,
        "start_by": start_by,
        "days_to_start_by": (start_by - as_of).days,
        "days_to_expiry": (expires_on - as_of).days,
        "schedule": _schedule_backward(expires_on),
        "projected_schedule": _schedule_forward(as_of),
        "projected_final_report": projected_final,
        "has_gap_warning": has_gap,
        "gap_days": gap_days,
        "warning_message": message,
        "note": None,
    }


def follow_on_due(report_date: date, months: int | None = None) -> date:
    """Follow-on review date after a modified final report: 12 months by default;
    the Board may shorten it, but not below 6 months."""
    months = FOLLOW_ON_MAX_MONTHS if months is None else months
    if not FOLLOW_ON_MIN_MONTHS <= months <= FOLLOW_ON_MAX_MONTHS:
        raise ValueError(
            f"follow-on period must be between {FOLLOW_ON_MIN_MONTHS} and {FOLLOW_ON_MAX_MONTHS} months")
    return add_months(report_date, months)
