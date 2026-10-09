"""Bounded experimental UPF forwarding mode and real BPF counters."""
import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from app.api.deps import operator_user
from app.core.config import get_settings
from app.db import add_audit
from app.models import UserPublic
from app.services.execution import ExecutionError, RemoteExecutionAdapter
from app.services import urllc_xdp

router = APIRouter(prefix='/upf-xdp', tags=['performance'])
AGENT = '/home/emsadmin/upf-xdp/upf_xdp_agent.py'


def request(action: str):
    if get_settings().execution_mode != 'remote':
        raise HTTPException(409, 'XDP requiere el UPF remoto; no se simulan contadores')
    adapter = RemoteExecutionAdapter({'open5gs-upfd'})
    try:
        output = adapter._execute_sync(adapter._sudo_cmd(f'/usr/bin/python3 {AGENT} {action}'),
                                       port=adapter.settings.upf_ssh_port, retries=1)
        return json.loads(output) if action == 'status' else None
    except (ExecutionError, ValueError) as exc:
        raise HTTPException(503, 'No se pudo consultar o cambiar XDP en el UPF') from exc


class Mode(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['legacy', 'kernel', 'xdp']
    slice: Literal['urllc'] | None = None


@router.get('/status')
async def status(response: Response, slice: Literal['urllc'] | None = None,
                 _: UserPublic = Depends(operator_user)):
    response.headers['Cache-Control'] = 'no-store'
    if slice == 'urllc' or get_settings().multi_upf_enabled:
        return await urllc_xdp.status()
    return await asyncio.to_thread(request, 'status')


@router.post('/mode')
async def mode(body: Mode, user: UserPublic = Depends(operator_user)):
    if body.slice == 'urllc' or (get_settings().multi_upf_enabled and body.mode != 'xdp'):
        try:
            result = await urllc_xdp.switch(body.mode)
        except HTTPException as exc:
            add_audit(user.username, user.role, user.testbed, 'upf-xdp.mode',
                      {'mode': body.mode, 'slice': 'urllc', 'status_code': exc.status_code}, 'failed')
            raise
        add_audit(user.username, user.role, user.testbed, 'upf-xdp.mode',
                  {'mode': body.mode, 'slice': 'urllc', 'namespace': 'maestro-urllc'}, 'success')
        return result
    if body.mode == 'xdp':
        if get_settings().multi_upf_enabled:
            raise HTTPException(409, 'Indicar slice=urllc; eMBB y MIoT no admiten este acelerador')
        current = request('status')
        if not current.get('lease_seconds'):
            raise HTTPException(409, 'Registrar primero la sesión activa del UE en el UPF')
        adapter = RemoteExecutionAdapter({'ueransim-ue'})
        import yaml
        try:
            observed = yaml.safe_load(adapter._execute_sync(
                '/home/emsadmin/UERANSIM/build/nr-cli imsi-999700000000001 --exec ps-list',
                port=adapter.settings.ue_ssh_port, retries=1)) or {}
        except Exception as exc:
            raise HTTPException(409, 'No se pudo verificar la sesión actual del UE') from exc
        if not any(isinstance(p, dict) and p.get('state') == 'PS-ACTIVE' and
                   p.get('apn') == 'internet' and p.get('address') == current.get('ue')
                   for p in observed.values()):
            raise HTTPException(409, 'La sesión cambió; actualizar los mapas antes de activar XDP')
    request('on' if body.mode == 'xdp' else 'off')
    add_audit(user.username, user.role, user.testbed, 'upf-xdp.mode', {'mode': body.mode}, 'success')
    return request('status')
