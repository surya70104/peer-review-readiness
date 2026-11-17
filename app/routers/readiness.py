"""Readiness scorecard and likely-sample APIs (Screens 2 and 3 read these)."""
from datetime import date

from fastapi import APIRouter, Query
from pydantic import ConfigDict, Field

from app.routers.registers import _Base, _run
from app.services import readiness_service as svc

router = APIRouter(prefix="/api/readiness", tags=["readiness"])

SelectionYear = Query(None, ge=2020, le=2100, description="Year the practice unit is selected; defaults without manual input.")


class LaterAdditionCreate(_Base):
    # No timestamp is accepted: the database stamps the time, so it cannot be back-dated.
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    description: str = Field(min_length=1, description="What was added to the file.")
    reason: str = Field(min_length=1, description="Why it was added after the assembly window closed.")
    dimension: str | None = None
    added_by: str | None = None


@router.post("/recompute")
def recompute(selection_year: int | None = SelectionYear):
    """Re-evaluate every engagement and update eng_score. Later additions are untouched."""
    return _run(svc.recompute, selection_year)


@router.get("/scorecard")
def scorecard(selection_year: int | None = SelectionYear,
              as_of: date | None = Query(None, description="Evaluate as at this date (read-only).")):
    return _run(svc.scorecard, selection_year, as_of)


@router.get("/likely-sample")
def likely_sample(limit: int | None = Query(25, ge=1, le=1000, description="Top N engagements."),
                  selection_year: int | None = SelectionYear,
                  as_of: date | None = Query(None, description="Evaluate as at this date (read-only).")):
    return _run(svc.likely_sample, limit, selection_year, as_of)


@router.post("/engagements/{engagement_id}/later-addition", status_code=201)
def add_later_addition(engagement_id: int, body: LaterAdditionCreate):
    """Append a documented later addition to a closed file. It cannot be edited, deleted or back-dated."""
    return _run(svc.add_later_addition, engagement_id, body.description, body.reason, body.dimension, body.added_by)
