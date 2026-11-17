"""Readiness scorecard and likely-sample selector (spec sections 4, 5, 7, 9; acceptance 3, 4, 5, 8, 9).

Everything here is rule-based and reproducible: the same recorded facts and the
same as-of date give the same scores and the same ranking, every time, with the
rule behind every number named in the output. There is no model and no
randomness. Weights and thresholds are the plain constants below.

What is scored, and what is not
-------------------------------
Scores are computed only from facts this module records. Three engagement
dimensions can be evidenced from the assurance register today:
  register  fee recorded; engagement period recorded
  SA 700    report dated once the period has ended; not dated before the period ends
  UDIN      UDIN recorded on a dated report; well formed (18 letters/digits)
SA 210, SA 230, SA 300, SA 580 and independence need file-level evidence the
module does not capture yet. They are reported as "not assessed" rather than
given a made-up score, and the scorecard shows how many dimensions it covers.

Honesty by construction
-----------------------
Documented later additions (spec section 7) never change a contemporaneous
score, up or down. They are listed beside the engagement as what they are, and
they count as one "something unusual" driver in the sample ranking. They are
read from the append-only eng_later_addition ledger (migration 0006).
"""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from psycopg2.extras import Json

from app.services import applicability, clock, profile_service
from app.services import staff_service
from app.services.registers_service import Conflict, NotFound  # noqa: F401  (re-exported)

# ---------------------------------------------------------------- constants
ASSEMBLY_DAYS = 60            # SA 230: time allowed to assemble the file after the report date
REPORT_GRACE_DAYS = 90        # planning allowance: report expected within this long of period end
WINDOW_YEARS = 3              # spec section 3
UDIN_RE = re.compile(r"^[A-Za-z0-9]{18}$")

ASSESSED = ("register", "SA 700", "UDIN")
NOT_ASSESSED = {
    "SA 210": "Engagement terms are not captured in this module yet.",
    "SA 230": "File assembly dates are not captured yet; only documented later additions are shown.",
    "SA 300": "Planning documentation is not captured in this module yet.",
    "SA 580": "Representation letters are not captured in this module yet.",
    "independence": "Independence declarations are not captured in this module yet.",
}
LATER_ADDITION_DIMENSIONS = ("SA 210", "SA 230", "SA 300", "SA 580", "SA 700",
                             "independence", "UDIN", "register")

# Likely-sample driver weights: points out of 100, shown against every engagement.
WEIGHTS = {"trigger": 25, "level1": 10, "statutory": 10, "fee": 25,
           "partner_spread": 10, "year_spread": 5, "readiness": 10, "unusual": 5}
LEVEL1_CATEGORIES = ("listed", "level1_pie")
PHASE_BY_FLAG = {p.flag_field: p.label for p in applicability.PHASES}
CATEGORY_FLAG = {"listed": "audits_listed_entities", "large_unlisted_public": "audits_large_unlisted_public",
                 "raised_50cr": "audits_entities_raised_50cr", "psb_branch": "audits_psb_branches"}
CATEGORY_LABEL = {"listed": "listed entity", "level1_pie": "Level I public interest entity",
                  "large_unlisted_public": "large unlisted public company", "raised_50cr": "entity that raised over Rs 50 cr",
                  "psb_branch": "Public Sector Bank branch audit", "other": "other"}

# The five control areas the reviewer's report addresses (spec section 4).
REQUIRED_SQC_AREAS = ("Independence",
                      "Maintenance of professional skills and standards",
                      "Outside consultation",
                      "Staff recruitment, supervision and development",
                      "Office administration")

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
PILLAR_ORDER = {"engagement_files": 0, "sqc1": 1, "staff": 2}


def _d(v: date | None) -> str:
    return "—" if v is None else f"{v.day:02d} {v:%b %Y}"


def _pct(passed: int, total: int) -> float | None:
    return round(passed * 100 / total, 1) if total else None


def _gap(dimension, code, severity, message, action):
    return {"dimension": dimension, "code": code, "severity": severity, "message": message, "action": action}


