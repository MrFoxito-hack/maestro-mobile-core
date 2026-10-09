import json
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import operator_user
from app.models import UserPublic
from app.services import nwdaf

router = APIRouter(prefix="/nwdaf", tags=["nwdaf"])


@router.get("/closed-loop/history")
def closed_loop_history(response: Response, _: UserPublic = Depends(operator_user)):
    response.headers["Cache-Control"] = "no-store"
    return nwdaf.nwdaf_request("/management/v1/closed-loop/history")


@router.get("/predictions")
def predictions(response: Response, _: UserPublic = Depends(operator_user)):
    response.headers["Cache-Control"] = "no-store"
    result = nwdaf.nwdaf_request("/management/v1/predictions")
    slices = nwdaf.slice_catalog()
    return {**result, 'slices': slices, 'items': [p for p in result.get('items', [])
            if any(p.get('snssai') == s['snssai'] for s in slices)]}


@router.get("/status")
def status(response: Response, _: UserPublic = Depends(operator_user)):
    """NWDAF service health and ingestion status."""
    response.headers["Cache-Control"] = "no-store"
    try:
        health = nwdaf.get_health()
        return {
            "connected": health.get("status") == "ready",
            "contract": health.get("contract"),
            "profile": health.get("profile"),
            "closed_loop_enabled": health.get("closed_loop_enabled", False),
            "ingestion": health.get("ingestion", {}),
        }
    except Exception:
        return {"connected": False, "contract": None, "profile": None,
                "closed_loop_enabled": False, "ingestion": {}}


@router.get("/overview")
def overview(response: Response, _: UserPublic = Depends(operator_user)):
    """Aggregated NWDAF overview for the dashboard."""
    response.headers["Cache-Control"] = "no-store"
    return nwdaf.get_overview()


@router.get("/analytics/{event_id}")
def analytics(event_id: Literal[
    "LOAD_LEVEL_INFORMATION", "ABNORMAL_BEHAVIOUR", "SERVICE_EXPERIENCE"
], response: Response,
    snssai_sst: int | None = Query(default=None, ge=0, le=255),
    snssai_sd: str | None = Query(default=None, pattern=r"^[A-Fa-f0-9]{6}$"),
    supi: str | None = Query(default=None, pattern=r"^imsi-[0-9]{5,15}$"),
    app_id: str | None = Query(default=None, min_length=1, max_length=100),
    _: UserPublic = Depends(operator_user)):
    """Query a specific NWDAF Analytics ID via the SBI interface."""
    response.headers["Cache-Control"] = "no-store"

    event_filter = None
    tgt_ue = None

    if event_id == "LOAD_LEVEL_INFORMATION":
        snssai = {"sst": snssai_sst or 1}
        if snssai_sd:
            snssai["sd"] = snssai_sd.upper()
        event_filter = json.dumps({"snssais": [snssai]})
    elif event_id == "ABNORMAL_BEHAVIOUR":
        if supi:
            tgt_ue = json.dumps({"supis": [supi]})
    elif event_id == "SERVICE_EXPERIENCE":
        if supi and app_id:
            tgt_ue = json.dumps({"supis": [supi]})
            event_filter = json.dumps({"appIds": [app_id]})

    result = nwdaf.get_analytics(event_id, event_filter=event_filter, tgt_ue=tgt_ue)
    if result is None:
        result = {"status": "no_data", "message": "No hay analíticas recientes disponibles"}
    if event_id == 'SERVICE_EXPERIENCE' and supi and app_id:
        from app.services.terminal_experience import latest
        result = {**result, 'player_observation': latest(supi, app_id)}
    return result
