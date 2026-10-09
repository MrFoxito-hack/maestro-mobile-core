"""Independent laboratory devices. Only observed, slice-matched TUNs carry traffic."""
import asyncio
import json
import shlex
from datetime import datetime, timezone

from fastapi import HTTPException
from app.core.config import get_settings
from app.services import terminal, terminal_sessions

DEVICES = {
    'vehicle': {'imsi': 'imsi-999700000000002', 'dnn': '5g-plus', 'sst': 2, 'sd': 2,
                'target': '172.31.48.2', 'gateway': '10.47.0.1', 'label': 'OBU-002'},
    'sensor': {'imsi': 'imsi-999700000000003', 'dnn': 'corporate', 'sst': 3, 'sd': 3,
               'target': '10.46.0.1', 'gateway': '10.46.0.1', 'label': 'GW-003'},
}
_locks = {name: asyncio.Lock() for name in DEVICES}
_locks['brake'] = asyncio.Lock()

# Fixed-size UDP echo measurements run on the UE, not on the EMS host. Both
# SO_BINDTODEVICE and source binding prevent accidental management-path probes.
PROBE = r'''
import json,socket,sys,time,statistics,threading,secrets
interface,source,target,kind=sys.argv[1:5]
count,pps=int(sys.argv[5]),int(sys.argv[6])
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET,socket.SO_BINDTODEVICE,interface.encode()+b'\0')
s.bind((source,0)); s.connect((target,8765)); s.settimeout(.1)
token=secrets.token_hex(8).encode(); sent={}; received={}; stop=threading.Event()
def receive():
 while not stop.is_set():
  try:
   data=s.recv(256); now=time.perf_counter()
   if data.startswith(token+b':'):
    seq=int(data.split(b':')[1]); begin=sent.get(seq)
    if begin is not None and seq not in received: received[seq]=(now-begin)*1000
  except (socket.timeout,ValueError,ConnectionRefusedError): pass
t=threading.Thread(target=receive,daemon=True); t.start()
start=time.perf_counter(); transmitted=0
for seq in range(count):
 delay=start+seq/pps-time.perf_counter()
 if delay>0: time.sleep(delay)
 identity=b':sensor-'+str(seq+1).encode() if kind=='fleet' else b''
 packet=(token+b':'+str(seq).encode()+b':'+kind.encode()+identity).ljust(64,b'.')
 sent[seq]=time.perf_counter()
 try: s.send(packet); transmitted+=1
 except OSError: sent.pop(seq,None)
send_seconds=max(time.perf_counter()-start,1/pps)
deadline=time.perf_counter()+.8
while len(received)<transmitted and time.perf_counter()<deadline: time.sleep(.01)
stop.set(); t.join(.2); s.close()
samples=[received[k] for k in sorted(received)]
jitter=statistics.mean(abs(b-a) for a,b in zip(samples,samples[1:])) if len(samples)>1 else None
print(json.dumps({'sent':transmitted,'received':len(samples),'payload_bytes':64,
 'sent_bytes':transmitted*64,'send_seconds':send_seconds,'requested_pps':pps,
 'rtt_ms':statistics.mean(samples) if samples else None,'min_ms':min(samples) if samples else None,
 'max_ms':max(samples) if samples else None,'jitter_ms':jitter,'samples_ms':samples[-40:],
 'loss_pct':100*(transmitted-len(samples))/transmitted if transmitted else None,
 'sent_sequences':sorted(sent),'acked_sequences':sorted(received)}))
'''


def device(name, imsi=None):
    if name not in DEVICES:
        raise HTTPException(404, 'Dispositivo desconocido')
    # The default is used only by the existing operator-owned XDP controller.
    # All user endpoints pass their explicitly authorized IMSI.
    from app.services.terminal_inventory import by_supi, public_device
    from app.services.upf_inventory import profile
    spec = DEVICES[name]
    target = by_supi(terminal.normalize_imsi(imsi or spec['imsi']))
    if target is None or target.kind != name:
        raise HTTPException(422, 'El IMSI no corresponde al tipo de dispositivo solicitado')
    configured = public_device(target)
    network = profile(configured['dnn'])
    return {**spec, 'imsi': target.supi, 'label': target.label,
            'dnn': configured['dnn'], 'sst': network['sst'], 'sd': int(network['sd'], 16),
            'gateway': network['gateway']}