# ================================================================== window
def resolve_window(cur, profile: dict | None, selection_year: int | None, as_of: date) -> dict:
    """The rolling review window: the three years before the selection year (acceptance 3).

    With no year given, the year is taken without manual input: an open review
    cycle's selection year; else the year the certificate's start-by date falls
    in (the review that has to start is the one that will select the firm); else
    the current year. A recorded cycle's own period dates win when present.
    """
    source = "requested selection year"
    cycle = None
    if selection_year is None:
        cur.execute("SELECT * FROM pr_cycle WHERE final_report_at IS NULL ORDER BY selection_year DESC LIMIT 1")
        cycle = cur.fetchone()
        if cycle:
            selection_year, source = cycle["selection_year"], "open review cycle"
        elif profile and profile.get("expires_on"):
            selection_year = clock.compute_start_by(profile["expires_on"]).year
            source = "year the review must start, from the certificate expiry"
        else:
            selection_year, source = as_of.year, "current year (no certificate or cycle recorded)"
    else:
        cur.execute("SELECT * FROM pr_cycle WHERE selection_year = %s", (selection_year,))
        cycle = cur.fetchone()
    if cycle:
        wf, wt = cycle["period_from"], cycle["period_to"]
        source = "review cycle " + str(selection_year)
    else:
        wf, wt = date(selection_year - WINDOW_YEARS, 1, 1), date(selection_year - 1, 12, 31)
    return {"selection_year": selection_year, "from": wf, "to": wt, "source": source}


# ================================================================== engagements
def evaluate_engagement(e: dict, as_of: date, later: list[dict]) -> dict:
    rd, pe = e["report_date"], e["period_to"]
    checks = []  # (dimension, code, passed, gap-or-None)

    def check(dim, code, passed, gap=None):
        checks.append((dim, code, passed, None if passed else gap))

    # register: fee and engagement metadata
    check("register", "fee_recorded", e["fee"] is not None,
          _gap("register", "fee_missing", "medium", "Fee not recorded",
               "Record the fee: the questionnaire needs fees for every assurance client."))
    check("register", "period_recorded", bool(e["period_from"] and e["period_to"]),
          _gap("register", "period_missing", "low", "Engagement period not recorded",
               "Record the period the engagement covers."))

    # SA 700: reporting
    overdue = pe is not None and as_of > pe + timedelta(days=REPORT_GRACE_DAYS)
    if rd is not None or overdue:
        days = (as_of - pe).days if pe else 0
        check("SA 700", "report_dated", rd is not None,
              _gap("SA 700", "report_date_missing", "medium",
                   f"Report date not recorded; the period ended {days} days ago",
                   "Record the date the report was signed, or confirm the engagement is still open."))
        if rd is not None and pe is not None:
            check("SA 700", "report_after_period_end", rd >= pe,
                  _gap("SA 700", "report_before_period_end", "high",
                       f"Report is dated {_d(rd)}, before the period ended on {_d(pe)}",
                       "Correct the report date or the period."))

    # UDIN: only meaningful once a report is dated
    if rd is not None:
        udin = (e["udin"] or "").strip()
        check("UDIN", "udin_present", bool(udin),
              _gap("UDIN", "udin_missing", "high", "UDIN not recorded on a signed report",
                   "Generate the UDIN for this report and record it."))
        if udin:
            check("UDIN", "udin_format", bool(UDIN_RE.match(udin)),
                  _gap("UDIN", "udin_malformed", "medium", f"UDIN '{udin}' is not 18 letters and digits",
                       "Check the UDIN against the ICAI portal and correct it."))

    dims: dict[str, dict] = {}
    for dim, _code, passed, _gap_ in checks:
        d = dims.setdefault(dim, {"checks_passed": 0, "checks_total": 0, "gaps": []})
        d["checks_total"] += 1
        d["checks_passed"] += 1 if passed else 0
    for dim, _code, passed, g in checks:
        if g:
            dims[dim]["gaps"].append(g)
    for d in dims.values():
        d["score"] = _pct(d["checks_passed"], d["checks_total"])

    total = sum(d["checks_total"] for d in dims.values())
    passed = sum(d["checks_passed"] for d in dims.values())
    gaps = sorted((g for d in dims.values() for g in d["gaps"]),
                  key=lambda g: (SEVERITY_ORDER[g["severity"]], g["dimension"]))
    if any(g["severity"] == "high" for g in gaps):
        status = "at_risk"
    elif gaps:
        status = "needs_attention"
    else:
        status = "ready" if rd is not None else "open"

    closes = rd + timedelta(days=ASSEMBLY_DAYS) if rd else None
    file_date = rd or pe
    return {
        "id": e["id"], "engagement": e["engagement"], "client": e["client"], "service": e["service"],
        "partner": e["partner"], "fee": e["fee"], "entity_category": e["entity_category"],
        "period_from": e["period_from"], "period_to": pe, "report_date": rd, "udin": e["udin"],
        "file_date": file_date, "file_state": "reported" if rd else "open",
        "assembly_window_closes": closes, "assembly_closed": bool(closes and as_of > closes),
        "score": _pct(passed, total), "status": status, "dimensions": dims, "gaps": gaps,
        # Shown as what they are; never counted in the contemporaneous score.
        "later_additions": later, "later_addition_count": len(later),
    }


