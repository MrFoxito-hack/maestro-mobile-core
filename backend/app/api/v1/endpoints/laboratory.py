from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response

from app.api.deps import current_user
from app.core.config import get_settings
from app.laboratory.capabilities import CAPABILITIES, templates, c6_preset
from app.laboratory.planner import validate_design
from app.laboratory.repository import ConflictError, Repository
from app.laboratory.schemas import CampaignCreate, Descriptor, ExperimentCreate
from app.laboratory.schemas import AssignmentCreate, ExecutionStart
from app.laboratory.schemas import BindingCreate, PreflightCreate, NotebookCreate
from app.laboratory.research import Research
from app.laboratory.storage import repository
from app.laboratory.runtime import Runtime
from app.db import connection
from app.models import UserPublic

router = APIRouter(prefix="/laboratory", tags=["laboratory"])
User = Annotated[UserPublic, Depends(current_user)]
RequestKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


Store = Annotated[Repository, Depends(repository)]

from app.laboratory.investigation.service import Investigations, InvestigationCreate, SuggestRequest


@router.post('/experiments/{experiment_id}/investigations', status_code=201)
def investigation_create(experiment_id: str, body: InvestigationCreate, user: User, store: Store, key: RequestKey):
    return call(lambda: Investigations(store).create(experiment_id, user, key, body))


@router.get('/investigations/{identity}')
def investigation_detail(identity: str, user: User, store: Store):
    return call(lambda: Investigations(store).detail(identity, user))


@router.post('/investigations/{identity}/suggest')
async def investigation_suggest(identity: str, body: SuggestRequest, user: User, store: Store, key: RequestKey, request: Request):
    import asyncio
    from contextlib import suppress
    task = asyncio.create_task(Investigations(store).suggest(identity, user, key, body))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=.1)
            if not task.done() and await request.is_disconnected():
                task.cancel()
                with suppress(asyncio.CancelledError): await task
                return Response(status_code=499)
        return await task
    except KeyError as error:
        raise HTTPException(404, 'Investigacion no encontrada.') from error
    except ConflictError as error:
        raise HTTPException(409, str(error)) from error
    except PermissionError as error:
        raise HTTPException(403, 'Investigacion no autorizada.') from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError): await task


def call(operation):
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    except ConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/capabilities")
def capabilities(user: User):
    return {"source": "repository_review", "live_verified": False,
            "execution_mode": get_settings().execution_mode, "items": CAPABILITIES}


@router.get("/templates")
def catalog(user: User):
    return templates()


@router.get('/presets')
def presets(user: User):
    return [c6_preset()]


@router.get("/experiments")
def experiments(user: User, store: Store):
    return store.list_experiments(user)


@router.post("/experiments", status_code=201)
def create(body: ExperimentCreate, user: User, store: Store, key: RequestKey):
    return call(lambda: store.create_experiment(user, key, body.model_dump()))


@router.get("/experiments/{experiment_id}")
def detail(experiment_id: str, user: User, store: Store):
    return call(lambda: store.detail(experiment_id, user))


@router.post("/experiments/{experiment_id}/revisions", status_code=201)
def revision(experiment_id: str, body: Descriptor, user: User, store: Store, key: RequestKey):
    return call(lambda: store.create_revision(experiment_id, user, key, body.model_dump()))


@router.post("/revisions/{revision_id}/validate")
def validate(revision_id: str, user: User, store: Store):
    revision = call(lambda: store.get_revision(revision_id, user))
    return {"revision_id": revision_id, "descriptor_sha256": revision["sha256"],
            **validate_design(Descriptor.model_validate(revision["descriptor"]))}


@router.post("/campaigns", status_code=201)
def campaign(body: CampaignCreate, user: User, store: Store, key: RequestKey):
    return call(lambda: store.create_campaign(body.revision_id, user, key))


