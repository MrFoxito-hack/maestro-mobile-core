from typing import Literal
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, ConfigDict
from app.api.deps import terminal_user
from app.api.v1.endpoints.terminal import resolve_imsi_for_user, audited
from app.models import UserPublic
from app.services import terminal_devices as devices

router = APIRouter(prefix='/terminal/devices', tags=['terminal'])
vertical_router = APIRouter(prefix='/terminal-devices', tags=['terminal'])


class Target(BaseModel):
    model_config = ConfigDict(extra='forbid')
    imsi: str = Field(pattern=r'^(?:imsi-)?\d{14,15}$')


class FleetCycle(Target):
    model_config = ConfigDict(extra='forbid')
    sensors: int = Field(default=100, ge=1, le=1000, strict=True)


@vertical_router.post('/industrial/telemetry')
async def telemetry(body: FleetCycle, user: UserPublic = Depends(terminal_user)):
    imsi = authorize('sensor', user, body.imsi)
    return await audited('industrial-telemetry', user,
                         devices.measure('sensor', 'burst', body.sensors, 200, fleet_size=body.sensors, imsi=imsi), imsi=imsi)


class Burst(Target):
    model_config = ConfigDict(extra='forbid')
    packets: int = Field(default=1000, ge=1, le=1000, strict=True)
    pps: int = Field(default=200, ge=1, le=200, strict=True)


def authorize(name, user, imsi):
    target = resolve_imsi_for_user(user, imsi)
    devices.device(name, target)
    return target


@router.get('/{name}')
async def status(name: Literal['vehicle', 'sensor'], response: Response, imsi: str | None = None, user: UserPublic = Depends(terminal_user)):
    response.headers['Cache-Control'] = 'no-store'
    target = authorize(name, user, imsi)
    return await devices.status(name, imsi=target)


@router.post('/vehicle/probe')
async def probe(body: Target, user: UserPublic = Depends(terminal_user)):
    target = authorize('vehicle', user, body.imsi)
    return await audited('vehicle-probe', user, devices.measure('vehicle', 'probe', imsi=target), imsi=target)


@router.post('/vehicle/brake')
async def brake(body: Target, user: UserPublic = Depends(terminal_user)):
    target = authorize('vehicle', user, body.imsi)
    return await audited('vehicle-brake', user, devices.measure('vehicle', 'brake', imsi=target), imsi=target)


@router.post('/sensor/burst')
async def burst(body: Burst, user: UserPublic = Depends(terminal_user)):
    target = authorize('sensor', user, body.imsi)
    return await audited('sensor-burst', user, devices.measure('sensor', 'burst', body.packets, body.pps, imsi=target), imsi=target)