# ================================================================== likely sample
def _triggers(profile: dict | None, as_of: date) -> dict:
    """Whether the partner ladder brings the firm into a phase. If it does, it covers every statutory audit."""
    if not profile:
        return {"ladder": False}
    ev = applicability.evaluate(profile, as_of)
    return {"ladder": any(b.startswith("Practice unit rendering attestation")
                          for ph in ev["phases_applying"] for b in ph["bases"])}


def rank_sample(window_engs: list[dict], profile: dict | None, as_of: date) -> list[dict]:
    """Rank in-window engagements on the drivers a reviewer works from (spec section 5)."""
    trig = _triggers(profile, as_of)
    max_fee = max((e["fee"] or Decimal(0) for e in window_engs), default=Decimal(0))

    def top(group_key):
        """Largest-fee engagement per group; ties go to the smaller engagement code."""
        best: dict = {}
        for e in window_engs:
            if e["fee"] is None:
                continue
            k = group_key(e)
            b = best.get(k)
            if b is None or e["fee"] > b["fee"] or (e["fee"] == b["fee"] and e["engagement"] < b["engagement"]):
                best[k] = e
        return {k: v["engagement"] for k, v in best.items()}

    top_partner = top(lambda e: e["partner"].strip().casefold())
    top_year = top(lambda e: e["file_date"].year)

    ranked = []
    for e in window_engs:
        statutory = "statutory audit" in e["service"].casefold()
        cat = e["entity_category"]
        drivers = []

        def add(code, label, points, detail):
            drivers.append({"code": code, "label": label, "points": round(points, 2),
                            "max_points": WEIGHTS[code], "detail": detail})

        if cat in CATEGORY_FLAG:
            note = "" if profile and profile.get(CATEGORY_FLAG[cat]) else                 " The firm profile does not record that the firm audits these; update it on the Standing screen."
            add("trigger", "In the category that triggers the firm's phase", WEIGHTS["trigger"],
                f"{CATEGORY_LABEL[cat].capitalize()} audits are a Phase {PHASE_BY_FLAG[CATEGORY_FLAG[cat]]} trigger category.{note}")
        elif trig["ladder"] and statutory:
            add("trigger", "In the category that triggers the firm's phase", WEIGHTS["trigger"],
                "The partner ladder applies to the firm, and it covers every statutory audit.")
        else:
            add("trigger", "In the category that triggers the firm's phase", 0,
                "Not in a triggering category." if cat else "Entity category not recorded, so category triggers cannot be checked.")
        add("level1", "Level I category", WEIGHTS["level1"] if cat in LEVEL1_CATEGORIES else 0,
            f"{CATEGORY_LABEL[cat].capitalize()} is a Level I category." if cat in LEVEL1_CATEGORIES else "Not a Level I category.")
        add("statutory", "Statutory audit", WEIGHTS["statutory"] if statutory else 0,
            "Statutory audit." if statutory else f"Service is '{e['service']}'.")
        fee_pts = float(WEIGHTS["fee"] * e["fee"] / max_fee) if e["fee"] is not None and max_fee > 0 else 0
        add("fee", "Fee size, a proxy for significance", fee_pts,
            "Fee not recorded." if e["fee"] is None else
            f"Fee is {float(e['fee'] / max_fee):.0%} of the largest fee in the window.")
        is_tp = top_partner.get(e["partner"].strip().casefold()) == e["engagement"]
        add("partner_spread", "Largest engagement of its partner", WEIGHTS["partner_spread"] if is_tp else 0,
            f"A reviewer tests the firm, not one partner; this is {e['partner']}'s largest file." if is_tp
            else f"Not {e['partner']}'s largest file in the window.")
        is_ty = top_year.get(e["file_date"].year) == e["engagement"]
        add("year_spread", f"Largest engagement of {e['file_date'].year}", WEIGHTS["year_spread"] if is_ty else 0,
            f"Spread across years: the largest file of {e['file_date'].year}." if is_ty else "Not the largest file of its year.")
        add("readiness", "Readiness shortfall", WEIGHTS["readiness"] * (100 - (e["score"] or 0)) / 100,
            f"Readiness score is {e['score']}.")
        unusual = e["later_addition_count"] > 0 or any(g["severity"] == "high" for g in e["gaps"])
        add("unusual", "Something unusual on the file", WEIGHTS["unusual"] if unusual else 0,
            "Has documented later additions or a high-severity gap." if unusual else "Nothing unusual recorded.")
        risk = round(sum(d["points"] for d in drivers), 2)
        ranked.append({"e": e, "risk": risk, "drivers": drivers})

    ranked.sort(key=lambda r: (-r["risk"], -(r["e"]["fee"] or Decimal(0)), r["e"]["engagement"]))
    out = []
    for i, r in enumerate(ranked, 1):
        e = r["e"]
        out.append({
            "rank": i, "engagement": e["engagement"], "engagement_id": e["id"], "client": e["client"],
            "service": e["service"], "partner": e["partner"], "fee": e["fee"],
            "entity_category": e["entity_category"], "file_date": e["file_date"],
            "risk_score": r["risk"], "max_score": sum(WEIGHTS.values()),
            "drivers": r["drivers"],
            "reasons": [d["detail"] for d in r["drivers"] if d["points"] > 0],
            "readiness_score": e["score"], "status": e["status"], "gaps": e["gaps"],
            "later_addition_count": e["later_addition_count"],
        })
    return out


