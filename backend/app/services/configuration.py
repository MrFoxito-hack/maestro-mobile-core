import asyncio
import difflib
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.core.config import get_settings
from app.services.execution import RemoteExecutionAdapter
from app.services.scenarios import CATALOG, scenario_manager


class ConfigurationError(ValueError):
    pass


SECRET_KEYS = {"key", "k", "op", "opc", "password", "passwd", "secret", "token", "privatekey", "private_key"}


def _is_secret_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    compact = normalized.replace("_", "")
    return normalized in SECRET_KEYS or compact in SECRET_KEYS or any(word in normalized for word in ("password", "secret", "token"))


def _redact(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict):
        result = {}
        count = 0
        for key, child in value.items():
            if _is_secret_key(key):
                result[key] = "[REDACTED]"
                count += 1
            else:
                result[key], child_count = _redact(child)
                count += child_count
        return result, count
    if isinstance(value, list):
        result = []
        count = 0
        for child in value:
            redacted, child_count = _redact(child)
            result.append(redacted)
            count += child_count
        return result, count
    return value, 0


def _extract_values(value: Any, aliases: set[str]) -> set[Any]:
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower().replace("-", "_") in aliases:
                if isinstance(child, (str, int, float)):
                    found.add(child)
                elif isinstance(child, list):
                    found.update(item for item in child if isinstance(item, (str, int, float)))
            found.update(_extract_values(child, aliases))
    elif isinstance(value, list):
        for child in value:
            found.update(_extract_values(child, aliases))
    return found


def _normalize_parameter(name: str, value: Any) -> str:
    if name == "sd":
        if isinstance(value, int):
            return f"{value:06x}"
        raw = str(value).lower().removeprefix("0x")
        return raw.zfill(6)
    return str(value).lower()


def _simulated_config_content(scenario_id: str, component_id: str) -> str:
    scenario = CATALOG.get(scenario_id, {})
    defaults = scenario.get("defaults", {"mcc": "999", "mnc": "70", "tac": 1, "apn_dnn": "internet", "sst": 1, "sd": "000001"})
    mcc = defaults.get("mcc", "999")
    mnc = defaults.get("mnc", "70")
    tac = defaults.get("tac", 1)
    dnn = defaults.get("apn_dnn", "internet")
    sst = defaults.get("sst", 1)
    sd = defaults.get("sd", "000001")

    if component_id == "amf":
        return f"""amf:
  sbi:
    server:
      - address: 127.0.0.5
        port: 7777
  guami:
    - plmn_id:
        mcc: "{mcc}"
        mnc: "{mnc}"
  tai:
    - plmn_id:
        mcc: "{mcc}"
        mnc: "{mnc}"
      tac: {tac}
  plmn_support:
    - plmn_id:
        mcc: "{mcc}"
        mnc: "{mnc}"
      s_nssai:
        - sst: {sst}
          sd: "{sd}"
"""
    if component_id == "smf":
        return f"""smf:
  sbi:
    server:
      - address: 127.0.0.4
        port: 7777
  pfcp:
    server:
      - address: 127.0.0.4
  subnet:
    - dnn: "{dnn}"
      s_nssai:
        sst: {sst}
        sd: "{sd}"
"""
    if component_id == "upf":
        return f"""upf:
  pfcp:
    server:
      - address: 127.0.0.7
  gtpu:
    server:
      - address: 127.0.0.7
  subnet:
    - dnn: "{dnn}"
"""
    if component_id == "gnb":
        return f"""mcc: "{mcc}"
mnc: "{mnc}"
tac: {tac}
slices:
  - sst: {sst}
    sd: "{sd}"
"""
    if component_id == "ue":
        return f"""supi: "imsi-{mcc}{mnc}0000000001"
mcc: "{mcc}"
mnc: "{mnc}"
key: "00112233445566778899AABBCCDDEEFF"
opc: "FFEEDDCCBBAA99887766554433221100"
dnn: "{dnn}"
sessions:
  - apn: "{dnn}"
    slice:
      sst: {sst}
      sd: "{sd}"
"""
    return f"""{component_id}:
  enabled: true
  mcc: "{mcc}"
  mnc: "{mnc}"
  tac: {tac}
"""


