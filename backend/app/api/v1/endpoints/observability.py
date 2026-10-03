import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from app.api.deps import current_user, operator_user
from app.core.config import get_settings
from app.core.security import decode_token
from app.db import add_audit
from app.models import InterfaceTraceStart, SubscriberTraceStart, TraceStart, UserPublic
from app.services.observability import collect_alarms, metrics_service, trace_service
from app.services.scenarios import CATALOG, scenario_manager
from app.services.trace_catalog import TRACE_PROFILES
from app.services.trace_tasks import TraceAccessError, TraceTaskError

router = APIRouter(tags=["observability"])


@router.get("/metrics")
async def metrics(scenario_id: str = "5g-sa", _: UserPublic = Depends(current_user)):
    return await metrics_service.snapshot(scenario_id)


@router.post("/traffic/ping")
async def test_traffic(count: int = 3, user: UserPublic = Depends(operator_user)):
    result = await scenario_manager.adapter.generate_test_traffic(min(max(count, 1), 10))
    add_audit(
        user.username,
        user.role,
        user.testbed,
        "traffic.ping",
        {"count": count},
        "success" if result.get("success") else "failed",
    )
    return result


@router.get("/runtime/{scenario_id}")
async def runtime(scenario_id: str, _: UserPublic = Depends(current_user)):
    if scenario_id not in CATALOG:
        raise HTTPException(404, "Escenario no encontrado")
    try:
        return await scenario_manager.adapter.runtime_snapshot()
    except Exception as exc:
        raise HTTPException(502, f"No se pudo consultar el testbed: {exc}") from exc


@router.get("/alarms/{scenario_id}")
async def alarms(scenario_id: str, _: UserPublic = Depends(current_user)):
    if scenario_id not in CATALOG:
        raise HTTPException(404, "Escenario no encontrado")
    return await collect_alarms(scenario_id)


@router.get("/logs/{scenario_id}/{component_id}")
async def logs(scenario_id: str, component_id: str, _: UserPublic = Depends(current_user)):
    if scenario_id not in CATALOG:
        raise HTTPException(404, "Escenario no encontrado")
    component = next((item for item in CATALOG[scenario_id]["components"] if item["id"] == component_id), None)
    if not component:
        raise HTTPException(404, "Componente no encontrado")
    return {"lines": await scenario_manager.adapter.logs(component["unit"])}


@router.get("/traces")
async def traces(
    scenario_id: str | None = None,
    trace_type: str | None = Query(default=None, pattern="^(interface|subscriber)$"),
    status: str | None = None,
    user: UserPublic = Depends(current_user),
):
    return await trace_service.list(
        user,
        scenario_id=scenario_id,
        trace_type=trace_type,
        status=status,
    )


@router.get("/traces/options/{scenario_id}")
def trace_options(scenario_id: str, _: UserPublic = Depends(current_user)):
    if scenario_id not in CATALOG:
        raise HTTPException(404, "Escenario no encontrado")
    return [
        {
            "id": point_id,
            "label": point["label"],
            "interface": point["device"],
            "protocol": point["protocols"][0].lower().replace("-", ""),
            "procedures": point["procedures"],
            "filter": None,
        }
        for point_id, point in TRACE_PROFILES[scenario_id].items()
    ]


@router.get("/traces/capabilities/{scenario_id}")
async def trace_capabilities(scenario_id: str, user: UserPublic = Depends(current_user)):
    try:
        return await trace_service.capabilities(scenario_id, user)
    except KeyError:
        raise HTTPException(404, "Escenario no encontrado")
    except Exception as exc:
        raise HTTPException(502, f"No se pudieron consultar las capacidades: {exc}")


@router.get("/traces/node-catalog/{scenario_id}")
def trace_node_catalog(scenario_id: str, _: UserPublic = Depends(current_user)):
    if scenario_id != "5g-sa":
        return []
    from app.services.trace_catalog import NODE_TRACE_CATALOG
    return [
        {"id": node_id, **{key: value for key, value in item.items() if key != "filter"}}
        for node_id, item in NODE_TRACE_CATALOG.items()
    ]


