import asyncio
import json
from datetime import datetime, timezone

from app.db import transaction
from app.models import ComponentStatus, ScenarioAction, ScenarioState, ScenarioStatus
from app.services.catalog import load_catalog
from app.services.execution import ExecutionAdapter, build_adapter


CATALOG = load_catalog()


class ScenarioManager:
    def __init__(self) -> None:
        units = {
            component["unit"]
            for scenario in CATALOG.values()
            for component in scenario["components"]
        }
        self.adapter: ExecutionAdapter = build_adapter(units)
        self.locks = {scenario_id: asyncio.Lock() for scenario_id in CATALOG}

    def list_catalog(self) -> list[dict]:
        return [{"id": key, **value} for key, value in CATALOG.items()]

    def _persist(
        self,
        scenario_id: str,
        state: ScenarioState,
        parameters: dict,
        message: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with transaction() as conn:
            conn.execute(
                "INSERT INTO scenario_states VALUES(?,?,?,?,?) "
                "ON CONFLICT(scenario_id) DO UPDATE SET "
                "state=excluded.state, parameters=excluded.parameters, "
                "message=excluded.message, updated_at=excluded.updated_at",
                (scenario_id, state.value, json.dumps(parameters), message, now),
            )

    async def status(self, scenario_id: str) -> ScenarioStatus:
        scenario = CATALOG[scenario_id]
        units = [item["unit"] for item in scenario["components"]]
        statuses = await self.adapter.service_statuses(units)
        components = []
        for item in scenario["components"]:
            components.append(
                ComponentStatus(
                    id=item["id"],
                    label=item["label"],
                    kind=item["kind"],
                    node_id=item["node_id"],
                    interfaces=item.get("interfaces", []),
                    status=statuses[item["unit"]],
                    unit=item["unit"],
                    depends_on=item.get("depends_on", []),
                    expected_endpoints=item.get("expected_endpoints", []),
                    config_paths=item.get("config_paths", []),
                    procedures=item.get("procedures", []),
                )
            )
        running = sum(item.status == "running" for item in components)
        if running == len(components):
            state = ScenarioState.running
        elif running == 0:
            state = ScenarioState.stopped
        else:
            state = ScenarioState.degraded
        return ScenarioStatus(
            scenario_id=scenario_id,
            state=state,
            components=components,
            updated_at=datetime.now(timezone.utc),
        )

    async def start(self, scenario_id: str, action: ScenarioAction) -> ScenarioStatus:
        async with self.locks[scenario_id]:
            self._persist(scenario_id, ScenarioState.starting, action.parameters.model_dump())
            started = []
            try:
                for component in CATALOG[scenario_id]["components"]:
                    await self.adapter.start_service(component["unit"])
                    started.append(component)
                self._persist(scenario_id, ScenarioState.running, action.parameters.model_dump())
            except Exception as exc:
                for component in reversed(started):
                    await self.adapter.stop_service(component["unit"])
                self._persist(
                    scenario_id,
                    ScenarioState.failed,
                    action.parameters.model_dump(),
                    str(exc),
                )
                raise
            return await self.status(scenario_id)

    async def stop(self, scenario_id: str) -> ScenarioStatus:
        async with self.locks[scenario_id]:
            self._persist(scenario_id, ScenarioState.stopping, {})
            for component in reversed(CATALOG[scenario_id]["components"]):
                await self.adapter.stop_service(component["unit"])
            self._persist(scenario_id, ScenarioState.stopped, {})
            return await self.status(scenario_id)

    def component(self, scenario_id: str, component_id: str) -> dict:
        component = next(
            (item for item in CATALOG[scenario_id]["components"] if item["id"] == component_id),
            None,
        )
        if not component:
            raise KeyError(component_id)
        return component

    async def start_component(self, scenario_id: str, component_id: str) -> ScenarioStatus:
        component = self.component(scenario_id, component_id)
        await self.adapter.start_service(component["unit"])
        return await self.status(scenario_id)

    async def stop_component(self, scenario_id: str, component_id: str) -> ScenarioStatus:
        component = self.component(scenario_id, component_id)
        await self.adapter.stop_service(component["unit"])
        return await self.status(scenario_id)

    async def restart_component(self, scenario_id: str, component_id: str) -> ScenarioStatus:
        component = self.component(scenario_id, component_id)
        await self.adapter.stop_service(component["unit"])
        await asyncio.sleep(0.5)
        await self.adapter.start_service(component["unit"])
        if component_id in ("gnb", "amf"):
            # Permite a gNodeB o AMF reestablecer NGAP (SCTP) antes de reconectar el terminal
            await asyncio.sleep(2)
            try:
                ue_comp = next((c for c in CATALOG[scenario_id]["components"] if c["id"] == "ue"), None)
                if ue_comp:
                    await self.adapter.stop_service(ue_comp["unit"])
                    await asyncio.sleep(0.5)
                    await self.adapter.start_service(ue_comp["unit"])
            except Exception:
                pass
        return await self.status(scenario_id)


scenario_manager = ScenarioManager()
