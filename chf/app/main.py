from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.models import AccountUpsert, ChargingDataRequest, ProblemDetails
from app.repository import ChargingRepository
from app.service import ChargingError, ChargingService


settings = get_settings()
repository = ChargingRepository(settings.database_path)
service = ChargingService(repository, settings.default_grant_bytes, settings.validity_time_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI):
    repository.initialize()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.exception_handler(ChargingError)
async def charging_error_handler(_, exc: ChargingError):
    problem = ProblemDetails(
        status=exc.status,
        cause=exc.cause,
        title="Nchf_ConvergedCharging request failed",
        detail=exc.detail,
    )
    return JSONResponse(
        status_code=exc.status,
        content=problem.model_dump(),
        media_type="application/problem+json",
    )


@app.get("/health")
def health():
    return {"status": "ok", "service": "nchf-convergedcharging", "apiVersion": "v3"}


@app.post("/nchf-convergedcharging/v3/chargingdata", status_code=201)
def create_charging_data(request: ChargingDataRequest, response: Response):
    ref, result = service.create(request)
    response.headers["Location"] = f"/nchf-convergedcharging/v3/chargingdata/{ref}"
    return result


@app.post("/nchf-convergedcharging/v3/chargingdata/{ref}/update")
def update_charging_data(ref: str, request: ChargingDataRequest):
    return service.update(ref, request)


@app.post(
    "/nchf-convergedcharging/v3/chargingdata/{ref}/release",
    status_code=204,
)
def release_charging_data(ref: str, request: ChargingDataRequest):
    service.release(ref, request)
    return Response(status_code=204)


# Management endpoints are intentionally outside the 3GPP API root. Authentication
# and EMS RBAC are required before these endpoints are exposed beyond localhost.
@app.put("/admin/v1/accounts/{supi}")
def upsert_account(supi: str, payload: AccountUpsert):
    if payload.supi != supi:
        raise HTTPException(status_code=409, detail="path and payload SUPI differ")
    return repository.upsert_account(payload.supi, payload.quotaBytes, payload.enabled)


@app.get("/admin/v1/accounts/{supi}")
def get_account(supi: str):
    account = repository.get_account(supi)
    if not account:
        raise HTTPException(status_code=404, detail="account not found")
    return account