# ================================================================== SQC 1 pillar
def sqc_pillar(policies: list[dict], as_of: date) -> dict:
    current: dict[str, dict] = {}
    for p in policies:
        if p["effective_from"] > as_of:
            continue
        k = p["area"].strip().casefold()
        if k not in current or (p["effective_from"], p["id"]) > (current[k]["effective_from"], current[k]["id"]):
            current[k] = p
    areas = [(a, a.casefold()) for a in REQUIRED_SQC_AREAS]
    seen = {k for _, k in areas}
    for k, p in sorted(current.items()):
        if k not in seen:
            areas.append((p["area"].strip(), k))

    gaps, rows, passed, total = [], [], 0, 0
    for name, k in areas:
        p = current.get(k)
        total += 3
        row = {"area": name, "version": p["policy_version"] if p else None, "recorded": bool(p),
               "acknowledged": bool(p and p["acknowledged_by"]),
               "evidence_of_operation": bool(p and p["evidence_of_operation"])}
        rows.append(row)
        passed += row["recorded"] + row["acknowledged"] + row["evidence_of_operation"]
        if not p:
            gaps.append(_gap("sqc1", "policy_missing", "high", f"{name}: no SQC 1 policy in effect",
                             "Record the firm's policy for this control area."))
            continue
        if not row["acknowledged"]:
            gaps.append(_gap("sqc1", "not_acknowledged", "medium",
                             f"{name} ({p['policy_version']}): not acknowledged",
                             "Record who acknowledged the policy."))
        if not row["evidence_of_operation"]:
            gaps.append(_gap("sqc1", "no_evidence", "medium",
                             f"{name} ({p['policy_version']}): no evidence of operation",
                             "Record what shows the policy was followed. The manual alone is not evidence."))
    return {"pillar": "sqc1", "label": "SQC 1 policies", "score": _pct(passed, total),
            "checks_passed": passed, "checks_total": total, "areas": rows, "gaps": gaps,
            "note": None}


