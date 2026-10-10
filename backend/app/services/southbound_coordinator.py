"""Southbound Coordinator for MAEstro 5G SA EMS.

Coordinates all Southbound (SBI) interactions with Network Elements (NFs, UPFs, VM hosts):
- Concurrency control and anti-overlapping locks.
- Decouples NRM Inventory synchronization from PM metrics collection.
- Robust exception handling and failure tracking without crashing caller loops.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services.nrm_state import ems_state_store
from app.services.scenarios import CATALOG, scenario_manager

logger = logging.getLogger("ems.southbound")


class SouthboundCoordinator:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sync_locks = {scenario_id: asyncio.Lock() for scenario_id in CATALOG}

    async def sync_nrm_inventory(self, scenario_id: str) -> bool:
        """Performs a background inventory and status synchronization for a scenario.

        Catches timeouts and network exceptions gracefully to update reachability state machine.
        """
        if scenario_id not in self._sync_locks:
            return False

        async with self._sync_locks[scenario_id]:
            try:
                # 1. Probe component service statuses
                status = await scenario_manager.status(scenario_id)

                # 2. Probe host runtime & network interfaces
                runtime = None
                try:
                    runtime = await scenario_manager.adapter.runtime_snapshot()
                except Exception as exc:
                    logger.warning("Southbound runtime snapshot degraded for %s: %s", scenario_id, exc)

                # 3. Update in-memory state store
                ems_state_store.update_from_southbound(
                    scenario_id=scenario_id,
                    components_status=status.components if status else None,
                    runtime_snapshot=runtime,
                )
                return True
            except Exception as exc:
                logger.error("Southbound NRM sync failed for %s: %s", scenario_id, exc)
                ems_state_store.update_from_southbound(
                    scenario_id=scenario_id,
                    probe_error=f"{type(exc).__name__}: {exc}",
                )
                return False


southbound_coordinator = SouthboundCoordinator()
