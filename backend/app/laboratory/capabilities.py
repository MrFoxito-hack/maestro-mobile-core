"""Repository evidence inventory, deliberately not a live capability probe."""
from app.laboratory.acceptance import real_availability

CAPABILITIES = [
    {"id": "qoe_closed_loop", "label": "Campaña QoE y closed-loop",
     "status": "documented", "source": "reportes/2026-09-28_nwdaf_gate4_resultados_tesis.md",
     "limitation": "Una pareja audiovisual documentada; requiere piloto y repeticiones."},
    {"id": "qos_enforcement", "label": "MBR por sesión",
     "status": "documented", "source": "reportes/2026-09-28_nwdaf_closed_loop_acceptance.md",
     "limitation": "ACK N7/PFCP no demuestra efecto. Verificar recepción; no es límite agregado por slice."},
    {"id": "charging", "label": "Cuota y liberación CHF",
     "status": "documented", "source": "docs/charging-acceptance-2026-09-16.md",
     "limitation": "Perfil acotado; el consumo no se revierte como configuración."},
    {"id": "slicing_fault", "label": "Fallo de slicing del catálogo actual",
     "status": "simulated", "source": "backend/app/services/experiments.py",
     "limitation": "No usar como inyección real ni evidencia del Core."},
    {"id": "campaign_execution", "label": "Worker experimental",
     "status": "operational_pilot", "source": "backend/app/laboratory/worker.py",
     "limitation": "Piloto real con guard de transporte y rescate; recuperación efectiva de políticas y exclusión de otros escritores pendientes."},
    {"id": "local_investigator", "label": "Investigador local en GPU",
     "status": "implemented", "source": "docs/C6_QOE_IA_VERIFICACION.md",
     "limitation": "Asesor local de lectura. Consultar exactitud y tiempos medidos; no aplica políticas."},
]


def c6_preset():
    import json
    from pathlib import Path
    return json.loads(Path(__file__).with_name('c6_preset.json').read_text(encoding='utf-8'))


def templates() -> list[dict]:
    return [{"id": "qoe_closed_loop", "version": 1, "title": "QoE con y sin closed-loop",
             "scenario": "5g-sa", "primary_metric": "player_startup_delay_seconds",
             "execution_available": False,
             "dry_run_available": True,
             **real_availability(),
             "description": "Compara el mismo servicio con carga competidora y dos estados verificados del controlador."}]
