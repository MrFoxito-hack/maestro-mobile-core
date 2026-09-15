import json
import logging
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse

from app.body_limit import BodyLimitMiddleware
from app.config import Settings, get_settings
from app.errors import ChargingError
from app.metrics import RequestMetrics
from app.models import AccountUpsert, ChargingDataRequest, ChargingDataResponse, ReconcileRequest
from app.repository import ChargingRepository
from app.service import ChargingService

LOG = logging.getLogger("maestro.charging")


def create_app(settings: Settings | None = None, *, management=False):
    settings = settings or get_settings()
    repository = ChargingRepository(settings.database_path)
    service = ChargingService(repository, settings.default_grant_bytes, settings.validity_time_seconds)
    metrics = RequestMetrics()

    @asynccontextmanager
    async def lifespan(_):
        settings.validate_security(management=management)
        settings.prepare()
        repository.initialize()
        repository.readiness()
        yield

    application = FastAPI(title=settings.app_name + (" Management" if management else " SBI"),
                          version="0.2.0", lifespan=lifespan, docs_url=None, redoc_url=None,
                          openapi_url="/openapi.json" if management else None)
    application.state.repository = repository
    application.state.service = service
    application.state.settings = settings
    application.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_bytes)

    def problem(status, cause, detail):
        return JSONResponse(status_code=status, media_type="application/problem+json",
                            content={"status": status, "cause": cause,
                                     "title": "Charging request failed", "detail": detail})

    @application.exception_handler(ChargingError)
    async def domain_error(_, exc):
        return problem(exc.status, exc.cause, exc.detail)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(_, exc):
        # Never echo secrets, SUPIs or raw payload values from validation errors.
        fields = ["/".join(str(p) for p in err['loc']) for err in exc.errors()]
        return problem(400, "MANDATORY_IE_INCORRECT", "Invalid or unsupported fields: " + ", ".join(fields))

    @application.exception_handler(sqlite3.Error)
    async def unavailable(_, exc):
        LOG.error("charging_database_failure", extra={"error_type": type(exc).__name__})
        return problem(503, "SYSTEM_FAILURE", "Charging storage is unavailable; retry the same invocation")

    def bearer(authorization):
        if not authorization or not authorization.startswith("Bearer "):
            raise ChargingError(401, "UNAUTHORIZED", "Bearer authentication required")
        return authorization[7:]

    def require_admin(authorization: str | None = Header(default=None)):
        token = bearer(authorization)
        configured = settings.admin_token
        if not configured or not secrets.compare_digest(token.encode(), configured.get_secret_value().encode()):
            raise ChargingError(403, "FORBIDDEN", "Invalid administrative credential")
        return "admin-api"

    def require_smf(authorization: str | None = Header(default=None)):
        if settings.sbi_lab_no_auth:
            return None  # Explicit isolated-lab opt-in. NOT OAuth2 support.
        token = bearer(authorization)
        for nf, secret in settings.sbi_tokens.items():
            if secrets.compare_digest(token.encode(), secret.get_secret_value().encode()):
                return nf.lower()
        raise ChargingError(403, "FORBIDDEN", "Invalid NF credential")

    def match_consumer(payload, principal):
        if principal and str(payload.nfConsumerIdentification.nFName) != principal:
            raise ChargingError(403, "FORBIDDEN", "Credential is bound to a different NF instance")

    @application.middleware("http")
    async def observe(request: Request, call_next):
        start, request_id = time.perf_counter(), str(uuid4())
        path = request.url.path
        operation = ("UPDATE" if path.endswith("/update") else
                     "RELEASE" if path.endswith("/release") else
                     "CREATE" if path.endswith("/chargingdata") else "MANAGEMENT")
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        if path.startswith("/nchf-"):
            metrics.observe(operation, response.status_code, elapsed)
            LOG.info(json.dumps({"event": "nchf_request", "operation": operation,
                                "status": response.status_code, "durationSeconds": elapsed,
                                "requestId": request_id}))
        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.get("/health")
    def health():
        return {"status": "ok", "service": "nchf-convergedcharging",
                "apiVersion": "v3", "profile": "experimental-rel16-volume-v2",
                "interface": "management" if management else "sbi"}

    @application.get("/ready")
    def ready():
        return repository.readiness()

    if not management:
        @application.post("/nchf-convergedcharging/v3/chargingdata", status_code=201,
                          response_model=ChargingDataResponse, response_model_exclude_none=True)
        def create(payload: ChargingDataRequest, response: Response, principal=Depends(require_smf)):
            match_consumer(payload, principal)
            ref, result = service.create(payload)
            response.headers["Location"] = f"/nchf-convergedcharging/v3/chargingdata/{ref}"
            return result

        @application.post("/nchf-convergedcharging/v3/chargingdata/{ref}/update",
                          response_model=ChargingDataResponse, response_model_exclude_none=True)
        def update(ref: str, payload: ChargingDataRequest, principal=Depends(require_smf)):
            match_consumer(payload, principal)
            return service.update(ref, payload)

        @application.post("/nchf-convergedcharging/v3/chargingdata/{ref}/release", status_code=204)
        def release(ref: str, payload: ChargingDataRequest, principal=Depends(require_smf)):
            match_consumer(payload, principal)
            service.release(ref, payload)
            return Response(status_code=204)

        # Restricted metrics include process-local SBI timings, without subscriber labels.
        @application.get("/metrics", dependencies=[Depends(require_smf)])
        def sbi_metrics():
            stale = (datetime.now(timezone.utc) - timedelta(seconds=settings.orphan_grace_seconds)).isoformat()
            return PlainTextResponse(metrics.render(repository, stale), media_type="text/plain; version=0.0.4")
    else:
        @application.put("/admin/v1/accounts/{supi}")
        def account_put(supi: str, payload: AccountUpsert, actor=Depends(require_admin)):
            if payload.supi != supi:
                raise ChargingError(409, "CONTEXT_MISMATCH", "path and payload SUPI differ")
            return repository.upsert_account(supi, payload.quotaBytes, payload.enabled, actor)

        @application.get("/admin/v1/accounts/{supi}", dependencies=[Depends(require_admin)])
        def account_get(supi: str):
            result = repository.get_account(supi)
            if not result:
                raise ChargingError(404, "USER_UNKNOWN", "account not found")
            return result

        @application.get("/admin/v1/{kind}", dependencies=[Depends(require_admin)])
        def list_records(kind: str, supi: str | None = None,
                         limit: int = Query(default=100, ge=1, le=500),
                         offset: int = Query(default=0, ge=0)):
            if kind not in {"accounts", "sessions", "cdrs", "ledger"}:
                raise ChargingError(404, "RESOURCE_NOT_FOUND", "unknown collection")
            return repository.list_records(kind, supi=supi, limit=limit, offset=offset)

        @application.post("/admin/v1/sessions/{ref}/reconcile")
        def reconcile(ref: str, payload: ReconcileRequest, actor=Depends(require_admin)):
            return service.reconcile(ref, payload.reason, actor)

    return application


app = create_app()
admin_app = create_app(management=True)
