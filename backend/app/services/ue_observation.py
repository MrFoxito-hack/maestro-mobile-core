"""Read-only UERANSIM observations; missing evidence is never deregistration."""
import json
import re

import yaml


def terminal_label(imsi: str) -> str:
    from app.services.terminal_inventory import by_supi
    device=by_supi(imsi)
    return device.label if device else 'UE'


def observed_nodes(nodes: list[str]) -> list[str]:
    return sorted({node.strip() for node in nodes
                   if isinstance(node, str) and re.fullmatch(r'imsi-\d{14,15}', node.strip())})


# One SSH connection to the UE host; one failure does not discard the other UEs.
SUBSCRIBER_STATUS_SCRIPT = r'''
import json,re,subprocess,sys
from concurrent.futures import ThreadPoolExecutor
cli='/home/emsadmin/UERANSIM/build/nr-cli'
dump=subprocess.run([cli,'--dump'],capture_output=True,text=True,timeout=5,check=True)
nodes=sorted({n.strip() for n in dump.stdout.splitlines() if re.fullmatch(r'imsi-\d{14,15}',n.strip())})
target=sys.argv[1] if len(sys.argv)>1 else None
def read(node):
    native={}
    for command in ('status','ps-list'):
        try:
            result=subprocess.run([cli,node,'--exec',command],capture_output=True,text=True,timeout=3,check=True)
            native[command]=result.stdout
        except (subprocess.SubprocessError,OSError):
            native[command]=None
    return node,native
with ThreadPoolExecutor(max_workers=4) as pool:
    native=dict(pool.map(read,[n for n in nodes if not target or n==target]))
print(json.dumps({'nodes':nodes,'native':native}))
'''


def unknown_status(reason: str = 'unavailable', *, available: bool | None = None) -> dict:
    return {
        'registered': None, 'cm_state': None, 'rm_state': None, 'mm_state': None,
        'cell_id': None, 'tac': None, 'guti': None, 'pdu_sessions': [], 'active_ip': None,
        'source': 'UERANSIM', 'observation_status': reason,
        'terminal_available': available,
    }


def _mapping(raw: str | None) -> dict:
    try:
        value = yaml.safe_load(raw or '')
        return value if isinstance(value, dict) else {}
    except yaml.YAMLError:
        return {}


def _text(value) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)


def parse_status(native: dict) -> dict:
    state = _mapping(native.get('status'))
    sessions = _mapping(native.get('ps-list'))
    result = unknown_status(available=True)
    rm = state.get('rm-state')
    result.update({
        'registered': True if rm == 'RM-REGISTERED' else False if rm == 'RM-DEREGISTERED' else None,
        'observation_status': 'observed' if isinstance(rm, str) else 'unavailable',
        'rm_state': _text(rm), 'cm_state': _text(state.get('cm-state')),
        'mm_state': _text(state.get('mm-state')), 'cell_id': _text(state.get('current-cell')),
        'tac': _text(state.get('current-tac')), 'guti': _text(state.get('stored-guti')),
    })
    def _parse_snssai(pdu_dict: dict) -> dict | None:
        snssai = pdu_dict.get('s-nssai')
        if not isinstance(snssai, dict):
            return None
        raw_sst = snssai.get('sst')
        raw_sd = snssai.get('sd')
        sst_val = None
        sd_val = None
        if isinstance(raw_sst, int):
            sst_val = raw_sst
        elif isinstance(raw_sst, str):
            try:
                sst_val = int(raw_sst, 16) if raw_sst.startswith(('0x', '0X')) else int(raw_sst)
            except ValueError:
                sst_val = None
        if isinstance(raw_sd, int):
            sd_val = f"{raw_sd:06x}"
        elif isinstance(raw_sd, str):
            try:
                sd_val = f"{int(raw_sd, 16):06x}" if raw_sd.startswith(('0x', '0X')) else raw_sd
            except ValueError:
                sd_val = raw_sd
        if sst_val is not None:
            return {'sst': sst_val, 'sd': sd_val}
        return None

    result['pdu_sessions'] = [
        {'session_id': str(identity), 'state': _text(pdu.get('state')),
         'type': _text(pdu.get('session-type')), 'apn': _text(pdu.get('apn')),
         'address': _text(pdu.get('address')), 'ambr': _text(pdu.get('ambr')),
         's_nssai': _parse_snssai(pdu)}
        for identity, pdu in sessions.items() if isinstance(pdu, dict)
    ]
    result['active_ip'] = next((pdu['address'] for pdu in result['pdu_sessions']
                                if pdu['state'] == 'PS-ACTIVE' and pdu['address']), None)
    return result
