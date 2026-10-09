"""Observed laboratory UEs; every control requires an explicit terminal identity."""
import asyncio
import json
import re
import shlex
from datetime import datetime, timezone

from fastapi import HTTPException
from app.core.config import get_settings
from app.services.terminal_access import normalize_imsi
from app.services import terminal_runtime
from app.services.upf_inventory import profile
from app.services.scenarios import scenario_manager
from app.services.charging import management_get, management_request, mask_identifiers
from app.services.ue_observation import observed_nodes, terminal_label

STATUS_SCRIPT = r'''
import json,re,subprocess,pathlib,sys
supi=sys.argv[1]
assert re.fullmatch(r'imsi-\d{14,15}',supi)
active='inactive'
links=json.loads(subprocess.check_output(['ip','-j','-s','addr','show']))
interfaces=[{'name':i['ifname'],'addresses':[a['local'] for a in i.get('addr_info',[]) if a['family']=='inet'],
 'rx_bytes':i.get('stats64',i.get('stats',{})).get('rx',{}).get('bytes'),
 'tx_bytes':i.get('stats64',i.get('stats',{})).get('tx',{}).get('bytes')}
 for i in links if re.fullmatch(r'uesimtun\d+',i['ifname'])]
cli='/home/emsadmin/UERANSIM/build/nr-cli'
outputs={}
raw_nodes=subprocess.run([cli,'--dump'],capture_output=True,text=True,timeout=3).stdout.splitlines()
# nr-cli may list the same SUPI more than once while old simulator contexts
# are being retired.  A SUPI is one logical terminal, so preserve order and
# de-duplicate before deciding whether the requested UE is addressable.
all_nodes=list(dict.fromkeys(n.strip() for n in raw_nodes if n.strip().startswith('imsi-')))
nodes=[n for n in all_nodes if supi and (n==supi or n.endswith('-'+supi))]
if len(nodes)==1:
 active='active'
 for command in ('status','ps-list'):
  result=subprocess.run([cli,nodes[0],'--exec',command],capture_output=True,text=True,timeout=3)
  if result.returncode!=0:
   active='unknown'
  outputs[command]=result.stdout
print(json.dumps({'supi':supi,'service':active,'interfaces':interfaces,'native':outputs,'available_nodes':all_nodes}))
'''


def adapter():
    if get_settings().execution_mode != 'remote':
        raise HTTPException(503, 'Terminal disponible solo con UE remoto; sin datos simulados')
    return scenario_manager.adapter


async def read_terminal(imsi=None):
    supi = normalize_imsi(imsi)
    try:
        data = json.loads(await adapter()._run(
            shlex.join(['python3', '-c', STATUS_SCRIPT, supi]), port=get_settings().ue_ssh_port))
        confirm_identity(data, supi)
        return data
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, 'No se pudo consultar el UE remoto') from None


def confirm_identity(data, imsi):
    if data.get('supi') != normalize_imsi(imsi):
        raise HTTPException(409, 'La identidad observada no corresponde al terminal solicitado')


async def snapshot(imsi=None, user=None):
    import yaml
    data = await read_terminal(imsi)
    from app.services.terminal_sessions import describe
    confirm_identity(data, imsi)
    selection = describe(data)
    supi = data.pop('supi', None)
    native = data.pop('native', {})
    available_nodes = data.pop('available_nodes', [])
    try:
        state = yaml.safe_load(native.get('status', '')) or {}
        sessions = yaml.safe_load(native.get('ps-list', '')) or {}
    except yaml.YAMLError:
        state, sessions = {}, {}
    data.update({'source': 'UERANSIM / Linux', 'observed_at': datetime.now(timezone.utc).isoformat(),
                 'subscriber': supi, 'supi': supi, 'imsi': supi, 'radio_metrics': None,
                 'native_state': state, 'sessions': sessions,
                 'registered': isinstance(state, dict) and state.get('rm-state') == 'RM-REGISTERED',
                 'balance': None, 'charging_available': False})
    data.update(selection)
    owned = {s['interface'] for s in selection['apn_sessions']}
    data['interfaces'] = [i for i in data['interfaces'] if i['name'] in owned]
    active_lab_nodes = observed_nodes(available_nodes)

    from app.services.terminal_access import visible_devices
    allowed = {d['supi'] for d in visible_devices(user)} if user else {supi}
    active_lab_nodes = [n for n in active_lab_nodes if n in allowed]
    data['available_nodes'] = [
        {'imsi': n, 'label': f'{terminal_label(n)} ({n})'} for n in active_lab_nodes
    ]
    boost_state = _af_boost_sessions.get(supi, {
        'active': False,
        'qos': '5QI=9',
        'pcc_rule': None,
        'session_url': None
    })
    dyn_pcc = _pcc_dynamic_qos.get(supi)
    if dyn_pcc:
        five_qi = int(dyn_pcc.get('five_qi', 9))
        if five_qi != 9:
            dl_val = dyn_pcc['mbr_dl_mbps']
            ul_val = dyn_pcc['mbr_ul_mbps']
            dl_str = f"{int(dl_val)}M" if float(dl_val).is_integer() else f"{dl_val}M"
            ul_str = f"{int(ul_val)}M" if float(ul_val).is_integer() else f"{ul_val}M"
            boost_state = {
                'active': True,
                'qos': f"5QI={five_qi}",
                'pcc_rule': f"maestro-mml (MBR {dl_str} / {ul_str})",
                'mbr_dl': f"{dl_val} Mbps",
                'mbr_ul': f"{ul_val} Mbps",
                'session_url': None,
                'note': f"PCC Dinámico N7 activo (5QI={five_qi}, MBR {dl_str} DL / {ul_str} UL)"
            }
        else:
            boost_state = {
                'active': False,
                'qos': '5QI=9',
                'pcc_rule': None,
                'session_url': None,
                'note': 'Prioridad estándar restablecida (5QI=9, best effort)'
            }
    data['af_boost'] = boost_state
    data['pcc_qos'] = dyn_pcc
    if supi:
        try:
            data['balance'] = await asyncio.to_thread(management_get, '/admin/v1/accounts/' + supi)
            data['charging_available'] = True
        except HTTPException:
            pass  # Unknown balance must not become zero balance.
    return data