# ================================================================== staff pillar
def staff_pillar(records: list[dict], window: dict) -> dict:
    recs = [r for r in records if window["from"] <= r["month"] <= window["to"]]
    base = {"pillar": "staff", "label": "Staff compliance"}
    if not recs:
        return {**base, "score": None, "checks_passed": 0, "checks_total": 0, "gaps": [],
                "people": 0, "compliant_people": 0, "compliance_rate": None,
                "note": "No staff compliance records fall in the review window, so this pillar is not assessed."}
    passed = total = 0
    gaps = []
    people: dict[str, dict] = {}
    code_check = {"missing_work_diary": "diary", "stipend_unpaid": "stipend",
                  "stipend_not_recorded": "stipend", "training_below_requirement": "training"}
    for r in recs:
        flags = staff_service.flags_for(r)
        trainee = r["role"] in staff_service.TRAINEE_ROLES
        applicable = (["diary", "stipend"] if trainee else []) + (["training"] if r["training_required"] > 0 else [])
        failed = {code_check[f["code"]] for f in flags}
        applicable = list(dict.fromkeys(applicable + [c for c in failed]))
        total += len(applicable)
        passed += len(applicable) - len(failed)
        p = people.setdefault(r["person"].strip().casefold(), {"person": r["person"].strip(), "role": r["role"],
                                                                 "months": set(), "flagged": 0})
        p["months"].add(r["month"])
        if flags:
            p["flagged"] += 1
        for f in flags:
            sev = "high" if f["code"] in ("stipend_unpaid", "stipend_not_recorded") else "medium"
            action = {"missing_work_diary": "Obtain and file the work diary.",
                      "stipend_unpaid": "Pay the stipend and record the date and mode of payment.",
                      "stipend_not_recorded": "Record the stipend amount and how it was paid.",
                      "training_below_requirement": "Arrange the remaining training hours and record them."}[f["code"]]
            gaps.append(_gap("staff", f["code"], sev,
                             f"{r['person']}, {r['month']:%b %Y}: {f['label'].lower()}", action))
    # A month with no record at all between a trainee's first and last record is a missing record.
    for p in people.values():
        if p["role"] not in staff_service.TRAINEE_ROLES:
            continue
        m, last = min(p["months"]), max(p["months"])
        while m < last:
            m = clock.add_months(m, 1)
            if m not in p["months"] and m < last:
                total += 1
                gaps.append(_gap("staff", "record_missing", "high",
                                 f"{p['person']}, {m:%b %Y}: no attendance, diary or stipend record",
                                 "Record this month. If it genuinely was not kept, say so in the notes."))
    compliant = sum(1 for p in people.values() if p["flagged"] == 0)
    n_missing = sum(1 for g in gaps if g["code"] == "record_missing")
    return {**base, "score": _pct(passed, total), "checks_passed": passed, "checks_total": total,
            "gaps": gaps, "people": len(people), "compliant_people": compliant,
            "compliance_rate": _pct(compliant, len(people)), "missing_records": n_missing, "note": None}


# ================================================================== assembling the picture
def _load(cur, selection_year: int | None, as_of: date) -> dict:
    cur.execute("SELECT * FROM assurance_register ORDER BY engagement")
    rows = cur.fetchall()
    cur.execute("SELECT * FROM eng_later_addition ORDER BY id")
    later: dict[str, list] = {}
    for a in cur.fetchall():
        later.setdefault(a["engagement"], []).append(a)
    cur.execute("SELECT * FROM sqc_policy")
    policies = cur.fetchall()
    cur.execute("SELECT * FROM staff_compliance ORDER BY month, id")
    staff = cur.fetchall()
    profile = profile_service.get_profile(cur)
    window = resolve_window(cur, profile, selection_year, as_of)

    evaluated = [evaluate_engagement(e, as_of, later.get(e["engagement"], [])) for e in rows]
    inside, outside = [], []
    for ev in evaluated:
        if ev["file_date"] is None:
            ev["window_note"] = "No report date or period end recorded, so it cannot be placed in the window."
            outside.append(ev)
        elif window["from"] <= ev["file_date"] <= window["to"]:
            inside.append(ev)
        else:
            ev["window_note"] = ("After the window: it falls in a later review window."
                                 if ev["file_date"] > window["to"] else "Before the window.")
            outside.append(ev)
    sample = rank_sample(inside, profile, as_of)
    by_code = {s["engagement"]: s for s in sample}
    for ev in inside:
        ev["sample_rank"], ev["risk_score"] = by_code[ev["engagement"]]["rank"], by_code[ev["engagement"]]["risk_score"]
    return {"profile": profile, "window": window, "inside": inside, "outside": outside, "sample": sample,
            "policies": policies, "staff": staff}


