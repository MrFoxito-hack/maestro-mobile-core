"""Restricted media relay: every asset is fetched on UE, bound to its PDU TUN."""
import asyncio
import re
import shlex
import threading
import paramiko
from fastapi import HTTPException
from app.core.config import get_settings
from app.services.terminal import adapter

_gate = asyncio.Semaphore(4)
MAX_BYTES = 5_000_000

_ssh_lock = threading.Lock()
_cached_client: paramiko.SSHClient | None = None


def validate_asset(profile: str, asset: str):
    if profile not in ('720p', '1080p') or not re.fullmatch(r'index\.m3u8|init\.mp4|seg0[0-2][0-9]\.m4s', asset):
        raise HTTPException(404, 'Recurso multimedia no disponible')


def _get_ssh_client() -> paramiko.SSHClient:
    global _cached_client
    with _ssh_lock:
        if _cached_client is not None:
            transport = _cached_client.get_transport()
            if transport is not None and transport.is_active():
                return _cached_client
            try:
                _cached_client.close()
            except Exception:
                pass
            _cached_client = None

        settings = get_settings()
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        if not settings.ssh_strict_host_key:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(settings.testbed_host, port=settings.ue_ssh_port,
                       username=settings.ssh_user, password=settings.ssh_password,
                       key_filename=str(settings.ssh_key_path) if settings.ssh_key_path else None,
                       look_for_keys=False, allow_agent=False, timeout=5)
        _cached_client = client
        return client


def _fetch(profile, asset, interface):
    command = shlex.join(['curl', '--silent', '--fail', '--noproxy', '*',
                          '--tcp-nodelay',
                          '--interface', interface, '--connect-timeout', '3', '--max-time', '15',
                          '--max-filesize', str(MAX_BYTES),
                          'http://10.210.50.1:18090/media/' + profile + '/' + asset])
    payload = None
    for attempt in range(2):
        try:
            client = _get_ssh_client()
            stdin, stdout, stderr = client.exec_command(command, timeout=18)
            stdin.channel.shutdown_write()
            payload = stdout.read(MAX_BYTES + 1)
            if len(payload) > MAX_BYTES or stdout.channel.recv_exit_status() != 0 or not payload:
                raise ValueError('Incomplete transfer')
            break
        except Exception:
            global _cached_client
            with _ssh_lock:
                if _cached_client is not None:
                    try:
                        _cached_client.close()
                    except Exception:
                        pass
                    _cached_client = None
            if attempt == 1:
                raise HTTPException(502, 'Flujo N6 interrumpido. Verifique sesión PDU, origen y evidencia CHF; sin reintento automático.') from None

    try:
        if asset == 'index.m3u8' and payload:
            # Never permit an origin manifest to turn this into an arbitrary proxy.
            lines = payload.decode('ascii').splitlines()
            for line in lines:
                if 'URI=' in line and line != '#EXT-X-MAP:URI="init.mp4"':
                    raise ValueError('Unsupported manifest URI')
                if line.startswith('#EXT-X-MAP:') and line != '#EXT-X-MAP:URI="init.mp4"':
                    raise ValueError('Unknown initialization asset')
                if line.startswith(('#EXT-X-KEY', '#EXT-X-SESSION', '#EXT-X-MEDIA:', '#EXT-X-STREAM-INF')):
                    raise ValueError('Unsupported manifest directive')
                if line and not line.startswith('#') and not re.fullmatch(r'seg0[0-2][0-9]\.m4s', line):
                    raise ValueError('Unknown segment')
            if not lines or lines[0] != '#EXTM3U':
                raise ValueError('Invalid manifest')
        return payload
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, 'Flujo N6 interrumpido. Verifique sesión PDU, origen y evidencia CHF; sin reintento automático.') from None


async def fetch(profile: str, asset: str, imsi=None):
    validate_asset(profile, asset)
    adapter()
    from app.services.terminal_sessions import resolve
    session = await resolve(imsi, required='internet')
    try:
        async with asyncio.timeout(18):
            async with _gate:
                # Shield retains the slot until the bounded remote transfer finishes even
                # if a browser disconnects, preventing unbounded detached curl processes.
                task = asyncio.create_task(asyncio.to_thread(_fetch, profile, asset, session['interface']))
                try:
                    return await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise
    except TimeoutError:
        raise HTTPException(429, 'Transferencias saturadas; intente nuevamente')
