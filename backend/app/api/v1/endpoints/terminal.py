from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictBool
from uuid import UUID
from typing import Literal
from pydantic import Field
from app.api.deps import terminal_user
from app.models import UserPublic
from app.db import add_audit
from app.services import terminal, terminal_access
from app.services import terminal_media
from app.services import terminal_sessions
from app.services import terminal_experience

router = APIRouter(prefix='/terminal', tags=['terminal'])

def resolve_imsi_for_user(user: UserPublic, requested_imsi: str | None) -> str:
    return terminal_access.authorize_device(requested_imsi, user)['supi']


@router.post('/media/experience')
def playback_experience(payload: terminal_experience.PlaybackObservation,
                        user: UserPublic = Depends(terminal_user)):
    resolve_imsi_for_user(user, payload.imsi)
    return terminal_experience.record(payload)


@router.get('/media/{profile}/{asset}')
async def media(profile: str, asset: str, imsi: str | None = None, user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, imsi)
    try:
        payload = await terminal_media.fetch(profile, asset, target_imsi)
    except Exception:
        add_audit(user.username, user.role, user.testbed, 'terminal.media',
                  {'imsi': target_imsi, 'profile': profile[:16], 'asset': asset[:32]}, 'failed')
        raise
    add_audit(user.username, user.role, user.testbed, 'terminal.media',
              {'imsi': target_imsi, 'profile': profile, 'asset': asset, 'http_bytes': len(payload)}, 'success')
    return Response(payload, media_type='application/vnd.apple.mpegurl' if asset.endswith('.m3u8') else 'video/mp4',
                    headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                             'X-MAEstro-Path': 'DN-UPF-UE-SSH-relay'})


class AirplaneMode(BaseModel):
    enabled: StrictBool
    imsi: str = Field(pattern=r'^(?:imsi-)?\d{14,15}$')


@router.get('/status')
async def status(response: Response, imsi: str | None = None, user: UserPublic = Depends(terminal_user)):
    response.headers['Cache-Control'] = 'no-store'
    target_imsi = resolve_imsi_for_user(user, imsi)
    return await terminal.snapshot(target_imsi, user=user)


class ApnSelection(BaseModel):
    apn: Literal['internet', 'corporate', '5g-plus']
    imsi: str = Field(pattern=r'^(?:imsi-)?\d{14,15}$')


@router.get('/profiles')
def profiles(_: UserPublic = Depends(terminal_user)):
    from app.core.config import get_settings
    from app.services.upf_inventory import inventory
    active = get_settings().multi_upf_enabled
    return {'migration_complete': active, 'profiles': [
        {'id': t['service'], 'label': t['label'], 'dnn': t['dnn'],
         'target_snssai': {'sst':t['sst'], 'sd':t['sd']},
         'enabled': active or t['service'] != 'urllc'} for t in inventory()['targets']]}


@router.post('/apn')
async def select_apn(payload: ApnSelection, user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, payload.imsi)
    return await audited('apn', user, terminal_sessions.select(payload.apn, target_imsi), imsi=target_imsi)


@router.get('/corporate/intranet')
async def intranet(imsi: str | None = None, user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, imsi)
    result = await audited('intranet', user, terminal_sessions.intranet(target_imsi), imsi=target_imsi)
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


async def audited(action, user, call, *, imsi=None):
    try:
        result = await call
    except Exception:
        add_audit(user.username, user.role, user.testbed, 'terminal.' + action, {'imsi': imsi}, 'failed')
        raise
    details = {}
    if action == 'apn':
        details = {'apn': result['active_apn'], 'interface': result['session']['interface']}
    elif action == 'intranet':
        details = {'apn': 'corporate', 'interface': result['interface']}
    elif action == 'af-boost':
        details = {'enabled': result.get('active'), 'qos': result.get('qos'), 'pcc_rule': result.get('pcc_rule')}
    outcome = 'success'
    if action == 'n6-probe':
        details = {key: result.get(key) for key in
                   ('completed', 'received_bytes', 'http_status', 'exit_code', 'duration_seconds')}
        if not result.get('completed'):
            outcome = 'failed'
    add_audit(user.username, user.role, user.testbed, 'terminal.' + action, {**details, 'imsi': imsi}, outcome)
    return result


class AfBoostControl(BaseModel):
    enabled: StrictBool
    imsi: str = Field(pattern=r'^(?:imsi-)?\d{14,15}$')


@router.post('/af-boost')
async def af_boost(payload: AfBoostControl, user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, payload.imsi)
    return await audited('af-boost', user, terminal.af_boost(payload.enabled, imsi=target_imsi), imsi=target_imsi)


@router.post('/airplane-mode')
async def airplane(payload: AirplaneMode, user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, payload.imsi)
    call = terminal.airplane(payload.enabled, target_imsi)
    return await audited('airplane-on' if payload.enabled else 'airplane-off', user, call, imsi=target_imsi)


class TrafficControl(BaseModel):
    imsi: str = Field(pattern=r'^(?:imsi-)?\d{14,15}$')


@router.post('/traffic/start')
async def start(payload: TrafficControl, user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, payload.imsi)
    return await audited('traffic-start', user, terminal.traffic(True, imsi=target_imsi), imsi=target_imsi)


@router.post('/traffic/stop')
async def stop(payload: TrafficControl, user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, payload.imsi)
    return await audited('traffic-stop', user, terminal.traffic(False, imsi=target_imsi), imsi=target_imsi)


@router.post('/traffic/n6-probe')
async def n6_probe(payload: TrafficControl, user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, payload.imsi)
    return await audited('n6-probe', user, terminal.n6_probe(imsi=target_imsi), imsi=target_imsi)


class Topup(BaseModel):
    request_id: UUID
    imsi: str = Field(pattern=r'^(?:imsi-)?\d{14,15}$')


@router.post('/topup')
async def topup(payload: Topup, user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, payload.imsi)
    return await audited('topup', user, terminal.topup(str(payload.request_id), imsi=target_imsi), imsi=target_imsi)


class SpeedtestRequest(BaseModel):
    imsi: str = Field(pattern=r'^(?:imsi-)?\d{14,15}$')


@router.post('/speedtest')
async def speedtest(payload: SpeedtestRequest, user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, payload.imsi)
    return await audited('speedtest', user, terminal.run_speedtest(imsi=target_imsi), imsi=target_imsi)