async def observed(name, imsi=None):
    spec = device(name, imsi)
    data = await terminal.read_terminal(spec['imsi'])
    if data.get('supi') != spec['imsi']:
        raise HTTPException(409, 'La identidad observada no corresponde a este dispositivo')
    matches = [s for s in terminal_sessions.sessions(data) if s['apn'] == spec['dnn']
               and terminal_sessions.matches_slice(s['snssai'], spec)]
    if len(matches) != 1:
        raise HTTPException(409, 'Sin sesión PDU del dispositivo; verificar su instancia UERANSIM')
    return matches[0]


async def status(name, imsi=None):
    from app.services import urllc_xdp
    spec = device(name, imsi)
    session, problem = None, None
    try:
        session = await observed(name, imsi=spec['imsi'])
    except HTTPException as exc:
        problem = exc.detail
    xdp = (await urllc_xdp.device_status() if name == 'vehicle' else
           {'available': False, 'reason': 'El acelerador está reservado a URLLC'})
    if name == 'vehicle' and xdp.get('available') and (session is None or xdp.get('ue') != session['address']):
        xdp = {**xdp, 'available': False, 'reason': 'Sesión del vehículo no correlacionada con PFCP'}
    return {**spec, 'id': name, 'session': session, 'connected': session is not None,
            'problem': problem, 'observed_at': datetime.now(timezone.utc).isoformat(),
            'xdp': xdp,
            'measurement': 'UDP echo · 64 B · RTT UE–destino · puerto 8765'}


async def measure(name, action, count=20, pps=20, *, fleet_size=None, imsi=None):
    spec = device(name, imsi)
    if not get_settings().multi_upf_enabled:
        raise HTTPException(409, 'La tríada de red no está habilitada')
    if action not in ('probe', 'brake', 'burst') or (action == 'burst') != (name == 'sensor'):
        raise HTTPException(422, 'Acción incompatible con el dispositivo')
    if not (1 <= count <= 1000 and 1 <= pps <= 200):
        raise HTTPException(422, 'Máximo 1000 paquetes y 200 pps')
    if fleet_size is not None and (name != 'sensor' or action != 'burst' or fleet_size != count):
        raise HTTPException(422, 'Un paquete por sensor virtual y ciclo')
    if action == 'brake':
        count, pps = 1, 1
    lock = terminal.control_lock(spec['imsi'])
    if lock.locked():
        raise HTTPException(409, 'Medición en curso para este dispositivo')
    async with lock:
        session = await observed(name, imsi=spec['imsi'])
        command = shlex.join(['timeout', '12', 'python3', '-c', PROBE,
                              session['interface'], session['address'], spec['target'],
                              'fleet' if fleet_size is not None else action, str(count), str(pps)])
        # Bound total duration as well as rate and packet count.
        if count / pps > 6:
            raise HTTPException(422, 'La ráfaga no puede superar 6 segundos')
        remote = terminal.adapter()
        try:
            raw = await asyncio.to_thread(remote._execute_sync, remote._sudo_cmd(command),
                                          port=get_settings().ue_ssh_port, retries=1)
            result = json.loads(raw)
        except Exception:
            raise HTTPException(503, 'No se pudo medir el tráfico del dispositivo') from None
    fleet = {} if fleet_size is None else {
        'virtual_sensors': fleet_size, 'radio_ues': 1,
        'sent_sensor_ids': [n + 1 for n in result['sent_sequences']],
        'acked_sensor_ids': [n + 1 for n in result['acked_sequences']],
    }
    return {**result, **fleet, 'imsi': spec['imsi'], 'device': name, 'action': action, 'target': spec['target'],
            'interface': session['interface'], 'source_ip': session['address'],
            'observed_at': datetime.now(timezone.utc).isoformat()}