def resolve_subject(username: str) -> UserPublic:
    with connection() as db:
        row = db.execute("SELECT username,role,testbed,assigned_imsi FROM users WHERE username=? AND enabled=1", (username,)).fetchone()
    if not row:
        raise HTTPException(404, "Usuario activo no encontrado.")
    return UserPublic(**dict(row))


@router.get("/assignments")
def assignments(user: User, store: Store):
    return Runtime(store).assignments(user)


@router.post("/assignments", status_code=201)
def grant(body: AssignmentCreate, user: User, store: Store, key: RequestKey):
    if user.role not in ("teacher", "admin"):
        raise HTTPException(403, "Solo docentes o administradores asignan prácticas.")
    subject = resolve_subject(body.username)
    return call(lambda: Runtime(store).grant(user, subject, key, body))


@router.post("/assignments/{assignment_id}/revoke")
def revoke(assignment_id: str, user: User, store: Store):
    return call(lambda: Runtime(store).revoke(assignment_id, user))


@router.post("/campaigns/{campaign_id}/start", status_code=202)
def start(campaign_id: str, body: ExecutionStart, user: User, store: Store, key: RequestKey):
    return call(lambda: Runtime(store).enqueue(campaign_id, body.assignment_id, user, key, mode=body.mode))


@router.get("/campaigns/{campaign_id}/executions")
def executions(campaign_id: str, user: User, store: Store):
    return call(lambda: Runtime(store).executions(campaign_id, user))


@router.get("/executions/{execution_id}")
def execution(execution_id: str, user: User, store: Store):
    return call(lambda: Runtime(store).detail(execution_id, user))


@router.get("/executions/{execution_id}/events")
def events(execution_id: str, user: User, store: Store, after: int = Query(default=0, ge=0)):
    return call(lambda: Runtime(store).events(execution_id, user, after))


@router.post("/executions/{execution_id}/cancel")
def cancel(execution_id: str, user: User, store: Store):
    return call(lambda: Runtime(store).cancel(execution_id, user))


@router.post("/executions/{execution_id}/recover")
def recover(execution_id: str, user: User, store: Store):
    return call(lambda: Runtime(store).retry_recovery(execution_id, user))


@router.post('/assignments/{assignment_id}/bindings', status_code=201)
def bind(assignment_id: str, body: BindingCreate, user: User, store: Store, key: RequestKey):
    return call(lambda: Research(store).bind(assignment_id, user, key, body))


@router.post('/campaigns/{campaign_id}/preflights', status_code=201)
def preflight(campaign_id: str, body: PreflightCreate, user: User, store: Store, key: RequestKey):
    return call(lambda: Research(store).collect(campaign_id, body.assignment_id, user, key))


@router.get('/campaigns/{campaign_id}/preflights')
def preflights(campaign_id: str, user: User, store: Store):
    return call(lambda: Research(store).preflights(campaign_id, user))


@router.get('/experiments/{experiment_id}/evidence')
def evidence(experiment_id: str, user: User, store: Store):
    return call(lambda: Research(store).evidence(experiment_id, user))


@router.post('/experiments/{experiment_id}/evidence/{evidence_id}/analyze', status_code=201)
def analyze(experiment_id: str, evidence_id: str, user: User, store: Store, key: RequestKey):
    return call(lambda: Research(store).analyze(experiment_id, evidence_id, user, key))


@router.get('/experiments/{experiment_id}/notes')
def notes(experiment_id: str, user: User, store: Store):
    return call(lambda: Research(store).notes(experiment_id, user))


@router.post('/experiments/{experiment_id}/notes', status_code=201)
def note(experiment_id: str, body: NotebookCreate, user: User, store: Store, key: RequestKey):
    return call(lambda: Research(store).note(experiment_id, user, key, body))


@router.get('/experiments/{experiment_id}/export')
def export(experiment_id: str, user: User, store: Store):
    content = call(lambda: Research(store).export(experiment_id, user))
    return Response(content, media_type='application/zip',
                    headers={'Content-Disposition': f'attachment; filename="laboratory-{experiment_id}.zip"'})
