"""EMS transport to the durable Core authority. Never renew/fabricate a fence."""
import json
import shlex

from fastapi import HTTPException

from app.core.config import get_settings


def request(payload):
    from app.services.execution import RemoteExecutionAdapter
    from app.services.scenarios import scenario_manager
    adapter = scenario_manager.adapter
    if get_settings().execution_mode != 'remote' or not isinstance(adapter, RemoteExecutionAdapter):
        raise HTTPException(503, 'policy_authority_remote_required')
    script = """import json,socket
try:
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(20)
  s.connect('/run/maestro-policy-authority/control.sock')
  s.sendall((PAYLOAD+'\\n').encode())
  with s.makefile('rb') as stream:
   raw=stream.readline(65537)
  if len(raw)>65536: raise ValueError('oversized')
  print(json.dumps(json.loads(raw)))
except (OSError,ValueError):
 print(json.dumps({'status':'blocked','error_code':'authority_transport_outcome_unknown','http_status':503}))
""".replace('PAYLOAD', repr(json.dumps(payload)))
    try:
        raw = adapter._execute_sync(adapter._sudo_cmd(shlex.join(['python3', '-c', script])), retries=1)
        result = json.loads(raw)
    except Exception:
        raise HTTPException(503, 'authority_transport_outcome_unknown') from None
    if result.get('status') != 'success':
        status = result.get('http_status', 503)
        raise HTTPException(status if status in {400, 408, 409, 413, 422, 428, 503} else 503,
                            result.get('error_code', 'authority_rejected'))
    return result['data']


def owner_for(user):
    return f'{user.testbed or "local"}/{user.username}'
