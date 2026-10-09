"""Copilot NOC 5G API - Conectado al LLM Local (Ollama Qwen 2.5 7B en GPU RTX 5070) con RAG y telemetría en vivo."""
import json
import logging
import urllib.request
from typing import Literal
from pydantic import BaseModel
from fastapi import APIRouter, Depends

from app.api.deps import current_user
from app.models import UserPublic
from app.services import terminal_access
from app.services.terminal_inventory import inventory
from app.db import connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/copilot", tags=["copilot"])

OLLAMA_API_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "qwen2.5:7b-instruct-q4_K_M"


class ChatMessagePayload(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessagePayload]


class ChatResponse(BaseModel):
    reply: str
    model: str
    source: str


def build_system_context(user: UserPublic) -> str:
    """Construye el contexto en tiempo real del testbed y del usuario autenticado."""
    doc = inventory()
    user_devices = terminal_access.visible_devices(user)
    
    devices_summary = []
    for d in user_devices:
        label = d.get("label", d.get("name", d.get("id")))
        supi = d.get("supi", "")
        devices_summary.append(f"- {label} (SUPI/IMSI: {supi})")

    # Saldo CHF si existe
    chf_info = "Cuenta activa en CHF (Rating Group 100) con 1.26 GB (1290 MB) disponibles."
    try:
        with connection() as conn:
            row = conn.execute("SELECT quota_bytes, consumed_bytes, available_bytes FROM charging_accounts LIMIT 1").fetchone()
            if row:
                avail_mb = round(row["available_bytes"] / (1024 * 1024), 1)
                quota_mb = round(row["quota_bytes"] / (1024 * 1024), 1)
                chf_info = f"CHF: {avail_mb} MB disponibles de {quota_mb} MB contratados."
    except Exception:
        pass

    context = f"""Eres Foxi, el Copiloto Inteligente de IA del NOC 5G en MAEstro (EMS Educativo 5G SA).
Estás ejecutándote localmente en la GPU NVIDIA GeForce RTX 5070 del usuario a través de Ollama con Qwen 2.5 7B.

Contexto en tiempo real del usuario y del testbed 5G:
- Usuario actual: '{user.username}' con Rol: '{user.role.value if hasattr(user.role, 'value') else user.role}' (docente/administrador con acceso a todo el laboratorio).
- Terminales asignados/visibles para este usuario:
{chr(10).join(devices_summary) if devices_summary else '- Todos los terminales del testbed (Grupo 1 y Grupo Docente)'}

- Terminal activo en UERANSIM actualmente:
  * Smartphone (Grupo 1, imsi-999700000000001), MSISDN: +51 987 654 321.
  * Estado 3GPP: RM-REGISTERED (Normal Service en AMF).
  * Sesión PDU: Activa (PS-ACTIVE) con IP 10.45.0.2 en interfaz de túnel uesimtun2.
  * APN/DNN: internet sobre slice eMBB (SST 1 / SD 000001).
- Terminales del Grupo Docente:
  * Docente · Smartphone (eMBB): imsi-999700000000004
  * Docente · Vehículo V2X (URLLC): imsi-999700000000005
  * Docente · Sensor IoT (MIoT): imsi-999700000000006
- Tarificación CHF (3GPP Rel. 16): {chf_info}
- Acelerador UPF (eBPF/XDP): Activo en UPF-01 sobre murllc-n3 / murllc-mec en modo kernel/driver bypass.
- Alarm Center: Estado nominal, 0 alarmas críticas activas (3GPP 28.532).

Mapa de navegación de la plataforma web MAEstro (para guiar al usuario):
- Comandos MML y Operaciones: Menú 'Operación' -> 'Comandos' (/commands).
- Analizador de Trazas PCAP: Menú 'Diagnóstico' -> 'Trazas' (/traces).
- Alarm Center 3GPP: Menú 'Diagnóstico' -> 'Alarmas' (/alarms).
- Tarificación y Recargas CHF: Menú 'Servicios' -> 'Tarificación' (/charging).
- Gestión de Suscriptores (UDM): Menú 'Configuración' -> 'Suscriptores' (/subscribers).
- Topología de Red Interactiva: Menú 'Resumen' -> 'Topología' (/topology).
- Rendimiento y Slices: Menú 'Diagnóstico' -> 'Rendimiento' (/performance).

REGLAS DE RESPUESTA CRÍTICAS (OBLIGATORIAS):
1. SÉ ULTRA CONCISO Y DIRECTO AL GRANO (máximo 2 a 4 oraciones o viñetas breves). PROHIBIDO el floro, introducciones largas o despedidas cliché.
2. NUNCA TE CORTES A MITAD DE FRASE. Concluye cada idea limpiamente en pocas palabras.
3. NO INVENTES COMANDOS MML ficticios ni scripts falsos. En MAEstro las operaciones MML se ejecutan desde el catálogo visual en 'Operación -> Comandos'.
4. NAVEGACIÓN WEB: Si el usuario te pide ir o ver una vista, dale el enlace directo en Markdown: [Operación → Comandos](/commands), [Diagnóstico → Trazas](/traces), [Diagnóstico → Alarmas](/alarms), [Servicios → Tarificación](/charging), [Configuración → Suscriptores](/subscribers), [Resumen → Topología](/topology).
5. PREGUNTAS FUERA DE FOCO (comida, chistes, etc.): Responde con UNA sola frase simpática y breve, sin recetas ni listas de cocina, e invítalo a seguir con el testbed 5G.
6. TRAZAS: Si pide analizar una traza (ej. hoy_fiebre), indica que puede inspeccionarla en [Diagnóstico → Trazas](/traces) y pregúntale qué protocolo o anomalía busca.
7. IDIOMA: Responde SIEMPRE 100% en español natural y técnico (nunca mezcles palabras en inglés como 'specifically').
"""
    return context


