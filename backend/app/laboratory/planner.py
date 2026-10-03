"""Pure static planning. A valid design is never permission to run a network test."""

import math
import random

from app.laboratory.schemas import Descriptor

PLANNER_VERSION = "paired-blocks-v1"
TREATMENTS = ("controller_disabled_verified", "controller_enabled_verified")


def validate_design(descriptor: Descriptor) -> dict:
    issues = []
    for field in ("observed_ue", "competing_ue", "repetitions", "measurement_seconds",
                  "campaign_budget_bytes", "capture_budget_bytes"):
        if getattr(descriptor, field) is None:
            issues.append({"field": field, "code": "required", "message": "Campo necesario para planificar."})
    if not descriptor.load_mbps:
        issues.append({"field": "load_mbps", "code": "required", "message": "Define al menos un nivel de carga."})
    if descriptor.observed_ue and descriptor.observed_ue == descriptor.competing_ue:
        issues.append({"field": "competing_ue", "code": "same_subject", "message": "Usa dos UE diferentes."})

    count = len(descriptor.load_mbps) * (descriptor.repetitions or 0) * 2
    seconds = count * (descriptor.measurement_seconds or 0)
    traffic = math.ceil(sum(descriptor.load_mbps) * 1_000_000 / 8
                        * (descriptor.measurement_seconds or 0) * (descriptor.repetitions or 0) * 2)
    if descriptor.campaign_budget_bytes is not None and traffic > descriptor.campaign_budget_bytes:
        issues.append({"field": "campaign_budget_bytes", "code": "budget_exceeded",
                       "message": "El tráfico competidor previsto supera el presupuesto de campaña."})
    return {
        "design_valid": not issues,
        "execution_ready": False,
        "issues": issues,
        "estimates": {"runs": count, "measurement_seconds": seconds, "competing_payload_bytes": traffic},
        "warnings": [
            "Estimación de carga competidora: excluye vídeo, preparación, cabeceras y recuperación; no es una cuota CHF.",
            "Duración estimada solo de medición; el piloto debe determinar repeticiones y estabilización.",
            "Falta preflight vivo: sesiones, ruta, cuotas, política inicial, relojes y recuperación.",
        ],
        "execution_blockers": ["real_pilot_scope_required", "live_preflight_required", "lab_assignment_required"],
        "planner_version": PLANNER_VERSION,
    }


def plan_runs(descriptor: Descriptor) -> list[dict]:
    if not validate_design(descriptor)["design_valid"]:
        raise ValueError("El diseño contiene campos pendientes o incompatibles.")
    rng = random.Random(descriptor.seed)
    blocks = [(level, repetition) for level in descriptor.load_mbps
              for repetition in range(1, descriptor.repetitions + 1)]
    rng.shuffle(blocks)
    runs = []
    for block, (level, repetition) in enumerate(blocks, 1):
        treatments = list(TREATMENTS)
        rng.shuffle(treatments)
        for treatment in treatments:
            runs.append({"ordinal": len(runs) + 1, "block": block, "load_mbps": level,
                         "repetition": repetition, "treatment": treatment,
                         "measurement_seconds": descriptor.measurement_seconds,
                         "execution_status": "planned", "validity_status": "pending",
                         "hypothesis_outcome": "pending"})
    return runs