_af_boost_sessions: dict[str, dict] = {}
_pcc_dynamic_qos: dict[str, dict] = {}
_control_locks: dict[str, asyncio.Lock] = {}


def control_lock(imsi):
    return _control_locks.setdefault(normalize_imsi(imsi), asyncio.Lock())


def set_dynamic_pcc_qos(supi: str, five_qi: int, mbr_dl_mbps: float, mbr_ul_mbps: float) -> dict:
    info = {
        'five_qi': five_qi,
        'mbr_dl_mbps': mbr_dl_mbps,
        'mbr_ul_mbps': mbr_ul_mbps,
        'applied_at': datetime.now(timezone.utc).isoformat(),
    }
    _pcc_dynamic_qos[supi] = info
    return info


def clear_dynamic_pcc_qos(supi: str | None = None) -> None:
    if supi:
        _pcc_dynamic_qos.pop(supi, None)
    else:
        _pcc_dynamic_qos.clear()


def get_dynamic_pcc_qos(supi: str) -> dict | None:
    return _pcc_dynamic_qos.get(supi)


async def airplane(enabled: bool, imsi: str | None = None):
    supi = normalize_imsi(imsi)
    async with control_lock(supi):
        # Resolve even before NAS/traffic mutations; shared or unknown units fail closed.
        await terminal_runtime.control(supi, 'inspect')
        deregistered = False
        if enabled:
            clear_dynamic_pcc_qos(supi)
            try:
                await traffic(False, imsi=supi)
            except HTTPException:
                pass
            try:
                data = await read_terminal(supi)
                confirm_identity(data, supi)
                await adapter().native_operation('ueransim-cli', 'ue',
                    {'command': 'deregister', 'node_name': supi})
                deregistered = True
            except HTTPException:
                pass
            if supi in _af_boost_sessions:
                await af_boost(False, supi)
        runtime = await terminal_runtime.control(supi, 'stop' if enabled else 'start')
        return {'enabled': enabled, 'imsi': supi, 'unit': runtime['unit'],
                'deregistration_requested': deregistered,
                'note': 'Solicitud aplicada a esta instancia; confirmar registro por telemetría.'}


async def traffic(start: bool, imsi: str | None = None):
    imsi = normalize_imsi(imsi)
    unit = "maestro-terminal-traffic-" + imsi
    remote = adapter()
    from app.services.terminal_sessions import resolve
    session = await resolve(imsi=imsi) if start else None
    # Fixed target and interface, no arbitrary shell/URL and no background job without expiry.
    command = ('systemd-run --unit=' + unit + ' --collect --property=RuntimeMaxSec=25 '
               '/usr/bin/ping -I ' + shlex.quote(session['interface']) + ' -c 40 -i 0.2 -s 1000 -W 1 ' +
               profile(session['apn'])['gateway']) if start else (
               'systemctl stop ' + unit + '.service')
    try:
        await remote._run(remote._sudo_cmd(command), port=get_settings().ue_ssh_port)
    except Exception:
        raise HTTPException(409, 'No se pudo cambiar la prueba; compruebe la sesión PDU y si ya hay una prueba activa') from None
    return {'requested': 'start' if start else 'stop', 'max_seconds': 25,
            'profile': 'ICMP acotado hacia UPF; no es video ni prueba de velocidad'}


