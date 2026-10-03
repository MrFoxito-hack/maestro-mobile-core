from typing import Literal

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import operator_user
from app.models import UserPublic
from app.services import charging

router = APIRouter(prefix="/charging", tags=["charging"])


@router.get("/status")
def status(response: Response, _: UserPublic = Depends(operator_user)):
    response.headers["Cache-Control"] = "no-store"
    health = charging.management_get("/health")
    ready = charging.management_get("/ready")
    return {"connected": health.get("interface") == "management" and ready.get("status") == "ready",
            "profile": health.get("profile"), "read_only": True,
            "validation": "experimental", "source": "CHF management API"}


@router.get("/{kind}")
def records(kind: Literal["accounts", "sessions", "cdrs", "ledger"], response: Response,
            supi: str | None = Query(default=None, pattern=r"^imsi-[0-9]{5,15}$"),
            limit: int = Query(default=25, ge=1, le=100),
            offset: int = Query(default=0, ge=0, le=1000000),
            _: UserPublic = Depends(operator_user)):
    response.headers["Cache-Control"] = "no-store"
    return charging.records(kind, supi, limit, offset)
