from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.deps import current_user, operator_user
from app.db import add_audit
from app.models import SubscriberCreate, SubscriberUpdate, UserPublic
from app.services.subscribers import subscriber_service
from app.services.ue_observation import unknown_status

router = APIRouter(prefix="/subscribers", tags=["subscribers"])


@router.get("")
def list_subscribers(_: UserPublic = Depends(current_user)):
    return subscriber_service.list()


@router.get("/{imsi}/status")
def subscriber_status(imsi: str, _: UserPublic = Depends(current_user)):
    try:
        statuses = subscriber_service.get_live_status(imsi)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    status = statuses.get(imsi)
    if not status:
        return unknown_status()
    return status


@router.post("", status_code=201)
def create(payload: SubscriberCreate, user: UserPublic = Depends(operator_user)):
    try:
        result = subscriber_service.create(payload)
        add_audit(user.username, user.role, user.testbed, "subscriber.create", {"imsi": payload.imsi}, "success")
        return result
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.put("/{imsi}")
def update(imsi: str, payload: SubscriberUpdate, user: UserPublic = Depends(operator_user)):
    try:
        result = subscriber_service.update(imsi, payload)
        add_audit(user.username, user.role, user.testbed, "subscriber.update", {"imsi": imsi}, "success")
        return result
    except KeyError:
        raise HTTPException(404, "Suscriptor no encontrado")
    except Exception as exc:
        raise HTTPException(500, f"Error actualizando suscriptor: {exc}")


@router.delete("/{imsi}", status_code=204)
def delete(imsi: str, user: UserPublic = Depends(operator_user)):
    if not subscriber_service.delete(imsi):
        raise HTTPException(404, "Suscriptor no encontrado")
    add_audit(user.username, user.role, user.testbed, "subscriber.delete", {"imsi": imsi}, "success")
    return Response(status_code=204)