@router.post("/traces/interface", status_code=201)
async def start_interface_trace(payload: InterfaceTraceStart, user: UserPublic = Depends(current_user)):
    try:
        result = await trace_service.create_interface(payload, user)
        add_audit(
            user.username,
            user.role,
            payload.testbed_id,
            "trace.interface.start",
            {
                "task_id": result["id"],
                "scenario_id": payload.scenario_id,
                "node_id": payload.node_id,
                "component_id": payload.component_id,
                "capture_point": payload.capture_point,
                "duration_seconds": payload.duration_seconds,
                "max_megabytes": payload.max_megabytes,
            },
            "success",
        )
        return result
    except TraceAccessError as exc:
        raise HTTPException(403, str(exc))
    except (TraceTaskError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    except KeyError as exc:
        raise HTTPException(404, str(exc))


@router.post("/traces/subscriber", status_code=201)
async def start_subscriber_trace(payload: SubscriberTraceStart, user: UserPublic = Depends(current_user)):
    try:
        result = await trace_service.create_subscriber(payload, user)
        add_audit(
            user.username,
            user.role,
            payload.testbed_id,
            "trace.subscriber.start",
            {
                "task_id": result["id"],
                "scenario_id": payload.scenario_id,
                "selector": result.get("target"),
                "procedures": payload.procedures,
                "duration_seconds": payload.duration_seconds,
                "include_user_plane": payload.include_user_plane,
                "include_sbi": payload.include_sbi,
                "auto_trigger": payload.auto_trigger,
            },
            "success",
        )
        return result
    except TraceAccessError as exc:
        raise HTTPException(403, str(exc))
    except (TraceTaskError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    except KeyError as exc:
        raise HTTPException(404, str(exc))


@router.post("/traces", status_code=201)
async def start_trace(payload: TraceStart, user: UserPublic = Depends(current_user)):
    try:
        result = await trace_service.create_legacy(payload, user)
        add_audit(
            user.username,
            user.role,
            payload.testbed_id,
            "trace.interface.start.legacy",
            {
                "task_id": result["id"],
                "scenario_id": payload.scenario_id,
                "capture_point": payload.capture_point,
                "duration_seconds": payload.duration_seconds,
            },
            "success",
        )
        return result
    except TraceAccessError as exc:
        raise HTTPException(403, str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))


@router.get("/traces/{trace_id}")
async def trace_detail(trace_id: str, user: UserPublic = Depends(current_user)):
    try:
        return await trace_service.get(trace_id, user)
    except KeyError:
        raise HTTPException(404, "Tarea de traza no encontrada")
    except TraceAccessError as exc:
        raise HTTPException(403, str(exc))


@router.get("/traces/{trace_id}/analysis")
async def trace_analysis(trace_id: str, user: UserPublic = Depends(current_user)):
    try:
        return await trace_service.analysis(trace_id, user)
    except KeyError:
        raise HTTPException(404, "Tarea de traza no encontrada")
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except TraceAccessError as exc:
        raise HTTPException(403, str(exc))
    except TraceTaskError as exc:
        raise HTTPException(409, str(exc))


@router.get("/traces/{trace_id}/frames/{frame_number}/decode")
async def decode_trace_frame(
    trace_id: str,
    frame_number: int,
    user: UserPublic = Depends(current_user),
):
    try:
        return await trace_service.decode_frame(trace_id, frame_number, user)
    except KeyError:
        raise HTTPException(404, "Captura no encontrada")
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except TraceAccessError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except TraceTaskError as exc:
        raise HTTPException(409, str(exc))


@router.post("/traces/{trace_id}/stop")
async def stop_trace(trace_id: str, user: UserPublic = Depends(current_user)):
    try:
        result = await trace_service.stop(trace_id, user)
        add_audit(user.username, user.role, user.testbed, "trace.stop", {"trace_id": trace_id}, "success")
        return result
    except KeyError:
        raise HTTPException(404, "Captura no encontrada")
    except TraceAccessError as exc:
        raise HTTPException(403, str(exc))
    except TraceTaskError as exc:
        raise HTTPException(409, str(exc))


@router.get("/traces/{trace_id}/download")
async def download_trace(
    trace_id: str,
    artifact: str = Query(default="original", pattern="^(original|filtered|evidence)$"),
    user: UserPublic = Depends(current_user),
):
    try:
        path, media_type, filename = await trace_service.artifact(trace_id, artifact, user)
    except KeyError:
        raise HTTPException(404, "Captura no encontrada")
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except TraceAccessError as exc:
        raise HTTPException(403, str(exc))
    except TraceTaskError as exc:
        raise HTTPException(409, str(exc))
    return FileResponse(path, media_type=media_type, filename=filename)


@router.delete("/traces/{trace_id}", status_code=204)
async def delete_trace(trace_id: str, user: UserPublic = Depends(current_user)):
    try:
        await trace_service.delete(trace_id, user)
        add_audit(user.username, user.role, user.testbed, "trace.delete", {"trace_id": trace_id}, "success")
        return Response(status_code=204)
    except KeyError:
        raise HTTPException(404, "Captura no encontrada")
    except TraceAccessError as exc:
        raise HTTPException(403, str(exc))
    except TraceTaskError as exc:
        raise HTTPException(409, str(exc))


@router.websocket("/ws/live/{scenario_id}")
async def live(websocket: WebSocket, scenario_id: str, token: str):
    if scenario_id not in CATALOG:
        await websocket.close(code=4404)
        return
    try:
        decode_token(token)
    except Exception:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    try:
        while True:
            status = await scenario_manager.status(scenario_id)
            metrics_payload = await metrics_service.snapshot(scenario_id)
            await websocket.send_text(json.dumps({
                "type": "snapshot",
                "scenario": status.model_dump(mode="json"),
                "metrics": metrics_payload,
                "alarms": await collect_alarms(scenario_id),
            }))
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        return
