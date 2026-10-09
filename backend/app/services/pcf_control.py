"""Local PCF control on the core VM, transported through the configured SSH link."""
import json
import shlex

from fastapi import HTTPException

from app.core.config import get_settings


def control_request(payload: dict) -> dict:
    if payload.get('operation') not in {'status', 'laboratory_snapshot'}:
        from app.services.policy_authority_client import request
        authority = payload.get('authority')
        if not isinstance(authority, dict):
            raise HTTPException(428, 'policy_authority_lease_required')
        return request({**authority, 'operation': 'submit',
                        'command': {k: v for k, v in payload.items() if k != 'authority'}})
    from app.services.execution import RemoteExecutionAdapter
    from app.services.scenarios import scenario_manager
    adapter = scenario_manager.adapter
    if get_settings().execution_mode != "remote" or not isinstance(adapter, RemoteExecutionAdapter):
        raise HTTPException(503, "Actuador PCF nativo no conectado")
    # Abstract client socket leaves no files behind. The server pathname is fixed,
    # owned by PCF and mode 0600. Commands execute once; no retry after an ACK loss.
    script = """import socket, json, uuid
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
s.settimeout(10)
s.bind('\\0maestro-mml-' + uuid.uuid4().hex)
try:
    s.sendto(PAYLOAD.encode(), '/var/lib/open5gs/nwdaf/mml.sock')
    print(s.recv(65536).decode())
except (OSError, TimeoutError):
    print(json.dumps({'status': 'failed', 'detail': 'Actuador PCF no disponible o confirmación N7 no recibida; no se reintenta'}))
finally:
    s.close()
""".replace("PAYLOAD", repr(json.dumps(payload)))
    # The script catches transport errors so the SSH adapter cannot retry a
    # mutation merely because the remote command returned a nonzero exit code.
    raw = adapter._execute_sync(adapter._sudo_cmd(shlex.join(["python3", "-c", script])), retries=1)
    result = json.loads(raw)
    if result.get("status") != "success":
        raise HTTPException(502, result.get("detail", "PCF rechazó la mutación"))
    return result