class ConfigurationService:
    # Operaciones locales heredadas, utilizadas por pruebas y modo agente.
    def resolve(self, relative_path: str) -> Path:
        if Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
            raise ConfigurationError("La ruta debe ser relativa y no puede contener '..'")
        roots = get_settings().allowed_config_roots
        target = (roots[0] / relative_path).resolve()
        if not any(target.is_relative_to(root.resolve()) for root in roots):
            raise ConfigurationError("Ruta fuera de los directorios permitidos")
        if target.suffix not in {".yaml", ".yml"}:
            raise ConfigurationError("Solo se permiten archivos YAML")
        return target

    def read(self, relative_path: str) -> str:
        target = self.resolve(relative_path)
        if not target.exists():
            raise FileNotFoundError(relative_path)
        return target.read_text(encoding="utf-8")

    def validate(self, content: str) -> dict:
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"YAML inválido: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ConfigurationError("La raíz del YAML debe ser un objeto")
        return parsed

    def diff(self, relative_path: str, content: str) -> str:
        old = self.read(relative_path) if self.resolve(relative_path).exists() else ""
        return "".join(difflib.unified_diff(old.splitlines(True), content.splitlines(True), fromfile="actual", tofile="propuesto"))

    def write(self, relative_path: str, content: str) -> dict:
        self.validate(content)
        target = self.resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if target.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = get_settings().backup_dir / f"{target.name}.{stamp}.bak"
            shutil.copy2(target, backup)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
        return {"path": relative_path, "backup": backup.name if backup else None}

    def restore(self, relative_path: str) -> dict:
        target = self.resolve(relative_path)
        backups = sorted(get_settings().backup_dir.glob(f"{target.name}.*.bak"), reverse=True)
        if not backups:
            raise FileNotFoundError("No existe respaldo")
        shutil.copy2(backups[0], target)
        return {"path": relative_path, "restored_from": backups[0].name}

    def catalog(self, scenario_id: str) -> list[dict]:
        if scenario_id not in CATALOG:
            raise KeyError(scenario_id)
        files = []
        for component in CATALOG[scenario_id]["components"]:
            for path in component.get("config_paths", []):
                if Path(path).suffix in {".yaml", ".yml", ".conf"}:
                    files.append({"component_id": component["id"], "component": component["label"], "path": path})
        return files

    def _allowed_path(self, scenario_id: str, component_id: str, path: str) -> None:
        component = next((item for item in CATALOG[scenario_id]["components"] if item["id"] == component_id), None)
        if not component or path not in component.get("config_paths", []):
            raise ConfigurationError("Archivo no declarado para la función de red")

    async def _read_declared(self, scenario_id: str, component_id: str, path: str) -> str:
        if scenario_id not in CATALOG:
            raise KeyError(scenario_id)
        self._allowed_path(scenario_id, component_id, path)
        adapter = scenario_manager.adapter
        if isinstance(adapter, RemoteExecutionAdapter):
            return await adapter.read_remote_file(path, component_id=component_id)
        if get_settings().execution_mode == "local":
            return Path(path).read_text(encoding="utf-8")
        if get_settings().execution_mode == "simulated":
            return _simulated_config_content(scenario_id, component_id)
        raise ConfigurationError("La lectura declarativa requiere modo local, remoto o simulado")

    async def read_declared(self, scenario_id: str, component_id: str, path: str) -> dict:
        content = await self._read_declared(scenario_id, component_id, path)
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"YAML inválido en la VM: {exc}") from exc
        redacted, count = _redact(parsed)
        safe_content = yaml.safe_dump(redacted, sort_keys=False, allow_unicode=True)
        return {
            "scenario_id": scenario_id,
            "component_id": component_id,
            "path": path,
            "content": safe_content,
            "redacted_fields": count,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "read_only": True,
        }

    async def validate_scenario(self, scenario_id: str) -> dict:
        if scenario_id not in CATALOG:
            raise KeyError(scenario_id)
        scenario = CATALOG[scenario_id]
        yaml_files = [item for item in self.catalog(scenario_id) if Path(item["path"]).suffix in {".yaml", ".yml"}]
        jobs = [self._read_declared(scenario_id, item["component_id"], item["path"]) for item in yaml_files]
        results = await asyncio.gather(*jobs, return_exceptions=True)
        parsed_by_component: dict[str, dict] = {}
        checks = []
        for item, result in zip(yaml_files, results):
            if isinstance(result, Exception):
                checks.append({"id": f"file:{item['component_id']}", "category": "file", "status": "error", "title": f"No se pudo leer {item['path']}", "evidence": str(result), "components": [item["component_id"]]})
                continue
            try:
                parsed = yaml.safe_load(result)
                if isinstance(parsed, dict):
                    parsed_by_component[item["component_id"]] = parsed
            except yaml.YAMLError as exc:
                checks.append({"id": f"yaml:{item['component_id']}", "category": "syntax", "status": "error", "title": f"YAML inválido en {item['path']}", "evidence": str(exc), "components": [item["component_id"]]})

        expected = scenario["defaults"]
        aliases = {"mcc": {"mcc"}, "mnc": {"mnc"}, "tac": {"tac"}, "apn_dnn": {"dnn", "apn", "apn_dnn"}, "sst": {"sst"}, "sd": {"sd"}}
        required = {
            "5G": {"mcc": ["amf", "gnb", "ue"], "mnc": ["amf", "gnb", "ue"], "tac": ["amf", "gnb"], "apn_dnn": ["smf", "ue"], "sst": ["amf", "smf", "gnb", "ue"], "sd": ["amf", "smf", "gnb", "ue"]},
            "4G": {"mcc": ["mme", "enb", "ue"], "mnc": ["mme", "enb", "ue"], "tac": ["mme", "enb"], "apn_dnn": ["smf", "ue"]},
        }[scenario["technology"]]
        observed_summary = {}
        for parameter, components in required.items():
            expected_value = _normalize_parameter(parameter, expected.get(parameter))
            evidence_parts = []
            missing = []
            mismatched = []
            all_values = set()
            for component_id in components:
                parsed = parsed_by_component.get(component_id)
                values = {_normalize_parameter(parameter, value) for value in _extract_values(parsed, aliases[parameter])} if parsed else set()
                all_values.update(values)
                evidence_parts.append(f"{component_id}={','.join(sorted(values)) if values else 'no encontrado'}")
                if not values:
                    missing.append(component_id)
                elif expected_value not in values:
                    mismatched.append(component_id)
            observed_summary[parameter] = sorted(all_values)
            status = "error" if mismatched else "warning" if missing else "pass"
            checks.append({
                "id": f"parameter:{parameter}",
                "category": "telco-parameter",
                "status": status,
                "title": f"{parameter.upper()} {'coincide' if status == 'pass' else 'requiere revisión'}",
                "evidence": "; ".join(evidence_parts) + f"; esperado={expected_value}",
                "components": components,
            })

        status = await scenario_manager.status(scenario_id)
        runtime = await scenario_manager.adapter.runtime_snapshot()
        observed_endpoints = {(item["protocol"], item["address"], item["port"]) for item in runtime["listening_ports"]}
        for component in status.components:
            for endpoint in component.expected_endpoints:
                key = (endpoint["protocol"], endpoint["address"], endpoint["port"])
                ok = key in observed_endpoints
                checks.append({"id": f"endpoint:{component.id}:{endpoint['protocol']}:{endpoint['port']}", "category": "endpoint", "status": "pass" if ok else "error", "title": f"{component.label} {endpoint['interface']} {'escuchando' if ok else 'no disponible'}", "evidence": f"{endpoint['protocol']}://{endpoint['address']}:{endpoint['port']}", "components": [component.id]})

        expected_yaml = yaml.safe_dump(expected, sort_keys=True, allow_unicode=True)
        observed_yaml = yaml.safe_dump(observed_summary, sort_keys=True, allow_unicode=True)
        baseline_diff = "".join(difflib.unified_diff(expected_yaml.splitlines(True), observed_yaml.splitlines(True), fromfile="baseline-esperado", tofile="valores-observados"))
        return {
            "scenario_id": scenario_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {"pass": sum(item["status"] == "pass" for item in checks), "warning": sum(item["status"] == "warning" for item in checks), "error": sum(item["status"] == "error" for item in checks)},
            "checks": checks,
            "baseline_diff": baseline_diff,
        }


configuration_service = ConfigurationService()
