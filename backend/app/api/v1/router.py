from fastapi import APIRouter

from app.api.v1.endpoints import (
    alarm_center,
    audit,
    auth,
    charging,
    configuration,
    experiments,
    laboratory,
    nwdaf,
    observability,
    operations,
    performance,
    scenarios,
    subscribers,
    terminal,
    upf_xdp,
)

router = APIRouter()
router.include_router(alarm_center.router)
router.include_router(auth.router)
router.include_router(charging.router)
router.include_router(nwdaf.router)
router.include_router(terminal.router)
router.include_router(scenarios.router)
router.include_router(configuration.router)
router.include_router(subscribers.router)
router.include_router(observability.router)
router.include_router(operations.router)
router.include_router(performance.router)
router.include_router(audit.router)
router.include_router(experiments.router)
router.include_router(laboratory.router)
router.include_router(upf_xdp.router)