async def topup(request_id: str, imsi: str | None = None):
    supi = normalize_imsi(imsi)
    try:
        result = await asyncio.to_thread(management_request, '/admin/v1/accounts/' + supi + '/topup',
                                        payload={'requestId': request_id, 'amountBytes': 50_000_000})
        from app.services.terminal_sessions import sessions
        for _ in range(6):
            await asyncio.sleep(0.5)
            try:
                state = await read_terminal(imsi)
                if any(s.get('apn') == 'internet' for s in sessions(state)):
                    break
            except Exception:
                pass
        return mask_identifiers(result)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, 'Recarga no confirmada; no se modifica el saldo mostrado') from None


def _n6_probe_sync(interface):
    """One transfer, never retried: retries would silently consume more quota."""
    import paramiko
    settings = get_settings()
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if not settings.ssh_strict_host_key:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(settings.testbed_host, port=settings.ue_ssh_port,
                       username=settings.ssh_user, password=settings.ssh_password,
                       key_filename=str(settings.ssh_key_path) if settings.ssh_key_path else None,
                       look_for_keys=False, allow_agent=False, timeout=5)
        command = shlex.join([
            'curl', '--silent', '--show-error', '--noproxy', '*',
            '--interface', interface, '--connect-timeout', '3', '--max-time', '12',
            '--max-filesize', '262144', '--limit-rate', '32K',
            '--output', '/dev/null', '--write-out', '%{json}',
            'http://10.210.50.1:18090/probe.bin',
        ])
        stdin, stdout, stderr = client.exec_command(command, timeout=16)
        stdin.channel.shutdown_write()
        # curl output contains metadata only; payload is discarded on the UE.
        raw = stdout.read(16385)
        if len(raw) > 16384:
            raise ValueError('Oversized metadata')
        code = stdout.channel.recv_exit_status()
        result = json.loads(raw)
        received = int(result.get('size_download', 0))
        http_status = int(result.get('http_code', 0))
        return {'completed': code == 0 and http_status == 200 and received == 262144,
                'received_bytes': received, 'expected_bytes': 262144,
                'http_status': http_status, 'exit_code': code,
                'duration_seconds': float(result.get('time_total', 0)),
                'local_ip': result.get('local_ip'),
                'interface': interface, 'destination': '10.210.50.1:18090',
                'note': 'Bytes HTTP recibidos; no equivalen al débito IP del CHF. Una interrupción no prueba por sí sola cuota agotada.'}
    except Exception:
        raise HTTPException(503, 'Prueba N6 no confirmada; no se reintenta automáticamente') from None
    finally:
        client.close()


async def n6_probe(imsi=None):
    imsi = normalize_imsi(imsi)
    lock = control_lock(imsi)
    adapter()  # Never run against a simulated scenario.
    if lock.locked():
        raise HTTPException(409, 'Hay otra operación del terminal en curso')
    async with lock:
        from app.services.terminal_sessions import resolve
        session = await resolve(imsi=imsi, required='internet')
        result = await asyncio.to_thread(_n6_probe_sync, session['interface'])
        if result.get('local_ip') and result['local_ip'] != session['address']:
            raise HTTPException(409, 'La IP origen no corresponde a la sesión solicitada')
        return {**result, 'imsi': imsi, 'interface': session['interface'], 'source_ip': session['address']}


