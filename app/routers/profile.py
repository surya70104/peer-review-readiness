from datetime import date
from typing import Literal

import psycopg2.errors
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app import db
from app.services import profile_service as svc

router = APIRouter(prefix="/api/profile", tags=["profile"])


class ProfileIn(BaseModel):
    """Create or update. On update, send only the fields that change."""
    firm: str | None = Field(None, min_length=1)
    level: Literal[1, 2] | None = None
    partner_count: int | None = Field(None, ge=0)
    audits_listed_entities: bool | None = None
    audits_large_unlisted_public: bool | None = None
    audits_entities_raised_50cr: bool | None = None
    audits_psb_branches: bool | None = None
    renders_attestation: bool | None = None
    certificate_no: str | None = None
    effective_from: date | None = None
    expires_on: date | None = None  # read from the certificate, never derived
    last_report_type: Literal["clean", "modified"] | None = None
    last_report_date: date | None = None
    follow_on_months: int | None = Field(None, ge=6, le=12)  # modified reports only
    effective_on: date | None = None  # date the change took effect (recorded in history)


class SimulatePartnerChange(BaseModel):
    effective_on: date
    partners_added: int = 1  # negative = partner leaves
    partner_count: int | None = Field(None, ge=0)  # absolute count; overrides partners_added
    apply: bool = False  # False = dry run (real trigger runs, then rolled back)


def _today_check(effective_on: date | None):
    if effective_on and effective_on > date.today():
        raise HTTPException(422, "effective_on cannot be in the future when the change is persisted")


def _save(cur, body: ProfileIn):
    data = body.model_dump(exclude_unset=True)
    follow_on_months = data.pop("follow_on_months", None)
    effective_on = data.pop("effective_on", None)
    try:
        svc.save_profile(cur, data, follow_on_months=follow_on_months, effective_on=effective_on)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except psycopg2.errors.CheckViolation as exc:
        raise HTTPException(422, f"constraint violated: {exc.diag.constraint_name}")


@router.get("")
def get_profile(as_of: date | None = Query(None, description="Evaluate the clock as of this date (default today)")):
    with db.get_conn() as conn, svc.dict_cursor(conn) as cur:
        profile = svc.get_profile(cur)
        if profile is None:
            raise HTTPException(404, "No profile yet. POST /api/profile with at least a firm name.")
        return svc.build_view(cur, profile, as_of)


@router.post("")
def upsert_profile(body: ProfileIn, as_of: date | None = Query(None)):
    _today_check(body.effective_on)
    with db.get_conn() as conn, svc.dict_cursor(conn) as cur:
        _save(cur, body)
        return svc.build_view(cur, svc.get_profile(cur), as_of)


@router.post("/simulate-partner-change")
def simulate_partner_change(body: SimulatePartnerChange, as_of: date | None = Query(None)):
    """Admit (or remove) partners on a given date and show the phase flip and the
    history rows the trigger writes. Dry run unless apply=true."""
    if body.apply:
        _today_check(body.effective_on)
    with db.get_conn(commit=body.apply) as conn, svc.dict_cursor(conn) as cur:
        before = svc.get_profile(cur)
        if before is None:
            raise HTTPException(404, "No profile yet. POST /api/profile first.")
        new_count = body.partner_count if body.partner_count is not None \
            else before["partner_count"] + body.partners_added
        if new_count < 0:
            raise HTTPException(422, "partner count cannot go below 0")
        _save(cur, ProfileIn(partner_count=new_count, effective_on=body.effective_on))
        after = svc.get_profile(cur)
        view = svc.build_view(cur, after, as_of)
        view["simulation"] = {
            "applied": body.apply,
            "effective_on": body.effective_on,
            "partner_count": {"before": before["partner_count"], "after": new_count},
            "phase": {"before": before["phase_applicable"], "after": after["phase_applicable"]},
            "phase_changed": before["phase_applicable"] != after["phase_applicable"],
            "basis": {"before": before["phase_basis"], "after": after["phase_basis"]},
        }
        return view
