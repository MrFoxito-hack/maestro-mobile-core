import asyncio

import pytest

from app.laboratory.preflight import CORE_UNITS, REQUIRED_GATES, CoreServicesPreflight, configured_core_observer


def test_live_observer_rejects_simulated_configuration():
    with pytest.raises(ValueError, match="no admite datos simulados"):
        configured_core_observer()


def test_service_observer_uses_fixed_read_only_catalog_and_never_authorizes_campaign():
    class Reader:
        async def service_statuses(self, units):
            assert tuple(units) == CORE_UNITS
            return {**dict.fromkeys(units, "running"), "unrequested-secret": "do not expose"}
    report = asyncio.run(CoreServicesPreflight(Reader()).observe())
    assert report["execution_ready"] is False
    assert report["network_measurements"] is False
    assert report["pending_gates"] == list(REQUIRED_GATES)
    assert len(report["services"]) == len(CORE_UNITS)
    assert all(c["status"] == "observed" for c in report["services"])
    assert "unrequested-secret" not in str(report)


@pytest.mark.parametrize("behavior,code", [("timeout", "service_observation_timeout"), ("error", "service_observation_failed"), ("missing", None)])
def test_preflight_unavailable_readings_remain_unknown_and_redacted(behavior, code):
    class Reader:
        async def service_statuses(self, units):
            if behavior == "timeout":
                await asyncio.sleep(1)
            if behavior == "error":
                raise RuntimeError("SSH password=secret")
            return {units[0]: "password=secret"}
    report = asyncio.run(CoreServicesPreflight(Reader(), timeout_seconds=0.01).observe())
    assert report["error_code"] == code
    assert all(c["state"] == "unknown" for c in report["services"])
    assert report["execution_ready"] is False
    assert "secret" not in str(report)
