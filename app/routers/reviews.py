"""Review tracker (Screen 5) and the evidence pack export."""
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import ConfigDict, Field

from app import db
from app.routers.registers import _Base, _run
from app.services import export_service, registers_service
from app.services import review_service as svc

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


class _Strict(_Base):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


# ------------------------------------------------------------------ cycles
class CycleCreate(_Strict):
    selection_year: int = Field(ge=2020, le=2100)
    reviewer: str | None = None
    period_from: date | None = None          # defaults to the three years before the selection year
    period_to: date | None = None
    intimation_at: date | None = None        # date the Board's selection letter arrived
    consent_at: date | None = None           # starts the questionnaire clock
    questionnaire_due: date | None = None    # computed as consent + 15 days unless given
    questionnaire_sent: date | None = None
    visit_at: date | None = None
    findings_at: date | None = None          # starts the response clock
    response_due: date | None = None         # computed as findings + 15 days unless given
    response_sent: date | None = None
    preliminary_report_at: date | None = None


class CycleUpdate(_Strict):
    reviewer: str | None = None
    period_from: date | None = None
    period_to: date | None = None
    intimation_at: date | None = None
    consent_at: date | None = None
    questionnaire_due: date | None = None
    questionnaire_sent: date | None = None
    visit_at: date | None = None
    findings_at: date | None = None
    response_due: date | None = None
    response_sent: date | None = None
    preliminary_report_at: date | None = None
    final_report_at: date | None = None
    outcome: Literal["clean", "modified"] | None = None
    follow_on_months: int | None = Field(None, ge=6, le=12)   # only when closing with a modified report


@router.post("/cycles", status_code=201)
def create_cycle(body: CycleCreate):
    return _run(svc.create_cycle, body.model_dump(exclude_unset=True))


@router.get("/cycles")
def list_cycles(as_of: date | None = Query(None, description="Evaluate the clocks as at this date (read-only).")):
    return _run(svc.list_cycles, as_of)


@router.patch("/cycles/{cycle_id}")
def update_cycle(cycle_id: int, body: CycleUpdate):
    return _run(svc.update_cycle, cycle_id, body.model_dump(exclude_unset=True))


# ---------------------------------------------------------------- findings
class FindingCreate(_Strict):
    control_area: str = Field(min_length=1)
    description: str = Field(min_length=1)
    ref: str | None = None                  # generated (F-001, F-002, ...) when not given
    engagement: str | None = None
    severity: Literal["high", "medium", "low"] | None = None
    due_at: date | None = None              # defaults to the cycle's response due date
    owner: str | None = None


class FindingUpdate(_Strict):
    control_area: str | None = Field(None, min_length=1)
    description: str | None = Field(None, min_length=1)
    engagement: str | None = None
    severity: Literal["high", "medium", "low"] | None = None
    due_at: date | None = None
    owner: str | None = None
    pu_response: str | None = None          # the firm's written response
    responded_at: date | None = None        # defaults to today when a response is first recorded
    corrective_action: str | None = None
    resolution: str | None = None
    evidence_ref: str | None = None
    resolved: bool | None = None            # true marks it resolved; needs response, resolution, corrective action
    resolved_on: date | None = None


@router.post("/cycles/{cycle_id}/findings", status_code=201)
def create_finding(cycle_id: int, body: FindingCreate):
    return _run(svc.create_finding, cycle_id, body.model_dump(exclude_unset=True))


@router.patch("/findings/{finding_id}")
def update_finding(finding_id: int, body: FindingUpdate):
    return _run(svc.update_finding, finding_id, body.model_dump(exclude_unset=True))


# ----------------------------------------------------------- evidence pack
@router.get("/evidence-pack")
def evidence_pack():
    """The whole dossier as one ZIP. Read-only: it records nothing."""
    now = datetime.now(timezone.utc)
    # Everything is read first, so no database connection is held while the file streams.
    with db.get_conn(commit=False) as conn, registers_service.dict_cursor(conn) as cur:
        files, _info = export_service.build_pack(cur, now)
    return StreamingResponse(
        export_service.stream_zip(files, now),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{export_service.pack_filename(now)}"',
                 "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