async def af_boost(enable: bool, imsi: str | None = None):
    imsi = normalize_imsi(imsi)
    data = await read_terminal(imsi)
    confirm_identity(data, imsi)
    supi = data.get('supi')
    if not supi or not re.fullmatch(r'imsi-\d{14,15}', supi):
        raise HTTPException(422, 'Identidad del terminal no confirmada')

    settings = get_settings()
    current_boost = _af_boost_sessions.get(supi)

    if not enable:
        if current_boost and current_boost.get('session_url'):
            session_url = current_boost['session_url']
            del_cmd = (
                f"curl -s -i --http2-prior-knowledge -X POST -H 'Content-Type: application/json' "
                f"-d '{{}}' {shlex.quote(session_url)}/delete"
            )
            try:
                await adapter()._run(del_cmd, port=settings.ssh_port)
            except Exception:
                pass
        _af_boost_sessions.pop(supi, None)
        clear_dynamic_pcc_qos(supi)
        return {
            'active': False,
            'qos': '5QI=9',
            'session_url': None,
            'pcc_rule': None,
            'note': 'Prioridad estándar restablecida (5QI=9, best effort)'
        }

    if current_boost and current_boost.get('active'):
        return current_boost

    from app.services.terminal_sessions import sessions
    active_sessions = sessions(data)
    internet_sessions = [s for s in active_sessions if s['apn'] == 'internet']
    internet_session = internet_sessions[0] if len(internet_sessions) == 1 else None
    if not internet_session:
        raise HTTPException(409, 'Sesión PDU con DNN internet no activa; no se puede aplicar QoS Boost')

    ue_ip = internet_session['address']

    payload = {
        "ascReqData": {
            "ueIpv4": ue_ip,
            "dnn": "internet",
            "sliceInfo": {"sst": 1, "sd": "000001"},
            "notifUri": "http://10.210.50.1:18090/af/v1/notifications",
            "suppFeat": "0",
            "medComponents": {
                "1": {
                    "medCompN": 1,
                    "medType": "VIDEO",
                    "fStatus": "ENABLED",
                    "marBwDl": "20000 Kbps",
                    "marBwUl": "5000 Kbps",
                    "medSubComps": {
                        "1": {
                            "fNum": 1,
                            "fDescs": [
                                "permit out ip from 10.210.50.1 to 10.45.0.0/16",
                                "permit in ip from 10.45.0.0/16 to 10.210.50.1"
                            ]
                        }
                    }
                }
            }
        }
    }

    json_str = json.dumps(payload)
    curl_create = (
        f"curl -s -i --http2-prior-knowledge -H 'Content-Type: application/json' "
        f"-d {shlex.quote(json_str)} http://127.0.0.13:7777/npcf-policyauthorization/v1/app-sessions"
    )
    try:
        raw_res = await adapter()._run(curl_create, port=settings.ssh_port)
    except Exception as e:
        raise HTTPException(502, f'Fallo de comunicación con PCF N5: {e}')

    loc_match = re.search(r'Location:\s*([^\r\n]+)', raw_res, re.IGNORECASE)
    session_url = loc_match.group(1).strip() if loc_match else None

    if '201' not in raw_res:
        raise HTTPException(502, f'PCF rechazó la solicitud de política AF N5: {raw_res[:200]}')

    boost_info = {
        'active': True,
        'qos': '5QI=2',
        'pcc_rule': 'pcc-boost-video',
        'gbr_dl': '10 Mbps',
        'mbr_dl': '20 Mbps',
        'session_url': session_url,
        'applied_at': datetime.now(timezone.utc).isoformat(),
        'note': 'Flujo QoS prioritario asignado en PCF/SMF/UPF (5QI=2, GBR 10M / MBR 20M)'
    }
    _af_boost_sessions[supi] = boost_info
    set_dynamic_pcc_qos(supi, 2, 20.0, 10.0)
    return boost_info


async def run_speedtest(imsi: str | None = None):
    """Bounded measurement on the requested PDU; no invented IP/rate fallback."""
    from app.services.terminal_sessions import resolve
    from app.services.terminal_inventory import by_supi
    from app.services import terminal_devices
    supi = normalize_imsi(imsi)
    session = await resolve(imsi=supi)
    download = ping = jitter = None
    received = 0
    if session['apn'] == 'internet':
        measurement = await n6_probe(imsi=supi)
        received = measurement['received_bytes']
        duration = measurement['duration_seconds']
        if measurement['completed'] and duration > 0:
            download = round(received * 8 / duration / 1_000_000, 4)
        completed = measurement['completed']
        server = measurement['destination']
        note = 'Transferencia HTTP acotada a 32 KiB/s; no mide capacidad máxima ni subida.'
    else:
        device = by_supi(supi)
        if device is None or device.kind not in ('vehicle', 'sensor'):
            raise HTTPException(409, 'Medición no disponible para este perfil de terminal')
        measurement = await terminal_devices.measure(device.kind,
            'probe' if device.kind == 'vehicle' else 'burst', imsi=supi)
        ping, jitter = measurement['rtt_ms'], measurement.get('jitter_ms')
        completed = measurement['received'] > 0
        server = measurement['target']
        note = 'RTT UDP medido; caudal de descarga y subida no medido.'
    if measurement['interface'] != session['interface'] or measurement['source_ip'] != session['address']:
        raise HTTPException(409, 'La sesión cambió durante la medición; vuelva a consultar el terminal')
    return {'status': 'success' if completed else 'incomplete', 'imsi': supi,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'client_ip': session['address'], 'interface': session['interface'],
            'apn': session['apn'], 'snssai': session['snssai'],
            'server': server, 'server_ip': server, 'qos': 'No medido', 'qos_level': None,
            'is_boosted': bool(_af_boost_sessions.get(supi, {}).get('active')),
            'pcc_rule': None, 'ping_ms': ping, 'jitter_ms': jitter,
            'download_mbps': download, 'upload_mbps': None,
            'bytes_downloaded': received, 'bytes_uploaded': None, 'note': note}
