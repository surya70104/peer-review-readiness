from datetime import date, datetime
from decimal import Decimal
from typing import Callable

import psycopg2.errors
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import db
from app.services import registers_service as svc

router = APIRouter(prefix="/api/registers", tags=["registers"])


class _Base(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        # Blank optional text is "not recorded", never an empty string.
        return None if isinstance(v, str) and v.strip() == "" else v


def _run(fn: Callable, *args, **kwargs):
    """Run a service call in a transaction and map errors to HTTP statuses."""
    try:
        with db.get_conn() as conn, svc.dict_cursor(conn) as cur:
            return fn(cur, *args, **kwargs)
    except svc.NotFound as exc:
        raise HTTPException(404, str(exc))
    except svc.Conflict as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except psycopg2.errors.CheckViolation as exc:
        raise HTTPException(422, f"constraint violated: {exc.diag.constraint_name}")


# ---------------------------------------------------------------- assurance
class AssuranceCreate(_Base):
    engagement: str = Field(min_length=1)
    client: str = Field(min_length=1)
    service: str = Field(min_length=1)
    partner: str = Field(min_length=1)
    period_from: date | None = None
    period_to: date | None = None
    fee: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    report_date: date | None = None
    udin: str | None = None


class AssuranceUpdate(_Base):
    engagement: str | None = Field(None, min_length=1)
    client: str | None = Field(None, min_length=1)
    service: str | None = Field(None, min_length=1)
    partner: str | None = Field(None, min_length=1)
    period_from: date | None = None
    period_to: date | None = None
    fee: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    report_date: date | None = None
    udin: str | None = None


@router.post("/assurance", status_code=201)
def create_assurance(body: AssuranceCreate):
    return _run(svc.create_assurance, body.model_dump())


@router.get("/assurance")
def list_assurance(partner: str | None = Query(None), service: str | None = Query(None)):
    return _run(svc.list_assurance, partner, service)


@router.patch("/assurance/{engagement_id}")
def update_assurance(engagement_id: int, body: AssuranceUpdate):
    return _run(svc.update_assurance, engagement_id, body.model_dump(exclude_unset=True))


# ---------------------------------------------------------------------- sqc
class SqcCreate(_Base):
    area: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    effective_from: date
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None  # defaults to now when acknowledged_by is given
    evidence_of_operation: str | None = None


class SqcUpdate(_Base):
    area: str | None = Field(None, min_length=1)
    policy_version: str | None = Field(None, min_length=1)
    effective_from: date | None = None
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    evidence_of_operation: str | None = None


@router.post("/sqc", status_code=201)
def create_sqc(body: SqcCreate):
    return _run(svc.create_sqc, body.model_dump())


@router.get("/sqc")
def list_sqc(area: str | None = Query(None)):
    return _run(svc.list_sqc, area)


@router.patch("/sqc/{policy_id}")
def update_sqc(policy_id: int, body: SqcUpdate):
    return _run(svc.update_sqc, policy_id, body.model_dump(exclude_unset=True))
