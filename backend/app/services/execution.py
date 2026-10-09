import asyncio
import json
import re
import shlex
import shutil
from abc import ABC, abstractmethod
from pathlib import Path

import psutil

from app.core.config import get_settings


class ExecutionError(RuntimeError):
    pass


class ExecutionAdapter(ABC):
    @abstractmethod
    async def start_service(self, unit: str) -> None: ...

    @abstractmethod
    async def stop_service(self, unit: str) -> None: ...

    @abstractmethod
    async def service_status(self, unit: str) -> str: ...

    @abstractmethod
    async def service_statuses(self, units: list[str]) -> dict[str, str]: ...

    @abstractmethod
    async def logs(self, unit: str, lines: int = 100) -> list[str]: ...

    @abstractmethod
    async def runtime_snapshot(self) -> dict: ...

    @abstractmethod
    async def remote_kpis(self) -> dict: ...

    @abstractmethod
    async def generate_test_traffic(self, count: int = 3) -> dict: ...

    @abstractmethod
    async def set_ip_forward(self, enable: bool) -> None: ...

    @abstractmethod
    async def get_ip_forward(self) -> bool: ...

    @abstractmethod
    async def native_operation(
        self, operation: str, component: str, parameters: dict
    ) -> dict: ...


class SimulatedExecutionAdapter(ExecutionAdapter):
    def __init__(self) -> None:
        self._ip_forward = True
        path = get_settings().mock_state_path
        try:
            self.states: dict[str, str] = json.loads(path.read_text(encoding="utf-8")).get("units", {})
        except (OSError, json.JSONDecodeError) as exc:
            raise ExecutionError(f"No se pudo cargar el estado mock {path}: {exc}") from exc

    async def start_service(self, unit: str) -> None:
        await asyncio.sleep(0.03)
        self.states[unit] = "running"

    async def stop_service(self, unit: str) -> None:
        await asyncio.sleep(0.02)
        self.states[unit] = "stopped"

    async def service_status(self, unit: str) -> str:
        return self.states.get(unit, "unknown")

    async def service_statuses(self, units: list[str]) -> dict[str, str]:
        return {unit: self.states.get(unit, "unknown") for unit in units}

    async def logs(self, unit: str, lines: int = 100) -> list[str]:
        state = await self.service_status(unit)
        return [f"[INFO] {unit}: execution_mode=simulated state={state}"]

    async def runtime_snapshot(self) -> dict:
        return {
            "source": "mock",
            "hostname": "ems-testbed-mock",
            "interfaces": [],
            "listening_ports": [],
        }

    async def remote_kpis(self) -> dict:
        return {
            "interfaces": {
                "lo": {"rx_bytes": 1024000, "tx_bytes": 1024000},
                "ogstun": {"rx_bytes": 204800, "tx_bytes": 102400},
                "enp0s3": {"rx_bytes": 5120000, "tx_bytes": 3072000},
            },
            "load_1m": 0.15,
            "memory_percent": 18.5,
        }

    async def generate_test_traffic(self, count: int = 3) -> dict:
        return {
            "output": f"PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.\n{count} packets transmitted, {count} received, 0% packet loss\nrtt min/avg/max = 24.1/25.3/27.0 ms",
            "success": self._ip_forward,
        }

    async def set_ip_forward(self, enable: bool) -> None:
        self._ip_forward = enable

    async def get_ip_forward(self) -> bool:
        return self._ip_forward

    async def native_operation(
        self, operation: str, component: str, parameters: dict
    ) -> dict:
        nodes = ["UERANSIM-gnb-999-70-1", "imsi-999700000000001"]
        if operation == "ueransim-nodes":
            return {"output": "\n".join(nodes), "data": {"nodes": nodes}}
        if operation == "ueransim-cli":
            node = parameters.get("node_name") or (
                nodes[1] if component == "ue" else nodes[0]
            )
            command = parameters.get("command", "status")
            samples = {
                "status": "cm-state: CM-CONNECTED\nrm-state: RM-REGISTERED\nmm-state: MM-REGISTERED/NORMAL-SERVICE",
                "info": f"node-name: {node}\ncomponent: {component}\nplmn: 999/70",
                "amf-list": "AMF[0] 127.0.0.5:38412 connected",
                "amf-info": "state: connected\naddress: 127.0.0.5\nport: 38412",
                "ue-count": "1",
                "ue-list": "UE[1] imsi-999700000000001 connected",
                "coverage": "cell[1] plmn[999/70] tac[1] suitable",
                "timers": "T3512: running\nT3502: stopped",
                "rls-state": "state: in-coverage",
                "ps-list": "PDU Session1:\n state: PS-ACTIVE\n apn: internet\n address: 10.45.0.2",
                "ps-release": "PDU session release procedure triggered",
                "deregister": "De-registration procedure triggered. UE device will be switched off.",
            }
            output = samples.get(command, f"{command}: completed")
            return {"output": output, "data": _key_value_payload(output)}
        if operation == "open5gs-info":
            endpoint = parameters.get("endpoint")
            if endpoint == "pdu-info":
                data = {"items": [{"supi": "imsi-***0001", "pdu": [{"psi": 1, "dnn": "internet", "ipv4": "10.45.0.2", "pdu_state": "active"}]}]}
            elif endpoint in {"gnb-info", "enb-info"}:
                data = {"items": [{"id": 1, "plmn": "99970", "num_connected_ues": 1}]}
            else:
                data = {"items": [{"supi": "imsi-***0001", "cm_state": "connected"}]}
            return {"output": json.dumps(data, indent=2), "data": data}
        if operation == "software-version":
            output = "Open5GS daemon v2.7.x (simulated)" if component not in {"gnb", "ue"} else "UERANSIM v3.2.x (simulated)"
            return {"output": output, "data": {"version": output}}
        raise ExecutionError(f"OperaciÃ³n nativa no soportada: {operation}")


