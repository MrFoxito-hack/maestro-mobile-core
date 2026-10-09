"""Fixed live observation programs and bounded transport. No public shell API."""
import hashlib
import json
import re
import shlex
import time
from concurrent.futures import ThreadPoolExecutor

import paramiko
import yaml

from app.core.config import get_settings
from app.laboratory.preflight import CORE_UNITS, EMBB_CRITICAL_UNITS, REQUIRED_GATES
from app.laboratory.repository import canonical, stamp


class ObservationUnavailable(RuntimeError):
    pass


class LabSSH:
    """One bounded invocation of server-owned programs, including the receiver guard."""
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        if self.settings.execution_mode != "remote":
            raise ValueError("El preflight vivo requiere modo remoto; no hay fallback simulado.")

    def read(self, node, script, arguments=(), *, privileged=False):
        ports = {"core": self.settings.ssh_port, "ue": self.settings.ue_ssh_port,
                 "upf": self.settings.upf_ssh_port}
        if node not in ports:
            raise ValueError("Nodo fuera del catálogo.")
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        if not self.settings.ssh_strict_host_key:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(self.settings.testbed_host, port=ports[node], username=self.settings.ssh_user,
                           password=self.settings.ssh_password,
                           key_filename=str(self.settings.ssh_key_path) if self.settings.ssh_key_path else None,
                           look_for_keys=False, allow_agent=False, timeout=5, auth_timeout=5, banner_timeout=5)
            # Remote deadline also bounds the child after loss of the SSH controller.
            command = shlex.join(["timeout", "--kill-after=2", "18", "python3", "-c", script, *arguments])
            if privileged:
                command = "sudo -S -p '' " + command
            stdin, stdout, _ = client.exec_command(command, timeout=20)
            if privileged and self.settings.ssh_password:
                stdin.write(self.settings.ssh_password + '\n')
                stdin.flush()
            stdin.channel.shutdown_write()
            channel = stdout.channel
            deadline, chunks, size = time.monotonic() + 22, [], 0
            while True:
                if channel.recv_ready():
                    data = channel.recv(65536)
                    size += len(data)
                    if size > 2_000_000:
                        raise ObservationUnavailable("probe_output_limit")
                    chunks.append(data)
                if channel.recv_stderr_ready():
                    channel.recv_stderr(65536)  # discard raw diagnostics; may contain sensitive values
                if channel.exit_status_ready() and not channel.recv_ready():
                    break
                if time.monotonic() >= deadline:
                    raise ObservationUnavailable("probe_timeout")
                time.sleep(0.01)
            if channel.recv_exit_status() != 0:
                raise ObservationUnavailable("probe_failed")
            return json.loads(b"".join(chunks))
        except ObservationUnavailable:
            raise
        except Exception:
            raise ObservationUnavailable("probe_unavailable") from None
        finally:
            client.close()


COMMON = r'''
import json, pathlib, subprocess, time, hashlib, sys, sqlite3
def run(argv):
 r=subprocess.run(argv,capture_output=True,text=True,timeout=3)
 if r.returncode: raise RuntimeError('read_failed')
 return r.stdout
def meta():
 return {'utc_ns':time.time_ns(),'monotonic_ns':time.monotonic_ns(),
         'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
result={'clock_start':meta()}
'''
CORE_SCRIPT = COMMON + r'''
supis=json.loads(sys.argv[1]); units=json.loads(sys.argv[2])
result['services']={}
for unit in units:
 try:
  values=dict(line.split('=',1) for line in run(['systemctl','show',unit,'--property=Id,ActiveState,MainPID,ExecMainStartTimestampMonotonic']).splitlines() if '=' in line)
  result['services'][unit]=values
 except Exception: result['services'][unit]={'ActiveState':'unknown'}
result['accounts']={}
try:
 db=sqlite3.connect('file:/home/emsadmin/maestro-charging/charging.sqlite3?mode=ro',uri=True,timeout=2)
 db.row_factory=sqlite3.Row
 for supi in supis:
  row=db.execute("SELECT a.quota_bytes,a.consumed_bytes,COALESCE((SELECT SUM(s.reserved_bytes) FROM charging_sessions s WHERE s.supi=a.supi AND s.status='OPEN'),0) reserved_bytes FROM charging_accounts a WHERE a.supi=?",(supi,)).fetchone()
  result['accounts'][supi]=dict(row) if row else None
 db.close()
except Exception: result['charging_error']='unavailable'
try:
 p=pathlib.Path('/opt/maestro-terminal-media/720p/index.m3u8')
 result['media_manifest']={'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
except Exception: result['media_manifest']=None
result['clock_end']=meta(); print(json.dumps(result))
'''
UE_SCRIPT = COMMON + r'''
supis=json.loads(sys.argv[1]); result['subjects']={}
result['interfaces']=json.loads(run(['ip','-j','-s','-4','addr','show']))
for supi in supis:
 try:
  result['subjects'][supi]=run(['/home/emsadmin/UERANSIM/build/nr-cli',supi,'--exec','ps-list'])
 except Exception: result['subjects'][supi]=None
result['clock_end']=meta(); print(json.dumps(result))
'''
UPF_SCRIPT = COMMON + r'''
result['service']=run(['systemctl','show','open5gs-upfd','--property=ActiveState,MainPID'])
result['interfaces']=json.loads(run(['ip','-j','-s','-4','addr','show']))
result['clock_end']=meta(); print(json.dumps(result))
'''


