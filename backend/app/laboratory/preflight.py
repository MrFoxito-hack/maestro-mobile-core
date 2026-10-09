"""First real observer, deliberately NOT connected to campaign execution or API.

Only reads fixed systemd service states through the existing EMS SSH adapter.
This is infrastructure evidence, never session/routing/enforcement verification.
Do not give students access until a real assignment and resource mapping exist.
"""

import asyncio
from typing import Protocol

from app.core.config import get_settings
from app.laboratory.repository import stamp

CORE_UNITS = (
    "open5gs-amfd", "open5gs-smfd", "open5gs-smfd2", "open5gs-smfd3", "open5gs-pcfd",
    "open5gs-nssfd", "maestro-nwdaf",
)
# Health observations outside eMBB must not veto its campaign.
EMBB_CRITICAL_UNITS = tuple(u for u in CORE_UNITS if u not in ('open5gs-smfd2', 'open5gs-smfd3'))
REQUIRED_GATES = (
    "assigned_session_identity", "ue_upf_dn_path", "shared_resource_calibration",
    "charging_budget_including_preparation", "baseline_policy_and_inflight_actions",
    "receiver_measurement_and_capture_capacity", "clock_and_counter_continuity",
    "remote_fencing", "independent_remote_recovery", "verified_recovery_predicates",
)


class ServiceReader(Protocol):
    async def service_statuses(self, units: list[str]) -> dict[str, str]: ...


class CoreServicesPreflight:
    """Read-only prototype. Transport injection is for isolated software tests."""

    def __init__(self, reader: ServiceReader, timeout_seconds: float = 20):
        self.reader = reader
        self.timeout_seconds = timeout_seconds

    async def observe(self) -> dict:
        started = stamp()
        error = None
        try:
            states = await asyncio.wait_for(
                self.reader.service_statuses(list(CORE_UNITS)), timeout=self.timeout_seconds,
            )
        except TimeoutError:
            states, error = {}, "service_observation_timeout"
        except Exception:
            # Never serialize raw SSH exceptions, host credentials or logs.
            states, error = {}, "service_observation_failed"
        checks = []
        for unit in CORE_UNITS:
            raw = states.get(unit, "unknown")
            state = raw if raw in ("running", "stopped", "degraded", "unknown") else "unknown"
            checks.append({"unit": unit, "state": state,
                           "status": "observed" if state != "unknown" else "not_verified"})
        return {
            "schema_version": 1, "source": "ems_ssh_service_observer",
            "scope": "core_systemd_services_only", "started_at": started, "finished_at": stamp(),
            "read_only": True, "network_measurements": False, "execution_ready": False,
            "error_code": error, "services": checks,
            "pending_gates": list(REQUIRED_GATES),
            "limitations": [
                "Un servicio activo no demuestra sesión, ruta, cuota ni enforcement.",
                "Fencing y recuperación SQLite no acreditan protección del Core.",
                "El timeout es de la observación; una lectura SSH en curso puede continuar en el transporte EMS.",
            ],
        }


def configured_core_observer() -> CoreServicesPreflight:
    """No simulated fallback. Construction opens no connection; observe() reads SSH."""
    if get_settings().execution_mode != "remote":
        raise ValueError("La observación real requiere EMS_EXECUTION_MODE=remote; no admite datos simulados.")
    from app.services.execution import RemoteExecutionAdapter

    return CoreServicesPreflight(RemoteExecutionAdapter(set(CORE_UNITS)))