def _engagement_pillar(inside: list[dict]) -> dict:
    passed = sum(d["checks_passed"] for e in inside for d in e["dimensions"].values())
    total = sum(d["checks_total"] for e in inside for d in e["dimensions"].values())
    gaps = [{**g, "engagement": e["engagement"],
             "message": f"{e['engagement']} ({e['client']}): {g['message']}"} for e in inside for g in e["gaps"]]
    counts = {k: sum(1 for e in inside if e["status"] == k) for k in ("ready", "open", "needs_attention", "at_risk")}
    return {"pillar": "engagement_files", "label": "Engagement files", "score": _pct(passed, total) if inside else None,
            "checks_passed": passed, "checks_total": total, "engagements": len(inside), "status_counts": counts,
            "gaps": gaps,
            "note": None if inside else "No engagements fall in the review window, so this pillar is not assessed."}


def _persisted(cur, window: dict, inside: list[dict]) -> dict:
    cur.execute("SELECT engagement, score, sample_rank, computed_at FROM eng_score "
                "WHERE dimension = 'overall' AND window_from = %s AND window_to = %s",
                (window["from"], window["to"]))
    stored = {r["engagement"]: r for r in cur.fetchall()}
    in_sync = len(stored) == len(inside) and all(
        e["engagement"] in stored and float(stored[e["engagement"]]["score"]) == float(e["score"])
        and stored[e["engagement"]]["sample_rank"] == e["sample_rank"] for e in inside)
    return {"rows": len(stored), "in_sync": in_sync,
            "last_recomputed_at": max((r["computed_at"] for r in stored.values()), default=None)}


