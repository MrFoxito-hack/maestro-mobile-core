from fastapi import APIRouter, Depends, Response
from app.api.deps import operator_user
from app.models import UserPublic
from app.services.upf_pm import upf_collector

router = APIRouter(prefix='/upf-telemetry', tags=['performance'])


@router.get('/snapshot')
def snapshot(response: Response, _: UserPublic = Depends(operator_user)):
    response.headers['Cache-Control'] = 'no-store'
    return upf_collector.snapshot()
