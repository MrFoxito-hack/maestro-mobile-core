from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import current_user
from app.db import add_audit
from app.models import OperationExecute, UserPublic
from app.services.operations import OperationError, operations_service, _safe_value


router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("/catalog/{scenario_id}")
async def catalog(scenario_id: str, user: UserPublic = Depends(current_user)):
    try:
        return await operations_service.catalog(scenario_id, user)
    except KeyError:
        raise HTTPException(404, "Escenario no encontrado")
    except Exception as exc:
        raise HTTPException(502, f"No se pudo consultar el catálogo operativo: {exc}") from exc


@router.post("/execute", status_code=201)
async def execute(payload: OperationExecute, user: UserPublic = Depends(current_user)):
    audit_parameters = {
        "scenario_id": payload.scenario_id,
        "component_id": payload.component_id,
        "operation_id": payload.operation_id,
        "parameters": _safe_value(payload.parameters),
    }
    try:
        result = await operations_service.execute(
            payload.scenario_id,
            payload.component_id,
            payload.operation_id,
            payload.parameters,
            user,
        )
        add_audit(
            user.username,
            user.role,
            user.testbed,
            "operation.execute",
            {**audit_parameters, "run_id": result["id"]},
            "success",
        )
        return result
    except KeyError as exc:
        add_audit(user.username, user.role, user.testbed, "operation.execute", audit_parameters, "failed")
        raise HTTPException(404, f"Recurso operativo no encontrado: {exc}") from exc
    except PermissionError as exc:
        add_audit(user.username, user.role, user.testbed, "operation.execute", audit_parameters, "denied")
        raise HTTPException(403, str(exc)) from exc
    except OperationError as exc:
        add_audit(user.username, user.role, user.testbed, "operation.execute", audit_parameters, "failed")
        raise HTTPException(422, str(exc)) from exc


@router.get("/history")
def history(
    scenario_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    user: UserPublic = Depends(current_user),
):
    return operations_service.history(user, scenario_id, limit)