def session_identity(native, links, dnn):
    """Resolve an active PDU to exactly one observed TUN; no historical IP assumptions."""
    import ipaddress
    parsed = yaml.safe_load(native) if native else {}
    matches = []
    for identity, item in (parsed.items() if isinstance(parsed, dict) else []):
        if not isinstance(item, dict) or item.get('state') != 'PS-ACTIVE' or item.get('apn') != dnn:
            continue
        address = str(ipaddress.IPv4Address(item.get('address')))
        interfaces = [i for i in links if re.fullmatch(r'uesimtun\d+', i.get('ifname', ''))
                      and any(a.get('local') == address for a in i.get('addr_info', []))]
        if len(interfaces) != 1:
            raise ValueError('session_interface_ambiguous')
        matches.append({'pdu_session': str(identity), 'address': address,
                        'interface': interfaces[0]['ifname'], 'dnn': dnn})
    if len(matches) != 1:
        raise ValueError('session_missing_or_ambiguous')
    return matches[0]


class LivePreflight:
    def __init__(self, transport=None):
        self.transport = transport or LabSSH()

    def collect(self, bindings, traffic_budget_bytes):
        if set(bindings) != {'observed', 'competing'}:
            raise ValueError('Se requieren dos sujetos autorizados.')
        supis = [bindings[r]['supi'] for r in ('observed', 'competing')]
        if len(set(supis)) != 2 or any(not re.fullmatch(r'imsi-\d{14,15}', s) for s in supis):
            raise ValueError('Identidades inválidas o repetidas.')
        started, raw, errors = stamp(), {}, {}
        probes = {'core': (CORE_SCRIPT, (json.dumps(supis), json.dumps(CORE_UNITS))),
                  'ue': (UE_SCRIPT, (json.dumps(supis),)), 'upf': (UPF_SCRIPT, ())}
        def observe(item):
            node, (script, args) = item
            try:
                return node, self.transport.read(node, script, args), None
            except Exception:
                return node, {}, 'observation_unavailable'
        with ThreadPoolExecutor(max_workers=3) as pool:
            for node, value, error in pool.map(observe, probes.items()):
                raw[node] = value
                if error: errors[node] = error
        self.raw = raw
        subjects = {}
        checks = []
        for role, binding in bindings.items():
            identity = None
            try:
                identity = session_identity(raw['ue'].get('subjects', {}).get(binding['supi']),
                                            raw['ue'].get('interfaces', []), binding['dnn'])
            except (ValueError, TypeError, yaml.YAMLError):
                pass
            account = raw['core'].get('accounts', {}).get(binding['supi'])
            free = None
            if isinstance(account, dict) and all(type(account.get(k)) is int and account[k] >= 0 for k in ('quota_bytes', 'consumed_bytes', 'reserved_bytes')):
                free = account['quota_bytes'] - account['consumed_bytes'] - account['reserved_bytes']
            subjects[role] = {'alias': binding['alias'], 'session': identity,
                              'charging': account if free is not None else None, 'unreserved_bytes': free}
            checks.append({'id': role + '_session', 'status': 'passed' if identity else 'blocked'})
            checks.append({'id': role + '_declared_budget', 'status': 'passed' if free is not None and free >= traffic_budget_bytes else 'blocked'})
        identities = [v['session'] for v in subjects.values()]
        if all(identities) and (identities[0]['address'] == identities[1]['address'] or identities[0]['interface'] == identities[1]['interface']):
            checks.append({'id': 'distinct_sessions', 'status': 'blocked'})
        services = {unit: raw['core'].get('services', {}).get(unit, {}).get('ActiveState', 'unknown') for unit in CORE_UNITS}
        critical = EMBB_CRITICAL_UNITS if all(b['dnn'] == 'internet' for b in bindings.values()) else CORE_UNITS
        checks.append({'id': 'core_services', 'status': 'passed' if all(services[u] == 'active' for u in critical) else 'blocked'})
        # A budget check excludes neither future preparation nor unobserved competing traffic.
        return {'schema_version': 1, 'source': 'live_ssh', 'read_only': True,
                'started_at': started, 'finished_at': stamp(), 'execution_ready': False,
                'subjects': subjects, 'services': services, 'checks': checks,
                'critical_services': list(critical),
                'noncritical_observations': {u: services[u] for u in CORE_UNITS if u not in critical},
                'pending_gates': list(REQUIRED_GATES), 'errors': errors,
                'clocks': {n: {k: raw[n].get(k) for k in ('clock_start', 'clock_end')} for n in probes},
                'raw_sha256': hashlib.sha256(canonical(raw).encode()).hexdigest(),
                'limitations': ['Preflight de lectura: no demuestra ruta de paquetes ni enforcement.',
                                'Presupuesto declarado sin preparación, vídeo y cierre confirmados.',
                                'No se ha medido sincronización entre hosts.']}
