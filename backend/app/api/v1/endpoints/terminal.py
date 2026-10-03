from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictBool
from uuid import UUID
from typing import Literal
from pydantic import Field
from app.api.deps import terminal_user
from app.models import Role, UserPublic
from app.db import add_audit
from app.services import terminal
from app.services import terminal_media
from app.services import terminal_sessions
from app.services import terminal_experience

router = APIRouter(prefix='/terminal', tags=['terminal'])

OFFICIAL_LAB_IMSIS = {
    'imsi-999700000000001',
    'imsi-999700000000002',
    'imsi-999700000000003',
    'imsi-999700000000004',
    'imsi-999700000000005',
}


def resolve_imsi_for_user(user: UserPublic, requested_imsi: str | None) -> str | None:
    role_str = user.role.value if hasattr(user.role, 'value') else str(user.role)
    if role_str == 'student':
        assigned = user.assigned_imsi or 'imsi-999700000000001'
        if requested_imsi and requested_imsi != assigned:
            raise HTTPException(403, 'No autorizado para consultar u operar este terminal UE')
        return assigned
    return requested_imsi


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
                  {'profile': profile[:16], 'asset': asset[:32]}, 'failed')
        raise
    add_audit(user.username, user.role, user.testbed, 'terminal.media',
              {'profile': profile, 'asset': asset, 'http_bytes': len(payload)}, 'success')
    return Response(payload, media_type='application/vnd.apple.mpegurl' if asset.endswith('.m3u8') else 'video/mp4',
                    headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                             'X-MAEstro-Path': 'DN-UPF-UE-SSH-relay'})


class AirplaneMode(BaseModel):
    enabled: StrictBool
    imsi: str | None = Field(default=None, pattern=r'^(?:imsi-)?\d{14,15}$')


@router.get('/status')
async def status(imsi: str | None = None, user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, imsi)
    return await terminal.snapshot(target_imsi, user=user)


class ApnSelection(BaseModel):
    apn: Literal['internet', 'corporate']
    imsi: str | None = Field(default=None, pattern=r'^(?:imsi-)?\d{14,15}$')


@router.post('/apn')
async def select_apn(payload: ApnSelection, user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, payload.imsi)
    return await audited('apn', user, terminal_sessions.select(payload.apn, target_imsi))


@router.get('/corporate/intranet')
async def intranet(imsi: str | None = None, user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, imsi)
    result = await audited('intranet', user, terminal_sessions.intranet(target_imsi))
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


async def audited(action, user, call):
    try:
        result = await call
    except Exception:
        add_audit(user.username, user.role, user.testbed, 'terminal.' + action, {}, 'failed')
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
    add_audit(user.username, user.role, user.testbed, 'terminal.' + action, details, outcome)
    return result


class AfBoostControl(BaseModel):
    enabled: StrictBool
    imsi: str | None = Field(default=None, pattern=r'^(?:imsi-)?\d{14,15}$')


@router.post('/af-boost')
async def af_boost(payload: AfBoostControl, user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, payload.imsi)
    return await audited('af-boost', user, terminal.af_boost(payload.enabled, imsi=target_imsi))


@router.post('/airplane-mode')
async def airplane(payload: AirplaneMode, user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, payload.imsi)
    call = terminal.airplane(payload.enabled, target_imsi) if target_imsi else terminal.airplane(payload.enabled)
    return await audited('airplane-on' if payload.enabled else 'airplane-off', user, call)


class TrafficControl(BaseModel):
    imsi: str | None = Field(default=None, pattern=r'^(?:imsi-)?\d{14,15}$')


@router.post('/traffic/start')
async def start(payload: TrafficControl = TrafficControl(), user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, payload.imsi)
    return await audited('traffic-start', user, terminal.traffic(True, imsi=target_imsi))


@router.post('/traffic/stop')
async def stop(payload: TrafficControl = TrafficControl(), user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, payload.imsi)
    return await audited('traffic-stop', user, terminal.traffic(False, imsi=target_imsi))


@router.post('/traffic/n6-probe')
async def n6_probe(user: UserPublic = Depends(terminal_user)):
    return await audited('n6-probe', user, terminal.n6_probe())


class Topup(BaseModel):
    request_id: UUID
    imsi: str | None = Field(default=None, pattern=r'^(?:imsi-)?\d{14,15}$')


@router.post('/topup')
async def topup(payload: Topup, user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, payload.imsi)
    return await audited('topup', user, terminal.topup(str(payload.request_id), imsi=target_imsi))


class SpeedtestRequest(BaseModel):
    imsi: str | None = Field(default=None, pattern=r'^(?:imsi-)?\d{14,15}$')


@router.post('/speedtest')
async def speedtest(payload: SpeedtestRequest = SpeedtestRequest(), user: UserPublic = Depends(terminal_user)):
    target_imsi = resolve_imsi_for_user(user, payload.imsi)
    return await audited('speedtest', user, terminal.run_speedtest(imsi=target_imsi))
