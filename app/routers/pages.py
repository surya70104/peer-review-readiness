"""Server-rendered screens: 1 Standing (/), 2 Scorecard, 3 Likely sample, 4 Registers, 5 Review tracker."""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app import db
from app.services import profile_service as profiles
from app.services import readiness_service as readiness
from app.services import registers_service as registers
from app.services import review_service as reviews
from app.services import staff_service as staff

ROOT = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))
router = APIRouter(include_in_schema=False)

ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV"}


# ------------------------------------------------------------------ filters
def fmt_date(v) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return f"{v.day:02d} {v:%b %Y}"


def inr(v) -> str:
    """Indian digit grouping: 4,50,000."""
    if v is None:
        return "—"
    d = Decimal(v).quantize(Decimal("0.01"))
    whole, frac = f"{abs(d):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    out = ",".join(groups + [tail]) if groups else tail
    if frac != "00":
        out += "." + frac
    return ("-" if d < 0 else "") + "₹ " + out


def phase_label(n) -> str:
    return "Not applicable" if not n else f"Phase {ROMAN[int(n)]}"


def _parse_date(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


ROLE_LABELS = {"articled_assistant": "Articled assistant", "audit_assistant": "Audit assistant",
               "audit_staff": "Audit staff"}
MODE_LABELS = {"bank_transfer": "Bank transfer", "cheque": "Cheque", "cash": "Cash", "upi": "UPI", "other": "Other"}


def fmt_month(v) -> str:
    return "—" if v is None else f"{v:%b %Y}"


def fmt_score(v) -> str:
    if v is None:
        return "—"
    out = f"{float(v):.1f}"
    return out[:-2] if out.endswith(".0") else out


def score_tone(v) -> str:
    """Display colour only: green from 90, amber from 70, red below."""
    if v is None:
        return ""
    return "ok" if v >= 90 else "warn" if v >= 70 else "danger"


templates.env.filters.update(score=fmt_score, tone=score_tone)
templates.env.filters.update(date=fmt_date, inr=inr, phase=phase_label, month=fmt_month,
                             role=lambda r: ROLE_LABELS.get(r, r or "—"),
                             mode=lambda m: MODE_LABELS.get(m, m or "—"))


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month


# ------------------------------------------------------------------ screens
@router.get("/")
def standing(request: Request, as_of: str | None = None):
    today = date.today()
    as_of_date = _parse_date(as_of)
    eff = as_of_date or today
    with db.get_conn() as conn, profiles.dict_cursor(conn) as cur:
        profile = profiles.get_profile(cur)
        view = profiles.build_view(cur, profile, eff) if profile else None

    ctx = {"active": "standing", "view": view, "as_of": as_of_date, "eff": eff, "today": today,
           "simulated": as_of_date is not None and as_of_date != today}
    if view:
        p, app_, clock, fo = view["profile"], view["applicability"], view["clock"], view["follow_on"]
        months = 12
        if fo["due"] and fo["last_report_date"]:
            months = min(max(_months_between(fo["last_report_date"], fo["due"]), 6), 12)
        ctx.update(
            follow_on_months=months,
            days_to_phase=(app_["effective"] - eff).days if app_["effective"] else None,
            max_history_id=max((h["id"] for h in view["history"]), default=0),
            initial={
                "firm": p["firm"], "level": p["level"],
                "audits_listed_entities": p["audits_listed_entities"],
                "audits_large_unlisted_public": p["audits_large_unlisted_public"],
                "audits_entities_raised_50cr": p["audits_entities_raised_50cr"],
                "audits_psb_branches": p["audits_psb_branches"],
                "renders_attestation": p["renders_attestation"],
                "certificate_no": p["certificate_no"] or "",
                "effective_from": p["effective_from"].isoformat() if p["effective_from"] else "",
                "expires_on": p["expires_on"].isoformat() if p["expires_on"] else "",
                "last_report_type": p["last_report_type"] or "",
                "last_report_date": p["last_report_date"].isoformat() if p["last_report_date"] else "",
                "follow_on_months": months,
            },
        )
    return templates.TemplateResponse(request, "standing.html", ctx)


@router.get("/registers")
def registers_page(request: Request, partner: str | None = None, service: str | None = None,
                   role: str | None = None, period: str | None = None):
    partner, service = (partner or "").strip() or None, (service or "").strip() or None
    role, period = (role or "").strip() or None, (period or "").strip() or None
    if role not in ROLE_LABELS:
        role = None
    try:
        period = staff.parse_month(period).strftime("%Y-%m") if period else None
    except ValueError:
        period = None
    with db.get_conn() as conn, registers.dict_cursor(conn) as cur:
        assurance = registers.list_assurance(cur, partner, service)
        sqc = registers.list_sqc(cur)
        staff_reg = staff.list_staff(cur, role, period)
        cur.execute("SELECT DISTINCT month FROM staff_compliance ORDER BY month DESC")
        staff_months = [r["month"] for r in cur.fetchall()]

    rows = [s for c in assurance["client_list"] for s in c["services"]]
    partners = sorted({m["partner"] for m in assurance["summary"]["audits_per_member"]})
    services = sorted({r["service"] for r in rows}, key=str.lower)
    shown_fees = sum((e["fee"] or Decimal("0") for e in assurance["engagements"]), Decimal("0"))
    return templates.TemplateResponse(request, "registers.html", {
        "active": "registers", "a": assurance, "sqc": sqc,
        "partners": partners, "services": services, "shown_fees": shown_fees,
        "without_udin": sum(1 for r in rows if not r["udin"]),
        "f_partner": partner or "", "f_service": service or "",
        "today": date.today(),
        "st": staff_reg, "staff_months": staff_months, "roles": ROLE_LABELS, "modes": MODE_LABELS,
        "f_role": role or "", "f_period": period or "",
        "current_month": date.today().strftime("%Y-%m"),
    })


# ------------------------------------------------------------ Pass 2 screens
def _year(v: str | None) -> int | None:
    try:
        n = int((v or "").strip())
    except ValueError:
        return None
    return n if 2020 <= n <= 2100 else None


@router.get("/scorecard")
def scorecard_page(request: Request, selection_year: str | None = None):
    sy = _year(selection_year)
    with db.get_conn(commit=False) as conn, registers.dict_cursor(conn) as cur:
        sc = readiness.scorecard(cur, sy)
    return templates.TemplateResponse(request, "scorecard.html", {
        "active": "scorecard", "sc": sc, "f_year": sy or "",
        "assessed": readiness.ASSESSED, "not_assessed": readiness.NOT_ASSESSED,
        "dimensions": readiness.LATER_ADDITION_DIMENSIONS, "today": date.today(),
    })


@router.get("/likely-sample")
def likely_sample_page(request: Request, selection_year: str | None = None, limit: str | None = None):
    sy = _year(selection_year)
    top = None if (limit or "").lower() == "all" else (int(limit) if (limit or "").isdigit() and 0 < int(limit) <= 1000 else 25)
    with db.get_conn(commit=False) as conn, registers.dict_cursor(conn) as cur:
        ls = readiness.likely_sample(cur, top, sy)
    return templates.TemplateResponse(request, "likely_sample.html", {
        "active": "sample", "ls": ls, "f_year": sy or "", "f_limit": "all" if top is None else str(top),
        "weights": readiness.WEIGHTS, "today": date.today(),
    })


@router.get("/tracker")
def tracker_page(request: Request):
    today = date.today()
    with db.get_conn(commit=False) as conn, registers.dict_cursor(conn) as cur:
        lr = reviews.list_cycles(cur, today)
        profile = profiles.get_profile(cur)
        cur.execute("SELECT engagement, client FROM assurance_register ORDER BY engagement")
        engagements = cur.fetchall()
        default_year = readiness.resolve_window(cur, profile, None, today)["selection_year"] if profile else today.year + 1
    open_cycles = [c for c in lr["cycles"] if not c["completed"]]
    history = [c for c in lr["cycles"] if c["completed"]]
    return templates.TemplateResponse(request, "tracker.html", {
        "active": "tracker", "lr": lr, "open_cycles": open_cycles, "history": history,
        "has_profile": profile is not None, "engagements": engagements, "default_year": default_year,
        "areas": readiness.REQUIRED_SQC_AREAS, "today": today,
    })