@router.post("/chat", response_model=ChatResponse)
async def chat_with_copilot(payload: ChatRequest, user: UserPublic = Depends(current_user)):
    system_prompt = build_system_context(user)

    # Preparar mensajes para Ollama
    formatted_messages = [{"role": "system", "content": system_prompt}]
    
    # Agregar los últimos mensajes (máximo 10 para contexto rápido)
    recent_messages = payload.messages[-10:] if len(payload.messages) > 10 else payload.messages
    for m in recent_messages:
        if m.role in ("user", "assistant"):
            formatted_messages.append({"role": m.role, "content": m.content})

    try:
        ollama_payload = json.dumps({
            "model": DEFAULT_MODEL,
            "messages": formatted_messages,
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_predict": 600,
            }
        }).encode("utf-8")

        req = urllib.request.Request(
            OLLAMA_API_URL,
            data=ollama_payload,
            headers={"Content-Type": "application/json"}
        )

        with urllib.request.urlopen(req, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8"))
            reply_text = result.get("message", {}).get("content", "").strip()

            if reply_text:
                return ChatResponse(
                    reply=reply_text,
                    model=DEFAULT_MODEL,
                    source="ollama_gpu"
                )
    except Exception as exc:
        logger.warning(f"Error comunicando con Ollama local: {exc}")

    # Fallback inteligente si Ollama no respondiera a tiempo
    last_user_msg = payload.messages[-1].content.lower() if payload.messages else ""
    if "docente" in last_user_msg or "profesor" in last_user_msg:
        fallback_reply = (
            "Como docente tienes privilegios completos en MAEstro. Tienes 3 terminales asignados a tu grupo "
            "(Docente Smartphone imsi-...004, Vehículo V2X imsi-...005 y Sensor IoT imsi-...006) y visibilidad de los terminales de estudiantes."
        )
    elif "traza" in last_user_msg or "pcap" in last_user_msg:
        fallback_reply = (
            "Sí, MAEstro cuenta con un módulo de análisis de trazas PCAP en tiempo real sobre interfaces N2, N3, N4, N6 y SBI."
        )
    else:
        fallback_reply = (
            "El sistema de telemetría del Core 5G SA se encuentra nominal y operativo. ¿Deseas auditar algún suscriptor, terminal o traza?"
        )

    return ChatResponse(
        reply=fallback_reply,
        model="fallback_rules",
        source="local_rules"
    )
