"""CHF management over the configured core's SSH channel.

No browser-supplied hosts, paths or shell commands. No invented counters or
fallback to experiment databases when the live CHF is empty/unavailable.
"""
import http.client
import json
import re
from urllib.parse import urlencode

import paramiko
from fastapi import HTTPException

from app.core.config import get_settings

COLLECTIONS = {"accounts", "sessions", "cdrs", "ledger"}


def mask_identifiers(value):
    if isinstance(value, dict):
        return {key: mask_identifiers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [mask_identifiers(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"(?<!\d)(?:imsi-)?(\d{5})(\d{4,7})(\d{3})(?!\d)",
                      lambda m: m[1] + "•" * len(m[2]) + m[3], value)
    return value


def management_get(path: str):
    return management_request(path)


def management_request(path: str, *, payload: dict | None = None, method: str | None = None):
    verb = method or ('POST' if payload is not None else 'GET')
    if verb not in ('GET', 'POST', 'PUT') or (verb == 'GET') != (payload is None):
        raise ValueError('Unsupported management operation')
    settings = get_settings()
    if not settings.chf_management_enabled or settings.execution_mode != "remote":
        raise HTTPException(503, "CHF de gestión no conectado")
    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    if not settings.ssh_strict_host_key:
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connection = http.client.HTTPConnection("127.0.0.1", settings.chf_management_port, timeout=8)
    try:
        ssh.connect(settings.testbed_host, port=settings.ssh_port,
                    username=settings.ssh_user, password=settings.ssh_password,
                    key_filename=str(settings.ssh_key_path) if settings.ssh_key_path else None,
                    look_for_keys=False, allow_agent=False, timeout=8,
                    auth_timeout=8, banner_timeout=8)
        with ssh.open_sftp() as sftp:
            if payload is None:
                with sftp.open(settings.chf_reader_token_file, "r") as file:
                    token = file.read(1025).decode().strip()
            else:
                with sftp.open(settings.chf_admin_environment_file, 'r') as file:
                    env = dict(line.split('=', 1) for line in file.read(16384).decode().splitlines() if '=' in line)
                token = env.get('CHF_ADMIN_TOKEN', '')
        if not 32 <= len(token) <= 1024 or not token.isascii() or "\n" in token or "\r" in token:
            raise ValueError("invalid reader credential")
        channel = ssh.get_transport().open_channel(
            "direct-tcpip", ("127.0.0.1", settings.chf_management_port), ("127.0.0.1", 0), timeout=8)
        channel.settimeout(8)
        connection.sock = channel
        connection.request(verb, path,
                           body=json.dumps(payload) if payload is not None else None,
                           headers={"Authorization": "Bearer " + token, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("management request failed")
        if response.status != 200:
            problem = json.loads(raw)
            cause = problem.get('cause', 'MANAGEMENT_ERROR') if isinstance(problem, dict) else 'MANAGEMENT_ERROR'
            if not isinstance(cause, str) or not re.fullmatch(r'[A-Z_]{1,80}', cause):
                cause = 'MANAGEMENT_ERROR'
            raise HTTPException(response.status, f"CHF HTTP {response.status}: {cause}")
        return json.loads(raw)
    except (OSError, paramiko.SSHException, ValueError, http.client.HTTPException):
        # Never expose remote errors, URLs or credentials to the operator.
        raise HTTPException(503, "CHF de gestión no disponible; no se muestran datos simulados") from None
    finally:
        connection.close()
        ssh.close()


def records(kind: str, supi: str | None, limit: int, offset: int):
    if kind not in COLLECTIONS:
        raise HTTPException(404, "Colección desconocida")
    params = {"limit": limit, "offset": offset}
    if supi:
        params["supi"] = supi
    data = management_get("/admin/v1/" + kind + "?" + urlencode(params))
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise HTTPException(502, "Respuesta de gestión CHF inválida")
    return mask_identifiers(data)
