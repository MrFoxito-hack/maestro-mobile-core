"""Select and establish exactly one observed PDU session for the lab UE."""
import asyncio
import hashlib
import ipaddress
import json
import re
import shlex
from datetime import datetime, timezone
import yaml
from fastapi import HTTPException
from app.core.config import get_settings
from app.db import transaction
from app.services import terminal
from app.services.upf_inventory import profile

POOLS = {'internet': ipaddress.ip_network('10.45.0.0/16'),
         '5g-plus': ipaddress.ip_network('10.47.0.0/16'),
         'corporate': ipaddress.ip_network('10.46.0.0/16')}

def expected_slice(apn):
    if not get_settings().multi_upf_enabled:
        if apn == '5g-plus':
            raise HTTPException(409, 'Perfil URLLC pendiente de migración y aceptación del core')
        return {'sst': 1, 'sd': 1 if apn == 'internet' else 2}
    target = profile(apn)
    if target is None:
        raise HTTPException(422, 'DNN no permitido')
    return {'sst': target['sst'], 'sd': int(target['sd'], 16)}


def matches_slice(observed, expected):
    if not isinstance(observed, dict):
        return False
    sd = observed.get('sd')
    try:
        sd = int(sd, 16) if isinstance(sd, str) else sd
    except ValueError:
        return False
    return observed.get('sst') == expected['sst'] and sd == expected['sd']


def key(data):
    supi = data.get('supi') or ''
    if not re.fullmatch(r'imsi-\d{14,15}', supi):
        raise HTTPException(503, 'Identidad del terminal no confirmada')
    settings = get_settings()
    return hashlib.sha256(f'{settings.testbed_host}:{settings.ue_ssh_port}:{supi}'.encode()).hexdigest()


def sessions(data):
    try:
        native = yaml.safe_load(data.get('native', {}).get('ps-list', '')) or {}
    except yaml.YAMLError:
        native = {}
    result = []
    for identity, pdu in (native.items() if isinstance(native, dict) else []):
        if not isinstance(pdu, dict) or pdu.get('state') != 'PS-ACTIVE':
            continue
        apn, address = pdu.get('apn'), pdu.get('address')
        if apn not in POOLS:
            continue
        try:
            ip = ipaddress.ip_address(address)
        except (ValueError, TypeError):
            continue
        links = [i for i in data.get('interfaces', []) if address in i.get('addresses', [])
                 and re.fullmatch(r'uesimtun\d+', i.get('name', ''))]
        if ip not in POOLS[apn] or len(links) != 1:
            continue
        link = links[0]
        result.append({'apn': apn, 'pdu_session': identity, 'interface': link['name'],
                       'address': address, 'snssai': pdu.get('s-nssai'),
                       'rx_bytes': link.get('rx_bytes'), 'tx_bytes': link.get('tx_bytes')})
    return result


def describe(data):
    observed = sessions(data)
    with transaction() as conn:
        row = conn.execute('SELECT apn FROM terminal_preferences WHERE terminal_key=?', (key(data),)).fetchone()
    preferred = row['apn'] if row else 'internet'
    available = {session['apn'] for session in observed}
    # A persisted UI preference is not network evidence. If that PDU session
    # no longer exists, report the only observed session as active.
    if preferred not in available and len(observed) == 1:
        preferred = observed[0]['apn']
    return {'active_apn': preferred, 'apn_sessions': observed}


async def resolve(imsi=None, required=None):
    imsi = terminal.normalize_imsi(imsi)
    data = await terminal.read_terminal(imsi)
    terminal.confirm_identity(data, imsi)
    selection = describe(data)
    if required and selection['active_apn'] != required:
        raise HTTPException(403, 'Servicio no permitido en el DNN seleccionado')
    matches = [s for s in selection['apn_sessions'] if s['apn'] == selection['active_apn']]
    if len(matches) != 1:
        raise HTTPException(409, 'Sesión PDU ausente o ambigua; no se selecciona una interfaz por su número')
    return matches[0]


async def select(apn, imsi=None):
    if apn not in POOLS:
        raise HTTPException(422, 'DNN no permitido')
    expected = expected_slice(apn)
    async with terminal.control_lock(imsi):
        data = await terminal.read_terminal(imsi)
        terminal.confirm_identity(data, imsi)
        matches = [s for s in sessions(data) if s['apn'] == apn]
        if len(matches) != 1 and len(sessions(data)) == 1:
            matches = [await _switch_single_session(apn, data)]
        if len(matches) != 1:
            raise HTTPException(409, 'DNN sin una sesión PDU activa inequívoca')
        if get_settings().multi_upf_enabled and not matches_slice(matches[0]['snssai'], expected):
            raise HTTPException(409, 'El S-NSSAI observado no coincide con el perfil solicitado')
        with transaction() as conn:
            conn.execute('INSERT INTO terminal_preferences VALUES(?,?,?) ON CONFLICT(terminal_key) '
                         'DO UPDATE SET apn=excluded.apn, updated_at=excluded.updated_at',
                         (key(data), apn, datetime.now(timezone.utc).isoformat()))
        return {'active_apn': apn, 'session': matches[0]}


async def _switch_single_session(apn, data):
    """Release the current UE context and reconnect with one requested DNN."""
    expected = expected_slice(apn)
    supi = data.get('supi')
    await terminal.terminal_runtime.control(supi, 'inspect')
    remote = terminal.adapter()
    settings = get_settings()
    try:
        await remote.native_operation(
            'ueransim-cli', 'ue', {'command': 'deregister', 'node_name': supi}
        )
        await asyncio.sleep(0.6)
    except Exception:
        pass

    await terminal.terminal_runtime.control(supi, 'stop')
    try:
        await terminal.terminal_runtime.control(supi, 'configure', apn=apn, expected=expected)
    finally:
        await terminal.terminal_runtime.control(supi, 'start')

    terminal._af_boost_sessions.pop(supi, None)
    terminal.clear_dynamic_pcc_qos(supi)
    for _ in range(40):
        await asyncio.sleep(0.5)
        try:
            observed = sessions(await terminal.read_terminal(supi))
        except Exception:
            continue
        selected = [session for session in observed if session['apn'] == apn]
        if (len(observed) == 1 and len(selected) == 1 and
                matches_slice(selected[0]['snssai'], expected)):
            return selected[0]
    raise HTTPException(504, f'El UE no confirmÃ³ la sesiÃ³n PDU {apn} dentro del plazo')


async def intranet(imsi=None):
    session = await resolve(imsi, required='corporate')
    command = shlex.join(['curl', '--silent', '--fail', '--noproxy', '*', '--interface', session['interface'],
                          '--connect-timeout', '3', '--max-time', '5', '--max-filesize', '8192',
                          '-H', 'Accept: application/json', 'http://10.46.0.1:8080/intranet'])
    try:
        raw = await terminal.adapter()._run(command, port=get_settings().ue_ssh_port)
        if len(raw) > 8192:
            raise ValueError('Oversized portal')
        portal = json.loads(raw)
        if portal.get('client_ip') != session['address'] or portal.get('service') != 'maestro-corporate':
            raise ValueError('Unexpected origin or source')
    except Exception:
        raise HTTPException(502, 'Intranet no accesible por la sesión corporativa') from None
    # Return only known data, never execute remote HTML in the EMS origin.
    return {'title': 'MAEstro Corp', 'subtitle': 'Intranet corporativa 5G',
            'client_ip': portal['client_ip'], 'interface': session['interface'],
            'snssai': session['snssai'], 'apn': 'corporate', 'source': '10.46.0.1:8080'}
