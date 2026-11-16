"""Applicability engine: which ICAI peer review phase applies to the firm, and why.

Pure functions, no database access. Re-evaluated every time the profile is
created or updated. Writing the result to pr_profile fires the Stage 1 trigger,
which records the change in pr_applicability_history.

Rule for the single stored answer: every phase whose trigger is met is
reported in `phases_applying`; `phase` is the HIGHEST-numbered one, i.e. the
phase that reaches furthest down the profession (spec: "Phase IV applies --
four partners since last March"). Basis strings are ASCII and deterministic so
the same inputs never produce a spurious history row.
"""
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class PhaseDef:
    number: int
    label: str
    effective: date
    flag_field: str
    flag_text: str
    partner_threshold: int | None  # attestation-unit partner ladder; None = no ladder


PHASES: tuple[PhaseDef, ...] = (
    PhaseDef(1, "I", date(2022, 4, 1), "audits_listed_entities",
             "Statutory audit of listed entities (SEBI LODR 2015)", None),
    PhaseDef(2, "II", date(2023, 4, 1), "audits_large_unlisted_public",
             "Statutory audit of large unlisted public companies", 5),
    PhaseDef(3, "III", date(2025, 7, 1), "audits_entities_raised_50cr",
             "Statutory audit of entities that raised over Rs 50 cr from the public, banks or FIs", 4),
    PhaseDef(4, "IV", date(2026, 12, 31), "audits_psb_branches",
             "Audit of branches of Public Sector Banks", 3),
)

LOWEST_LADDER_THRESHOLD = min(p.partner_threshold for p in PHASES if p.partner_threshold)


def _partners(n: int) -> str:
    return f"{n} partner" if n == 1 else f"{n} partners"


def _ladder_text(partners: int, threshold: int) -> str:
    return (f"Practice unit rendering attestation services with {_partners(partners)} "
            f"(applies from {threshold} partners)")


def evaluate(profile: dict, as_of: date | None = None) -> dict:
    """profile needs: partner_count, renders_attestation and the four audits_* flags."""
    as_of = as_of or date.today()
    partners = int(profile.get("partner_count") or 0)
    attestation = bool(profile.get("renders_attestation"))

    applying = []
    for p in PHASES:
        bases = []
        if profile.get(p.flag_field):
            bases.append(p.flag_text)
        if attestation and p.partner_threshold and partners >= p.partner_threshold:
            bases.append(_ladder_text(partners, p.partner_threshold))
        if bases:
            applying.append({
                "phase": p.number,
                "label": p.label,
                "effective": p.effective,
                "in_force": as_of >= p.effective,
                "bases": bases,
            })

    if not applying:
        if attestation:
            # Deliberately independent of the exact partner count: admitting a 1st or
            # 2nd partner must not write a "0 -> 0" history row.
            detail = (f"below the earliest partner trigger of "
                      f"{LOWEST_LADDER_THRESHOLD} partners (Phase IV) and no statutory trigger met")
        else:
            detail = "no attestation services rendered and no statutory trigger met"
        return {
            "phase": 0,
            "label": "None",
            "basis": f"No phase applies: {detail}",
            "effective": None,
            "in_force": False,
            "phases_applying": [],
        }

    top = applying[-1]  # PHASES is ordered I..IV, so last = highest
    return {
        "phase": top["phase"],
        "label": top["label"],
        "basis": f"Phase {top['label']}: " + "; ".join(top["bases"]),
        "effective": top["effective"],
        "in_force": top["in_force"],
        "phases_applying": applying,
    }
