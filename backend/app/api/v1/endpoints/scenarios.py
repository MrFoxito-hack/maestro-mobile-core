from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import current_user, operator_user
from app.db import add_audit
from app.models import ScenarioAction, UserPublic
from app.services.scenarios import CATALOG, scenario_manager

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


def ensure_scenario(scenario_id: str) -> None:
    if scenario_id not in CATALOG:
        raise HTTPException(404, "Escenario no encontrado")


@router.get("")
def catalog(_: UserPublic = Depends(current_user)):
    return scenario_manager.list_catalog()


@router.get("/{scenario_id}/status")
async def status(scenario_id: str, _: UserPublic = Depends(current_user)):
    ensure_scenario(scenario_id)
    return await scenario_manager.status(scenario_id)


@router.post("/{scenario_id}/start")
async def start(scenario_id: str, action: ScenarioAction, user: UserPublic = Depends(operator_user)):
    ensure_scenario(scenario_id)
    try:
        result = await scenario_manager.start(scenario_id, action)
        add_audit(user.username, user.role, action.testbed, "scenario.start", {"scenario_id": scenario_id, **action.parameters.model_dump()}, "success")
        return result
    except Exception as exc:
        add_audit(user.username, user.role, action.testbed, "scenario.start", {"scenario_id": scenario_id}, "failed")
        raise HTTPException(500, str(exc))


@router.post("/{scenario_id}/stop")
async def stop(scenario_id: str, user: UserPublic = Depends(operator_user)):
    ensure_scenario(scenario_id)
    result = await scenario_manager.stop(scenario_id)
    add_audit(user.username, user.role, user.testbed, "scenario.stop", {"scenario_id": scenario_id}, "success")
    return result


@router.post("/{scenario_id}/components/{component_id}/start")
async def start_component(scenario_id: str, component_id: str, user: UserPublic = Depends(operator_user)):
    ensure_scenario(scenario_id)
    try:
        result = await scenario_manager.start_component(scenario_id, component_id)
    except KeyError:
        raise HTTPException(404, "Componente no encontrado")
    except Exception as exc:
        add_audit(user.username, user.role, user.testbed, "component.start", {"scenario_id": scenario_id, "component_id": component_id}, "failed")
        raise HTTPException(500, str(exc))
    add_audit(user.username, user.role, user.testbed, "component.start", {"scenario_id": scenario_id, "component_id": component_id}, "success")
    return result


@router.post("/{scenario_id}/components/{component_id}/stop")
async def stop_component(scenario_id: str, component_id: str, user: UserPublic = Depends(operator_user)):
    ensure_scenario(scenario_id)
    try:
        result = await scenario_manager.stop_component(scenario_id, component_id)
    except KeyError:
        raise HTTPException(404, "Componente no encontrado")
    except Exception as exc:
        add_audit(user.username, user.role, user.testbed, "component.stop", {"scenario_id": scenario_id, "component_id": component_id}, "failed")
        raise HTTPException(500, str(exc))
    add_audit(user.username, user.role, user.testbed, "component.stop", {"scenario_id": scenario_id, "component_id": component_id}, "success")
    return result


@router.post("/{scenario_id}/components/{component_id}/restart")
async def restart_component(scenario_id: str, component_id: str, user: UserPublic = Depends(operator_user)):
    ensure_scenario(scenario_id)
    try:
        result = await scenario_manager.restart_component(scenario_id, component_id)
    except KeyError:
        raise HTTPException(404, "Componente no encontrado")
    except Exception as exc:
        add_audit(user.username, user.role, user.testbed, "component.restart", {"scenario_id": scenario_id, "component_id": component_id}, "failed")
        raise HTTPException(500, str(exc))
    add_audit(user.username, user.role, user.testbed, "component.restart", {"scenario_id": scenario_id, "component_id": component_id}, "success")
    return result

