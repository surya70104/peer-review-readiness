"""Server-rendered screens: 1 · Standing (/) and 4 · Registers (/registers)."""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app import db
from app.services import profile_service as profiles
from app.services import registers_service as registers

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


templates.env.filters.update(date=fmt_date, inr=inr, phase=phase_label)


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
def registers_page(request: Request, partner: str | None = None, service: str | None = None):
    partner, service = (partner or "").strip() or None, (service or "").strip() or None
    with db.get_conn() as conn, registers.dict_cursor(conn) as cur:
        assurance = registers.list_assurance(cur, partner, service)
        sqc = registers.list_sqc(cur)

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
    })