class LocalExecutionAdapter(ExecutionAdapter):
    """Controls only units declared in the scenario catalog; never invokes a shell."""

    def __init__(self, allowed_units: set[str]) -> None:
        self.allowed_units = allowed_units
        if not shutil.which("systemctl"):
            raise ExecutionError("systemctl no está disponible")

    def _validate(self, unit: str) -> None:
        if unit not in self.allowed_units:
            raise ExecutionError(f"Unidad no permitida: {unit}")

    async def _run(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
        if process.returncode != 0:
            raise ExecutionError(stderr.decode(errors="replace").strip() or "Falló la operación")
        return stdout.decode(errors="replace")

    async def start_service(self, unit: str) -> None:
        self._validate(unit)
        await self._run("sudo", "-n", "systemctl", "start", unit)

    async def stop_service(self, unit: str) -> None:
        self._validate(unit)
        await self._run("sudo", "-n", "systemctl", "stop", unit)

    async def service_status(self, unit: str) -> str:
        return (await self.service_statuses([unit]))[unit]

    async def service_statuses(self, units: list[str]) -> dict[str, str]:
        for unit in units:
            self._validate(unit)
        output = await self._run(
            "systemctl", "show", "--property=Id", "--property=ActiveState", "--no-pager", *units
        )
        return _parse_service_states(output, units)

    async def logs(self, unit: str, lines: int = 100) -> list[str]:
        self._validate(unit)
        output = await self._run("journalctl", "-u", unit, "-n", str(min(lines, 500)), "--no-pager")
        return output.splitlines()

    async def runtime_snapshot(self) -> dict:
        hostname, interfaces, sockets = await asyncio.gather(
            self._run("hostname"),
            self._run("ip", "-j", "address", "show"),
            self._run("ss", "-H", "-lnutS"),
        )
        return _runtime_payload("local", hostname, interfaces, sockets)

    async def remote_kpis(self) -> dict:
        try:
            net_content = Path("/proc/net/dev").read_text(encoding="utf-8")
            load_content = Path("/proc/loadavg").read_text(encoding="utf-8")
        except OSError:
            return {"interfaces": {}, "load_1m": 0.0, "memory_percent": 0.0}

        interfaces = {}
        for line in net_content.splitlines():
            if ":" not in line:
                continue
            name, _, stats = line.partition(":")
            cols = stats.split()
            if len(cols) >= 9:
                interfaces[name.strip()] = {"rx_bytes": int(cols[0]), "tx_bytes": int(cols[8])}

        load_1m = float(load_content.split()[0]) if load_content.split() else 0.0
        mem = psutil.virtual_memory().percent
        return {"interfaces": interfaces, "load_1m": load_1m, "memory_percent": mem}

    async def generate_test_traffic(self, count: int = 3) -> dict:
        try:
            output = await self._run(
                "sudo", "-n", "ip", "netns", "exec", "ueransim-999700000000001-internet-psi1",
                "ping", "-c", str(min(count, 10)), "-W", "2", "8.8.8.8"
            )
            return {"output": output, "success": "bytes from" in output}
        except Exception as exc:
            return {"output": str(exc), "success": False}

    async def set_ip_forward(self, enable: bool) -> None:
        try:
            val = "1" if enable else "0"
            await self._run("sudo", "-n", "sysctl", "-w", f"net.ipv4.ip_forward={val}")
        except Exception:
            pass

    async def get_ip_forward(self) -> bool:
        try:
            content = Path("/proc/sys/net/ipv4/ip_forward").read_text().strip()
            return content == "1"
        except Exception:
            return True

    def _nr_cli_path(self) -> str:
        candidates = [
            shutil.which("nr-cli"),
            str(Path.home() / "UERANSIM" / "build" / "nr-cli"),
            "/usr/local/bin/nr-cli",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return candidate
        raise ExecutionError("nr-cli no estÃ¡ instalado o no se encontrÃ³ en UERANSIM/build")

    async def native_operation(
        self, operation: str, component: str, parameters: dict
    ) -> dict:
        if operation == "ueransim-nodes":
            output = await self._run(self._nr_cli_path(), "--dump")
            nodes = _parse_ueransim_nodes(output)
            return {"output": output, "data": {"nodes": nodes}}
        if operation == "ueransim-cli":
            cli = self._nr_cli_path()
            dumped = await self._run(cli, "--dump")
            nodes = _parse_ueransim_nodes(dumped)
            node = _select_ueransim_node(nodes, component, parameters.get("node_name"))
            raw_command = str(parameters.get("command", ""))
            if component == "gnb" and raw_command == "amf-info" and not parameters.get("amf_id"):
                try:
                    list_out = await self._run(cli, node, "--exec", "amf-list")
                    amf_ids = re.findall(r"id:\s*(\d+)", list_out)
                except Exception:
                    amf_ids = []
                if not amf_ids:
                    amf_ids = ["0"]
                outputs = []
                for aid in amf_ids:
                    sub_out = await self._run(cli, node, "--exec", f"amf-info {aid}")
                    outputs.append(sub_out.strip())
                output = "\n\n".join(outputs)
                return {"output": output, "data": _key_value_payload(output)}
            command = _validate_ueransim_command(component, parameters)
            output = await self._run(cli, node, "--exec", command)
            return {"output": output, "data": _key_value_payload(output)}
        if operation == "open5gs-info":
            return await asyncio.to_thread(_local_open5gs_info, component, parameters)
        if operation == "software-version":
            binary = _component_binary(component)
            output = await self._run(binary, "-v")
            return {"output": output, "data": {"version": output.strip()}}
        raise ExecutionError(f"OperaciÃ³n nativa no soportada: {operation}")



class RemoteExecutionAdapter(ExecutionAdapter):
    """Observes/manages a pre-existing testbed through SSH."""

    def __init__(self, allowed_units: set[str]) -> None:
        settings = get_settings()
        if not settings.testbed_host or not settings.ssh_user:
            raise ExecutionError("Faltan EMS_TESTBED_HOST o EMS_SSH_USER")
        if not settings.ssh_key_path and not settings.ssh_password:
            raise ExecutionError("Se requiere EMS_SSH_KEY_PATH o EMS_SSH_PASSWORD")
        self.allowed_units = allowed_units
        self.settings = settings

    def _validate(self, unit: str) -> None:
        if unit not in self.allowed_units:
            raise ExecutionError(f"Unidad no permitida: {unit}")

    def _remote_unit_name(self, unit: str) -> str:
        if unit in ("open5gs-upfd2", "open5gs-upfd-2"):
            return "open5gs-upfd"
        return unit

    def _port_for_unit(self, unit: str) -> int:
        if unit in ("open5gs-upfd2", "open5gs-upfd-2"):
            return getattr(self.settings, "upf2_ssh_port", 2224)
        if unit in ("open5gs-upfd", "open5gs-upfd-urllc"):
            return getattr(self.settings, "upf_ssh_port", 2223)
        if unit == "ueransim-gnb":
            return getattr(self.settings, "gnb_ssh_port", 2225)
        if unit == "ueransim-ue":
            return getattr(self.settings, "ue_ssh_port", 2226)
        return self.settings.ssh_port

    def _execute_sync(self, command: str, port: int | None = None, *, retries: int = 3) -> str:
        import paramiko
        import time

        target_port = port or self.settings.ssh_port
        connect_kwargs = {
            "hostname": self.settings.testbed_host,
            "port": target_port,
            "username": self.settings.ssh_user,
            "look_for_keys": bool(self.settings.ssh_key_path),
            "allow_agent": False,
            "timeout": 10,
        }
        if self.settings.ssh_key_path:
            connect_kwargs["key_filename"] = str(self.settings.ssh_key_path)
        if self.settings.ssh_password:
            connect_kwargs["password"] = self.settings.ssh_password

        last_err = None
        for attempt in range(retries):
            client = paramiko.SSHClient()
            if self.settings.ssh_strict_host_key:
                client.load_system_host_keys()
                client.set_missing_host_key_policy(paramiko.RejectPolicy())
            else:
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(**connect_kwargs)
                _, stdout, stderr = client.exec_command(command, timeout=20)
                # Drain stdout and stderr while the command runs. Calling
                # recv_exit_status() first can deadlock once a verbose tshark
                # analysis fills Paramiko's SSH channel window.
                channel = stdout.channel
                output_chunks: list[bytes] = []
                error_chunks: list[bytes] = []
                deadline = time.monotonic() + 120
                while True:
                    while channel.recv_ready():
                        output_chunks.append(channel.recv(1024 * 1024))
                    while channel.recv_stderr_ready():
                        error_chunks.append(channel.recv_stderr(256 * 1024))
                    if channel.exit_status_ready():
                        while channel.recv_ready():
                            output_chunks.append(channel.recv(1024 * 1024))
                        while channel.recv_stderr_ready():
                            error_chunks.append(channel.recv_stderr(256 * 1024))
                        break
                    if time.monotonic() >= deadline:
                        channel.close()
                        raise TimeoutError("El comando remoto excedió 120 segundos")
                    time.sleep(0.01)

                exit_code = channel.recv_exit_status()
                output = b"".join(output_chunks).decode(errors="replace")
                error = b"".join(error_chunks).decode(errors="replace")
                if exit_code != 0:
                    raise ExecutionError(error.strip() or output.strip() or f"SSH finalizó con código {exit_code}")
                return output
            except Exception as exc:
                last_err = exc
                if attempt < retries - 1:
                    time.sleep(0.35)
            finally:
                client.close()

        raise ExecutionError(f"Error en comunicación SSH tras reintentos (puerto {target_port}): {last_err}")

    def _sudo_cmd(self, subcmd: str) -> str:
        if self.settings.ssh_password:
            escaped_pass = shlex.quote(self.settings.ssh_password)
            return f"printf '%s\\n' {escaped_pass} | sudo -S {subcmd}"
        return f"sudo -n {subcmd}"

    async def _run(self, command: str, port: int | None = None) -> str:
        return await asyncio.to_thread(self._execute_sync, command, port)

    async def start_service(self, unit: str) -> None:
        self._validate(unit)
        port = self._port_for_unit(unit)
        r_unit = self._remote_unit_name(unit)
        cmd = self._sudo_cmd(f"systemctl start {shlex.quote(r_unit)}")
        await self._run(cmd, port=port)

    async def stop_service(self, unit: str) -> None:
        self._validate(unit)
        port = self._port_for_unit(unit)
        r_unit = self._remote_unit_name(unit)
        cmd = self._sudo_cmd(f"systemctl stop {shlex.quote(r_unit)}")
        await self._run(cmd, port=port)

    async def service_status(self, unit: str) -> str:
        return (await self.service_statuses([unit]))[unit]

    async def service_statuses(self, units: list[str]) -> dict[str, str]:
        for unit in units:
            self._validate(unit)

        groups: dict[int, list[str]] = {}
        for unit in units:
            p = self._port_for_unit(unit)
            groups.setdefault(p, []).append(unit)

        results: dict[str, str] = {}
        for port, port_units in groups.items():
            r_units = [self._remote_unit_name(u) for u in port_units]
            safe_units = " ".join(shlex.quote(u) for u in set(r_units))
            try:
                output = await self._run(
                    f"systemctl show --property=Id --property=ActiveState --no-pager {safe_units}",
                    port=port,
                )
                parsed = _parse_service_states(output, r_units)
                for u in port_units:
                    results[u] = parsed.get(self._remote_unit_name(u), "unknown")
            except Exception:
                results.update({u: "unknown" for u in port_units})
        return results

    async def logs(self, unit: str, lines: int = 100) -> list[str]:
        self._validate(unit)
        port = self._port_for_unit(unit)
        r_unit = self._remote_unit_name(unit)
        safe_lines = min(max(lines, 1), 500)
        output = await self._run(f"journalctl -u {shlex.quote(r_unit)} -n {safe_lines} --no-pager", port=port)
        return output.splitlines()

    async def runtime_snapshot(self) -> dict:
        from app.services.upf_inventory import inventory
        from datetime import datetime, timezone
        targets = [
            {'id': 'core', 'port': self.settings.ssh_port, 'namespace': None, 'role': '5GC control'},
            {'id': 'gnb-vm', 'port': self.settings.gnb_ssh_port, 'namespace': None, 'role': 'UERANSIM gNB'},
            {'id': 'ue-vm', 'port': self.settings.ue_ssh_port, 'namespace': None, 'role': 'UERANSIM terminals'},
        ]
        contexts = {'upf-01': 'upf-vm', 'upf-02': 'upf-vm2', 'upf-03': 'upf-urllc'}
        for target in inventory()['targets']:
            targets.append({'id': contexts[target['id']], 'port': getattr(self.settings, target['ssh_port_setting']),
                            'namespace': target['namespace'], 'role': target['label'], 'ip': target['n3_address']})

        async def observe(target):
            prefix = 'ip netns exec ' + shlex.quote(target['namespace']) + ' ' if target['namespace'] else ''
            def command(value):
                return self._sudo_cmd(prefix + value) if prefix else value
            base = {**target, 'observed_at': datetime.now(timezone.utc).isoformat()}
            try:
                host, links, sockets = await asyncio.gather(*[
                    self._run(command(value), port=target['port'])
                    for value in ('hostname', 'ip -j address show', 'ss -H -lnutS')])
                payload = _runtime_payload('remote', host, links, sockets)
                for key in ('interfaces', 'listening_ports'):
                    payload[key] = [{**v, 'runtime_id': target['id'], 'namespace': target['namespace']} for v in payload[key]]
                return {**base, **payload, 'observation_status': 'observed'}
            except Exception as exc:
                return {**base, 'hostname': None, 'interfaces': [], 'listening_ports': [],
                        'observation_status': 'unavailable', 'error': type(exc).__name__}
        hosts = await asyncio.gather(*(observe(target) for target in targets))
        return {'source': 'remote', 'hostname': ' + '.join(h['hostname'] for h in hosts if h['hostname']),
                'hosts': hosts, 'interfaces': [i for h in hosts for i in h['interfaces']],
                'listening_ports': [p for h in hosts for p in h['listening_ports']],
                'complete': all(h['observation_status'] == 'observed' for h in hosts)}

    @staticmethod
    def _capture_paths(trace_id: str) -> tuple[str, str]:
        if not re.fullmatch(r"[0-9a-f]{32}", trace_id):
            raise ExecutionError("Identificador de captura inválido")
        base = f"/tmp/ems-captures/{trace_id}"
        return f"{base}.pcap", f"{base}.log"

    async def start_remote_capture(
        self,
        trace_id: str,
        interface: str,
        capture_filter: str,
        duration_seconds: int,
        max_kilobytes: int,
    ) -> dict:
        if interface not in self.settings.allowed_interfaces:
            raise ExecutionError("Interfaz no permitida")
        pcap_path, log_path = self._capture_paths(trace_id)
        command = (
            "mkdir -p /tmp/ems-captures; "
            f"nohup tshark -n -i {shlex.quote(interface)} "
            f"-a duration:{int(duration_seconds)} -a filesize:{int(max_kilobytes)} "
            f"-f {shlex.quote(capture_filter)} -w {shlex.quote(pcap_path)} "
            f"> {shlex.quote(log_path)} 2>&1 < /dev/null & echo $!"
        )
        output = await self._run(command)
        try:
            pid = int(output.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise ExecutionError(f"No se obtuvo el PID de tshark: {output.strip()}") from exc
        return {"pid": pid, "remote_path": pcap_path, "remote_log": log_path}

    async def remote_capture_state(self, trace_id: str, pid: int) -> dict:
        pcap_path, _ = self._capture_paths(trace_id)
        process_state, size = await asyncio.gather(
            self._run(f"ps -o stat=,args= -p {int(pid)} 2>/dev/null || true"),
            self._run(f"stat -c %s {shlex.quote(pcap_path)} 2>/dev/null || echo 0"),
        )
        state = process_state.strip()
        running = bool(state) and not state.startswith("Z") and "tshark" in state and trace_id in state
        return {"status": "running" if running else "completed", "size_bytes": int(size.strip() or 0)}

    async def stop_remote_capture(self, trace_id: str, pid: int) -> dict:
        self._capture_paths(trace_id)
        command_line = await self._run(f"ps -o args= -p {int(pid)} 2>/dev/null || true")
        if command_line.strip() and ("tshark" not in command_line or trace_id not in command_line):
            raise ExecutionError("El PID ya no pertenece a la tarea de captura")
        if command_line.strip():
            await self._run(f"kill -INT {int(pid)} 2>/dev/null || true")
        await asyncio.sleep(0.5)
        return await self.remote_capture_state(trace_id, pid)

    def _download_sync(self, remote_path: str, destination: Path) -> None:
        import paramiko
        client = paramiko.SSHClient()
        if self.settings.ssh_strict_host_key:
            client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            connect_kwargs = {
                "hostname": self.settings.testbed_host,
                "port": self.settings.ssh_port,
                "username": self.settings.ssh_user,
                "look_for_keys": bool(self.settings.ssh_key_path),
                "allow_agent": False,
                "timeout": 10,
            }
            if self.settings.ssh_key_path:
                connect_kwargs["key_filename"] = str(self.settings.ssh_key_path)
            if self.settings.ssh_password:
                connect_kwargs["password"] = self.settings.ssh_password
            client.connect(**connect_kwargs)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with client.open_sftp() as sftp:
                sftp.get(remote_path, str(destination))
        finally:
            client.close()

    async def fetch_remote_capture(self, trace_id: str, destination: Path) -> None:
        remote_path, _ = self._capture_paths(trace_id)
        await asyncio.to_thread(self._download_sync, remote_path, destination)

    async def summarize_remote_capture(self, trace_id: str) -> dict:
        remote_path, _ = self._capture_paths(trace_id)
        output, packet_count, size = await asyncio.gather(
            self._run(
                f"tshark -r {shlex.quote(remote_path)} -T fields -e _ws.col.Protocol 2>/dev/null "
                "| sort | uniq -c | sort -nr | head -20"
            ),
            self._run(
                f"tshark -r {shlex.quote(remote_path)} -T fields -e frame.number 2>/dev/null "
                "| tail -1"
            ),
            self._run(f"stat -c %s {shlex.quote(remote_path)} 2>/dev/null || echo 0"),
        )
        protocols = []
        for line in output.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2 and parts[0].isdigit():
                protocols.append({"protocol": parts[1], "packets": int(parts[0])})
        return {
            "packet_count": int(packet_count.strip() or 0),
            "size_bytes": int(size.strip() or 0),
            "protocols": protocols,
        }

    async def analyze_remote_capture(self, trace_id: str) -> tuple[str, str]:
        from app.services.trace_analysis import TSHARK_FIELDS

        remote_path, _ = self._capture_paths(trace_id)
        field_arguments = " ".join(f"-e {shlex.quote(field)}" for field in TSHARK_FIELDS)
        command = (
            f"tshark -n -r {shlex.quote(remote_path)} -d tcp.port==7777,http2 -d tcp.port==8081,http2 -d tcp.port==18081,http2 -T fields "
            "-E separator=/t -E quote=d -E occurrence=a -E aggregator=, "
            f"{field_arguments} 2>/dev/null"
        )
        output, version = await asyncio.gather(
            self._run(command),
            self._run("tshark --version | head -1"),
        )
        return output, version.strip()

    async def export_remote_filtered_capture(self, trace_id: str, frame_numbers: list[int]) -> str | None:
        remote_path, _ = self._capture_paths(trace_id)
        if not frame_numbers:
            return None
        safe_frames = sorted({int(frame) for frame in frame_numbers if 0 < int(frame) <= 10_000_000})
        if not safe_frames:
            return None
        filtered_path = f"/tmp/ems-captures/{trace_id}.filtered.pcap"
        display_filter = " or ".join(f"frame.number == {frame}" for frame in safe_frames)
        await self._run(
            f"tshark -n -r {shlex.quote(remote_path)} -Y {shlex.quote(display_filter)} "
            f"-w {shlex.quote(filtered_path)} 2>/dev/null"
        )
        return filtered_path

    async def fetch_remote_artifact(self, remote_path: str, destination: Path) -> None:
        if not re.fullmatch(r"/tmp/ems-captures/[0-9a-f]{32}(?:\.filtered)?\.pcap", remote_path):
            raise ExecutionError("Ruta de artefacto remoto inválida")
        await asyncio.to_thread(self._download_sync, remote_path, destination)

    async def delete_remote_capture(self, trace_id: str) -> None:
        pcap_path, log_path = self._capture_paths(trace_id)
        filtered_path = f"/tmp/ems-captures/{trace_id}.filtered.pcap"
        await self._run(
            f"rm -f -- {shlex.quote(pcap_path)} {shlex.quote(log_path)} {shlex.quote(filtered_path)}"
        )

    def _read_file_sync(self, remote_path: str, component_id: str | None = None) -> str:
        import paramiko
        client = paramiko.SSHClient()
        if self.settings.ssh_strict_host_key:
            client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            target_port = self._config_port(remote_path, component_id)
            connect_kwargs = {
                "hostname": self.settings.testbed_host,
                "port": target_port,
                "username": self.settings.ssh_user,
                "look_for_keys": bool(self.settings.ssh_key_path),
                "allow_agent": False,
                "timeout": 10,
            }
            if self.settings.ssh_key_path:
                connect_kwargs["key_filename"] = str(self.settings.ssh_key_path)
            if self.settings.ssh_password:
                connect_kwargs["password"] = self.settings.ssh_password
            client.connect(**connect_kwargs)
            with client.open_sftp() as sftp:
                stat = sftp.stat(remote_path)
                if stat.st_size > 1_000_000:
                    raise ExecutionError("El archivo de configuración excede 1 MB")
                with sftp.open(remote_path, "r") as remote_file:
                    data = remote_file.read()
            return data.decode("utf-8") if isinstance(data, bytes) else data
        except FileNotFoundError:
            raise
        finally:
            client.close()

    def _config_port(self, remote_path, component_id=None):
        profiles = json.loads(self.settings.profiles_path.read_text(encoding='utf-8'))
        candidates = [p for key, p in profiles.items()
                      if (key.startswith('5G:') or key.startswith('common:'))
                      and remote_path in p.get('config_paths', [])
                      and (component_id is None or key.split(':', 1)[1] == component_id)]
        ports = {getattr(self.settings, p.get('ssh_port_setting', 'ssh_port')) for p in candidates}
        if len(ports) != 1:
            raise ExecutionError('Configuración sin destino único declarado en el catálogo')
        return ports.pop()

    async def read_remote_file(self, remote_path: str, *, component_id: str | None = None) -> str:
        if not remote_path.startswith(("/etc/open5gs/", "/etc/mongod.conf", "/home/emsadmin/UERANSIM/config/")):
            raise ExecutionError("Ruta remota fuera de las ubicaciones permitidas")
        return await asyncio.to_thread(self._read_file_sync, remote_path, component_id)

    @staticmethod
    def _parse_kpi_text(output: str) -> dict:
        parts = output.split("===SEP===")
        if len(parts) < 3:
            raise ExecutionError("Formato de métricas remoto inválido")

        net_part = parts[0].strip()
        load_part = parts[1].strip()
        mem_part = parts[2].strip()

        interfaces = {}
        for line in net_part.splitlines():
            if ":" not in line:
                continue
            name, _, stats = line.partition(":")
            cols = stats.split()
            if len(cols) >= 9:
                interfaces[name.strip()] = {"rx_bytes": int(cols[0]), "tx_bytes": int(cols[8])}

        load_fields = load_part.split()
        load_1m = float(load_fields[0]) if load_fields else 0.0

        mem_percent = 0.0
        for line in mem_part.splitlines():
            if line.startswith("Mem:"):
                m_cols = line.split()
                if len(m_cols) >= 3:
                    total = float(m_cols[1])
                    used = float(m_cols[2])
                    if total > 0:
                        mem_percent = round((used / total) * 100, 1)

        return {
            "interfaces": interfaces,
            "load_1m": load_1m,
            "memory_percent": mem_percent,
        }

    async def remote_kpis(self) -> dict:
        cmd = "cat /proc/net/dev; echo '===SEP==='; cat /proc/loadavg; echo '===SEP==='; free -m"
        upf1_port = getattr(self.settings, "upf_ssh_port", 2223)
        upf2_port = getattr(self.settings, "upf2_ssh_port", 2224)
        gnb_port = getattr(self.settings, "gnb_ssh_port", 2225)
        ue_port = getattr(self.settings, "ue_ssh_port", 2226)
        try:
            core_out, u1_out, u2_out, gnb_out, ue_out = await asyncio.gather(
                self._run(cmd, port=self.settings.ssh_port),
                self._run(cmd, port=upf1_port),
                self._run(cmd, port=upf2_port),
                self._run(cmd, port=gnb_port),
                self._run(cmd, port=ue_port),
            )
            core_kpis = self._parse_kpi_text(core_out)
            u1_kpis = self._parse_kpi_text(u1_out)
            u2_kpis = self._parse_kpi_text(u2_out)
            gnb_kpis = self._parse_kpi_text(gnb_out)
            ue_kpis = self._parse_kpi_text(ue_out)

            merged_interfaces = dict(core_kpis["interfaces"])
            if "ogstun" in u1_kpis["interfaces"]:
                merged_interfaces["ogstun"] = u1_kpis["interfaces"]["ogstun"]
            if "ogstun" in u2_kpis["interfaces"]:
                merged_interfaces["ogstun_corp"] = u2_kpis["interfaces"]["ogstun"]
            if "enp0s8" in u1_kpis["interfaces"]:
                merged_interfaces["enp0s8_upf1"] = u1_kpis["interfaces"]["enp0s8"]
            if "enp0s8" in u2_kpis["interfaces"]:
                merged_interfaces["enp0s8_upf2"] = u2_kpis["interfaces"]["enp0s8"]
            if "enp0s8" in gnb_kpis["interfaces"]:
                merged_interfaces["enp0s8_gnb"] = gnb_kpis["interfaces"]["enp0s8"]
            if "uesimtun0" in ue_kpis["interfaces"]:
                merged_interfaces["uesimtun0"] = ue_kpis["interfaces"]["uesimtun0"]
            if "uesimtun1" in ue_kpis["interfaces"]:
                merged_interfaces["uesimtun1"] = ue_kpis["interfaces"]["uesimtun1"]

            active_kpis = [core_kpis, u1_kpis, u2_kpis, gnb_kpis, ue_kpis]
            avg_load = round(sum(k["load_1m"] for k in active_kpis) / len(active_kpis), 2)
            avg_mem = round(sum(k["memory_percent"] for k in active_kpis) / len(active_kpis), 1)

            return {
                "interfaces": merged_interfaces,
                "load_1m": avg_load,
                "memory_percent": avg_mem,
            }
        except Exception:
            output = await self._run(cmd, port=self.settings.ssh_port)
            return self._parse_kpi_text(output)

    async def set_ip_forward(self, enable: bool) -> None:
        val = "1" if enable else "0"
        upf1_port = getattr(self.settings, "upf_ssh_port", 2223)
        upf2_port = getattr(self.settings, "upf2_ssh_port", 2224)
        cmd = self._sudo_cmd(f"sysctl -w net.ipv4.ip_forward={val}")
        await asyncio.gather(
            self._run(cmd, port=upf1_port),
            self._run(cmd, port=upf2_port),
            self._run(cmd, port=self.settings.ssh_port),
            return_exceptions=True,
        )

    async def get_ip_forward(self) -> bool:
        upf_port = getattr(self.settings, "upf_ssh_port", 2223)
        cmd = self._sudo_cmd("cat /proc/sys/net/ipv4/ip_forward")
        try:
            out = await self._run(cmd, port=upf_port)
            return out.strip() == "1"
        except Exception:
            out = await self._run(cmd, port=self.settings.ssh_port)
            return out.strip() == "1"

    async def generate_test_traffic(self, count: int = 3) -> dict:
        ue_port = getattr(self.settings, "ue_ssh_port", 2226)
        cmd = f"ping -c {int(count)} -I uesimtun0 -W 2 8.8.8.8"
        try:
            output = await self._run(cmd, port=ue_port)
            return {"output": output, "success": "bytes from" in output}
        except Exception:
            fallback_cmd = self._sudo_cmd(
                f"ip netns exec ueransim-999700000000001-internet-psi1 ping -c {int(count)} -W 2 8.8.8.8"
            )
            output = await self._run(fallback_cmd, port=self.settings.ssh_port)
            return {"output": output, "success": "bytes from" in output}

    async def _remote_nr_cli(self, port: int | None = None) -> str:
        target_port = port or getattr(self.settings, "ue_ssh_port", 2226)
        output = await self._run(
            "for p in \"$(command -v nr-cli 2>/dev/null)\" \"$HOME/UERANSIM/build/nr-cli\" /usr/local/bin/nr-cli; "
            "do if [ -n \"$p\" ] && [ -x \"$p\" ]; then printf '%s' \"$p\"; exit 0; fi; done; exit 127",
            port=target_port,
        )
        path = output.strip()
        if not path.startswith("/") or not re.fullmatch(r"[A-Za-z0-9_./-]+", path):
            raise ExecutionError("Ruta de nr-cli inválida")
        return path

    async def native_operation(
        self, operation: str, component: str, parameters: dict
    ) -> dict:
        if operation == "ueransim-nodes":
            ue_port = getattr(self.settings, "ue_ssh_port", 2226)
            gnb_port = getattr(self.settings, "gnb_ssh_port", 2225)
            nodes = []
            try:
                cli_ue = await self._remote_nr_cli(ue_port)
                out_ue = await self._run(f"{shlex.quote(cli_ue)} --dump", port=ue_port)
                nodes.extend(_parse_ueransim_nodes(out_ue))
            except Exception:
                pass
            try:
                cli_gnb = await self._remote_nr_cli(gnb_port)
                out_gnb = await self._run(f"{shlex.quote(cli_gnb)} --dump", port=gnb_port)
                nodes.extend(_parse_ueransim_nodes(out_gnb))
            except Exception:
                pass
            return {"output": "\n".join(nodes), "data": {"nodes": nodes}}
        if operation == "ueransim-cli":
            target_port = getattr(self.settings, "ue_ssh_port", 2226) if "ue" in component.lower() else getattr(self.settings, "gnb_ssh_port", 2225)
            cli = await self._remote_nr_cli(target_port)
            dumped = await self._run(f"{shlex.quote(cli)} --dump", port=target_port)
            nodes = _parse_ueransim_nodes(dumped)
            node = _select_ueransim_node(nodes, component, parameters.get("node_name"))
            raw_command = str(parameters.get("command", ""))
            if component == "gnb" and raw_command == "amf-info" and not parameters.get("amf_id"):
                try:
                    list_out = await self._run(
                        f"{shlex.quote(cli)} {shlex.quote(node)} --exec amf-list",
                        port=target_port,
                    )
                    amf_ids = re.findall(r"id:\s*(\d+)", list_out)
                except Exception:
                    amf_ids = []
                if not amf_ids:
                    amf_ids = ["0"]
                outputs = []
                for aid in amf_ids:
                    sub_out = await self._run(
                        f"{shlex.quote(cli)} {shlex.quote(node)} --exec {shlex.quote(f'amf-info {aid}')}",
                        port=target_port,
                    )
                    outputs.append(sub_out.strip())
                output = "\n\n".join(outputs)
                return {"output": output, "data": _key_value_payload(output)}
            command = _validate_ueransim_command(component, parameters)
            output = await self._run(
                f"{shlex.quote(cli)} {shlex.quote(node)} --exec {shlex.quote(command)}",
                port=target_port,
            )
            return {"output": output, "data": _key_value_payload(output)}
        if operation == "open5gs-info":
            endpoint = _validate_info_endpoint(component, parameters.get("endpoint"))
            address, port = _info_address(component)
            url = f"http://{address}:{port}/{endpoint}?page=0&page_size=100"
            try:
                output = await self._run(
                    f"curl --silent --show-error --fail --max-time 5 {shlex.quote(url)}"
                )
            except Exception as exc:
                raise ExecutionError(
                    "InfoAPI no disponible. Open5GS 2.7.x de repositorio no la incluye; "
                    "use una compilaciÃ³n reciente o consulte logs/KPI. " + str(exc)
                ) from exc
            return _json_operation_payload(output)
        if operation == "software-version":
            binary = _component_binary(component)
            output = await self._run(f"{shlex.quote(binary)} -v 2>&1 | head -5")
            return {"output": output, "data": {"version": output.strip()}}
        raise ExecutionError(f"OperaciÃ³n nativa no soportada: {operation}")


_GNB_COMMANDS = {"status", "info", "amf-list", "amf-info", "ue-count", "ue-list"}
_UE_COMMANDS = {"status", "info", "coverage", "timers", "rls-state", "ps-list"}


def _parse_ueransim_nodes(output: str) -> list[str]:
    return [
        line.strip()
        for line in output.splitlines()
        if re.fullmatch(r"[A-Za-z0-9._:-]+", line.strip())
    ]


def _select_ueransim_node(nodes: list[str], component: str, requested: str | None) -> str:
    eligible = [node for node in nodes if (node.lower().startswith("imsi-") == (component == "ue"))]
    if requested:
        if requested not in eligible:
            raise ExecutionError("El nodo UERANSIM indicado no existe o no corresponde al componente")
        return requested
    if not eligible:
        raise ExecutionError(f"No hay nodos UERANSIM activos para {component}")
    return eligible[0]


def _validate_ueransim_command(component: str, parameters: dict) -> str:
    command = str(parameters.get("command", ""))
    if component == "gnb" and command in _GNB_COMMANDS:
        if command == "amf-info":
            amf_id = parameters.get("amf_id")
            if amf_id is not None and str(amf_id).strip():
                return f"amf-info {amf_id}"
        return command
    if component == "ue" and command in _UE_COMMANDS:
        return command
    if component == "ue" and command == "ps-release":
        psi = parameters.get("psi")
        if not isinstance(psi, int) or not 1 <= psi <= 15:
            raise ExecutionError("PSI debe estar entre 1 y 15")
        return f"ps-release {psi}"
    if component == "ue" and command == "deregister":
        return "deregister switch-off"
    raise ExecutionError("Comando UERANSIM fuera del catÃ¡logo permitido")


def _validate_info_endpoint(component: str, endpoint: str | None) -> str:
    allowed = {
        "amf": {"ue-info", "gnb-info"},
        "smf": {"pdu-info"},
        "smf2": {"pdu-info"},
        "smf3": {"pdu-info"},
        "mme": {"ue-info", "enb-info"},
    }
    if endpoint not in allowed.get(component, set()):
        raise ExecutionError("Endpoint InfoAPI no permitido para esta funciÃ³n de red")
    return str(endpoint)


def _component_binary(component: str) -> str:
    if component in ("smf2", "smf3"):
        return "/home/emsadmin/maestro-charging/open5gs/build/src/smf/open5gs-smfd"
    if component in {"gnb", "ue"}:
        return "nr-gnb" if component == "gnb" else "nr-ue"
    if not re.fullmatch(r"[a-z0-9]+", component):
        raise ExecutionError("Componente invÃ¡lido")
    return f"open5gs-{component}d"


def _key_value_payload(output: str) -> dict:
    values = {}
    for line in output.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() and len(key.strip()) <= 80:
            values[key.strip()] = value.strip()
    return values


def _json_operation_payload(output: str) -> dict:
    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ExecutionError("InfoAPI devolviÃ³ una respuesta que no es JSON") from exc
    return {"output": json.dumps(data, indent=2, ensure_ascii=False), "data": data}


def _info_address(component: str) -> tuple[str, int]:
    if component == "smf2":
        return "127.0.0.15", 9091
    if component == "smf3":
        return "10.210.50.18", 9092
    return {"amf": "127.0.0.5", "smf": "127.0.0.4", "mme": "127.0.0.2"}[component], get_settings().open5gs_info_port


def _local_open5gs_info(component: str, parameters: dict) -> dict:
    from urllib.error import URLError
    from urllib.request import urlopen

    endpoint = _validate_info_endpoint(component, parameters.get("endpoint"))
    address, port = _info_address(component)
    url = f"http://{address}:{port}/{endpoint}?page=0&page_size=100"
    try:
        with urlopen(url, timeout=5) as response:
            output = response.read(get_settings().operation_output_limit + 1).decode("utf-8", errors="replace")
    except (OSError, URLError) as exc:
        raise ExecutionError(f"InfoAPI no disponible en {component.upper()}: {exc}") from exc
    return _json_operation_payload(output)


def _runtime_payload(source: str, hostname: str, interfaces: str, sockets: str) -> dict:
    try:
        raw_interfaces = json.loads(interfaces)
    except json.JSONDecodeError as exc:
        raise ExecutionError("La salida de 'ip -j address show' no es JSON válido") from exc

    normalized_interfaces = []
    for item in raw_interfaces:
        addresses = [
            {
                "family": address.get("family"),
                "address": address.get("local"),
                "prefix_length": address.get("prefixlen"),
                "scope": address.get("scope"),
            }
            for address in item.get("addr_info", [])
        ]
        normalized_interfaces.append(
            {
                "name": item.get("ifname"),
                "state": item.get("operstate", "UNKNOWN").lower(),
                "mtu": item.get("mtu"),
                "addresses": addresses,
            }
        )

    listening_ports = []
    for line in sockets.splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        local = fields[4]
        host, separator, port = local.rpartition(":")
        if not separator or not port.isdigit():
            continue
        listening_ports.append(
            {"protocol": fields[0], "address": host.strip("[]"), "port": int(port)}
        )

    return {
        "source": source,
        "hostname": hostname.strip(),
        "interfaces": normalized_interfaces,
        "listening_ports": listening_ports,
    }


def _parse_service_states(output: str, requested_units: list[str]) -> dict[str, str]:
    states = {unit: "unknown" for unit in requested_units}
    current_id = None
    for line in output.splitlines():
        if line.startswith("Id="):
            current_id = line.removeprefix("Id=")
        elif line.startswith("ActiveState=") and current_id:
            active_state = line.removeprefix("ActiveState=")
            normalized_id = current_id.removesuffix(".service")
            states[normalized_id] = {
                "active": "running",
                "activating": "degraded",
                "deactivating": "degraded",
                "failed": "stopped",
                "inactive": "stopped",
            }.get(active_state, "unknown")
            current_id = None
    return states


def build_adapter(allowed_units: set[str]) -> ExecutionAdapter:
    mode = get_settings().execution_mode
    if mode == "local":
        return LocalExecutionAdapter(allowed_units)
    if mode == "remote":
        return RemoteExecutionAdapter(allowed_units)
    return SimulatedExecutionAdapter()
