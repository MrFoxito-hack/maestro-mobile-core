"""Fixed-target URLLC control; never selects an interface, SUPI or VM from input."""
import asyncio
import ipaddress
import json
import re
import shlex

from fastapi import HTTPException

from app.core.config import get_settings
from app.services.execution import ExecutionError, RemoteExecutionAdapter

AGENT = '/opt/maestro-urllc-xdp/urllc_xdp_agent.py'
_switch_lock = asyncio.Lock()


def request(action, *, ue=None, generation=None):
    settings = get_settings()
    if settings.execution_mode != 'remote' or not settings.multi_upf_enabled:
        raise HTTPException(409, 'XDP URLLC requiere la tríada remota')
    if action not in ('status', 'on', 'off', 'confirm'):
        raise ValueError('Unsupported URLLC operation')
    args = ['/usr/sbin/ip', 'netns', 'exec', 'maestro-urllc',
            '/usr/bin/python3', AGENT, action]
    if action in ('on', 'confirm'):
        try:
            address = ipaddress.ip_address(ue)
            if address not in ipaddress.ip_network('10.47.0.0/16'):
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(409, 'La dirección observada no pertenece a URLLC') from None
        args += ['--ue', str(address)]
    if action == 'confirm':
        if not isinstance(generation, str) or not re.fullmatch(r'[0-9]{1,30}', generation):
            raise HTTPException(409, 'Generación PFCP no verificable')
        args += ['--generation', generation]
    remote = RemoteExecutionAdapter({'open5gs-upfd'})
    try:
        output = remote._execute_sync(remote._sudo_cmd(shlex.join(args)),
                                      port=settings.upf_ssh_port, retries=1)
        data = json.loads(output)
        if (not isinstance(data, dict) or data.get('slice') != 'urllc' or
                data.get('namespace') != 'maestro-urllc' or
                type(data.get('available')) is not bool or
                data.get('effective_mode') not in ('kernel', 'xdp', 'unknown')):
            raise ValueError('Invalid agent response')
        return data
    except (ExecutionError, ValueError, TypeError) as exc:
        raise HTTPException(503, 'No se pudo verificar el agente XDP exclusivo de URLLC') from exc


async def status():
    return await asyncio.to_thread(request, 'status')


async def device_status():
    """A missing controller must not hide the rest of the vehicle's telemetry."""
    try:
        return await status()
    except HTTPException as exc:
        return {'available': False, 'mode': 'unknown', 'effective_mode': 'unknown',
                'slice': 'urllc', 'reason': exc.detail}


def _redirects(data, direction):
    try:
        value = data['counters'][direction + '_redirect_requested']['packets']
        if type(value) is not int or value < 0: raise ValueError()
        return value
    except (KeyError, TypeError, ValueError):
        raise HTTPException(409, 'Contadores BPF no verificables') from None


async def switch(mode):
    from app.services import terminal_devices

    if _switch_lock.locked():
        raise HTTPException(409, 'Conmutación URLLC en curso')
    async with _switch_lock:
        if mode in ('kernel', 'legacy'):
            # Recovery never depends on the UE, MEC or charging service.
            return await asyncio.to_thread(request, 'off')
        if mode != 'xdp': raise ValueError('Unsupported mode')
        before = await status()
        if not before['available']:
            raise HTTPException(409, before.get('reason') or 'La política PFCP no admite aceleración')
        session = await terminal_devices.observed('vehicle')
        ue, generation = session['address'], before.get('session_generation')
        if before.get('ue') != ue:
            raise HTTPException(409, 'La sesión del vehículo cambió; esperar nueva autorización PFCP')
        counts = {d: _redirects(before, d) for d in ('ul', 'dl')}
        succeeded = False
        try:
            activated = await asyncio.to_thread(request, 'on', ue=ue)
            if activated.get('session_generation') != generation or activated.get('effective_mode') != 'xdp':
                raise HTTPException(409, 'La sesión cambió durante la activación')
            probe = await terminal_devices.measure('vehicle', 'probe', 20, 20)
            after = await status()
            if (probe.get('source_ip') != ue or probe.get('sent') != 20 or probe.get('received') != 20 or
                    after.get('effective_mode') != 'xdp' or after.get('session_generation') != generation or
                    any(_redirects(after, d) <= counts[d] for d in ('ul', 'dl'))):
                raise HTTPException(409, 'La prueba UE–MEC no confirmó XDP bidireccional; retorno a kernel')
            result = await asyncio.to_thread(request, 'confirm', ue=ue, generation=generation)
            if result.get('effective_mode') != 'xdp' or not result.get('confirmed'):
                raise HTTPException(409, 'No se pudo confirmar la conmutación URLLC')
            succeeded = True
            return {**result, 'verification': probe}
        finally:
            if not succeeded:
                try:
                    await asyncio.shield(asyncio.to_thread(request, 'off'))
                except HTTPException:
                    # The independent agent confirmation deadline also expires
                    # when the EMS process or SSH channel disappears.
                    pass