def scorecard(cur, selection_year: int | None = None, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    ctx = _load(cur, selection_year, as_of)
    inside, window = ctx["inside"], ctx["window"]
    eng = _engagement_pillar(inside)
    sqc = sqc_pillar(ctx["policies"], as_of)
    stf = staff_pillar(ctx["staff"], window)
    pillars = {"engagement_files": eng, "sqc1": sqc, "staff": stf}

    scores = [p["score"] for p in pillars.values() if p["score"] is not None]
    unassessed = [p["label"] for p in pillars.values() if p["score"] is None]
    actions = []
    for key, p in pillars.items():
        for g in p["gaps"]:
            actions.append({"pillar": key, "severity": g["severity"], "item": g["message"], "action": g["action"],
                            "engagement": g.get("engagement")})
    actions.sort(key=lambda a: (SEVERITY_ORDER[a["severity"]], PILLAR_ORDER[a["pillar"]], a["item"]))

    order = {"at_risk": 0, "needs_attention": 1, "open": 2, "ready": 3}
    engagements = sorted(inside, key=lambda e: (order[e["status"]], e["score"], e["engagement"]))
    return {
        "as_of": as_of,
        "window": window,
        "firm": {
            "overall_score": round(sum(scores) / len(scores), 1) if scores else None,
            "pillars_assessed": len(scores), "pillars_total": 3, "complete": not unassessed,
            "not_assessed": unassessed,
            "note": ("Overall is the average of the pillars that have evidence. Not assessed: " + ", ".join(unassessed) + "."
                     if unassessed else "Overall is the plain average of the three pillars."),
        },
        "pillars": pillars,
        "engagements": engagements,
        "outside_window": [{k: e[k] for k in ("engagement", "client", "file_date", "score", "status", "window_note")}
                           for e in ctx["outside"]],
        "coverage": {"engagement_dimensions_assessed": list(ASSESSED), "not_assessed": NOT_ASSESSED},
        "actions": actions,
        "persisted": _persisted(cur, window, inside),
    }


def likely_sample(cur, limit: int | None = 25, selection_year: int | None = None, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    ctx = _load(cur, selection_year, as_of)
    ranked = ctx["sample"]
    return {
        "as_of": as_of, "window": ctx["window"],
        "total_in_window": len(ranked), "returned": len(ranked[:limit]) if limit else len(ranked),
        "method": {"weights": WEIGHTS, "max_score": sum(WEIGHTS.values()),
                   "order": "risk score, then fee, then engagement code",
                   "note": ("Rank, not prophecy. The same recorded facts and as-of date give the same ranking. "
                            "Whether a report was modified is not recorded per engagement, so it is not a driver yet.")},
        "excluded_from_window": len(ctx["outside"]),
        "sample": ranked[:limit] if limit else ranked,
    }


# ================================================================== persistence (eng_score)
def recompute(cur, selection_year: int | None = None) -> dict:
    """Re-evaluate every engagement and write eng_score for the window.

    later_additions is never written here: the database fills it from the
    append-only ledger. Engagements outside the window are scored in the
    scorecard response but not stored, because a stored row belongs to a window.
    """
    as_of = date.today()
    ctx = _load(cur, selection_year, as_of)
    w = ctx["window"]
    by_code = {s["engagement"]: s for s in ctx["sample"]}
    written = removed = 0
    for e in ctx["inside"]:
        keep = []
        for dim, d in e["dimensions"].items():
            keep.append(dim)
            cur.execute(
                """INSERT INTO eng_score (engagement, window_from, window_to, dimension, score, gaps)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (engagement, window_from, window_to, dimension)
                   DO UPDATE SET score = EXCLUDED.score, gaps = EXCLUDED.gaps, computed_at = now()""",
                (e["engagement"], w["from"], w["to"], dim, d["score"], Json(d["gaps"])))
            written += 1
        s = by_code[e["engagement"]]
        cur.execute(
            """INSERT INTO eng_score (engagement, window_from, window_to, dimension, score, gaps, sample_rank, rank_drivers)
               VALUES (%s,%s,%s,'overall',%s,%s,%s,%s)
               ON CONFLICT (engagement, window_from, window_to, dimension)
               DO UPDATE SET score = EXCLUDED.score, gaps = EXCLUDED.gaps, sample_rank = EXCLUDED.sample_rank,
                             rank_drivers = EXCLUDED.rank_drivers, computed_at = now()""",
            (e["engagement"], w["from"], w["to"], e["score"], Json(e["gaps"]), s["rank"],
             Json({"risk_score": s["risk_score"], "drivers": s["drivers"], "reasons": s["reasons"]})))
        written += 1
        cur.execute("DELETE FROM eng_score WHERE engagement = %s AND window_from = %s AND window_to = %s "
                    "AND dimension <> 'overall' AND NOT (dimension = ANY(%s))",
                    (e["engagement"], w["from"], w["to"], keep))
        removed += cur.rowcount
    codes = [e["engagement"] for e in ctx["inside"]]
    cur.execute("DELETE FROM eng_score WHERE window_from = %s AND window_to = %s AND NOT (engagement = ANY(%s))",
                (w["from"], w["to"], codes))
    removed += cur.rowcount
    return {"recomputed_at": datetime.now(timezone.utc), "as_of": as_of, "window": w, "engagements_scored": len(codes),
            "engagements_outside_window": len(ctx["outside"]), "rows_written": written, "rows_removed": removed,
            "later_additions_preserved": sum(e["later_addition_count"] for e in ctx["inside"])}


# ================================================================== later additions (spec section 7)
def add_later_addition(cur, engagement_id: int, description: str, reason: str,
                       dimension: str | None = None, added_by: str | None = None) -> dict:
    if dimension is not None and dimension not in LATER_ADDITION_DIMENSIONS:
        raise ValueError("dimension must be one of: " + ", ".join(LATER_ADDITION_DIMENSIONS))
    cur.execute("SELECT * FROM assurance_register WHERE id = %s", (engagement_id,))
    e = cur.fetchone()
    if e is None:
        raise NotFound(f"engagement {engagement_id} not found")
    rd = e["report_date"]
    if rd is None:
        raise Conflict(f"{e['engagement']} has no report date, so its file is not closed. "
                       "Record the work in the file itself; a later addition applies only after the report.")
    closes = rd + timedelta(days=ASSEMBLY_DAYS)
    if date.today() <= closes:
        raise Conflict(f"The assembly window for {e['engagement']} closes on {_d(closes)}. "
                       "Until then, record the work in the file itself; it is not a later addition.")
    cur.execute(
        "INSERT INTO eng_later_addition (engagement, dimension, description, reason, added_by) "
        "VALUES (%s,%s,%s,%s,%s) RETURNING *",
        (e["engagement"], dimension, description, reason, added_by))
    added = cur.fetchone()
    cur.execute("SELECT * FROM eng_later_addition WHERE engagement = %s ORDER BY id", (e["engagement"],))
    return {"added": added, "engagement": e["engagement"], "assembly_window_closed_on": closes,
            "later_additions": cur.fetchall(),
            "note": "Recorded as a later addition with today's date and your reason. It does not change the contemporaneous score."}
