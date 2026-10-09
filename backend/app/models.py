from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Role(str, Enum):
    admin = "admin"
    teacher = "teacher"
    student = "student"


class ScenarioState(str, Enum):
    stopped = "stopped"
    starting = "starting"
    running = "running"
    degraded = "degraded"
    stopping = "stopping"
    failed = "failed"


class Severity(str, Enum):
    critical = "critical"
    major = "major"
    minor = "minor"
    warning = "warning"
    indeterminate = "indeterminate"
    cleared = "cleared"


class UserPublic(BaseModel):
    username: str
    role: Role
    testbed: str | None = None
    assigned_imsi: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


class ScenarioParameters(BaseModel):
    mcc: str = Field(pattern=r"^\d{3}$")
    mnc: str = Field(pattern=r"^\d{2,3}$")
    tac: int = Field(ge=1, le=16_777_215)
    apn_dnn: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9.-]+$")
    sst: int | None = Field(default=None, ge=0, le=255)
    sd: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{6}$")


class ScenarioAction(BaseModel):
    parameters: ScenarioParameters
    testbed: str = "local"


class ComponentStatus(BaseModel):
    id: str
    label: str
    kind: str
    status: str
    node_id: str
    unit: str
    interfaces: list[str] = Field(default_factory=list)
    expected_endpoints: list[dict[str, Any]] = Field(default_factory=list)
    config_paths: list[str] = Field(default_factory=list)
    procedures: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class ScenarioStatus(BaseModel):
    scenario_id: str
    state: ScenarioState
    components: list[ComponentStatus]
    updated_at: datetime
    message: str | None = None


class ConfigWrite(BaseModel):
    path: str
    content: str


class SubscriberCreate(BaseModel):
    imsi: str = Field(pattern=r"^\d{14,15}$")
    key: str = Field(min_length=32, max_length=32, pattern=r"^[0-9A-Fa-f]+$")
    opc: str = Field(min_length=32, max_length=32, pattern=r"^[0-9A-Fa-f]+$")
    amf: str = Field(default="8000", pattern=r"^[0-9A-Fa-f]{4}$")
    apn_dnn: str = Field(default="internet", pattern=r"^[a-zA-Z0-9.-]+$")
    sst: int = Field(default=1, ge=0, le=255)
    sd: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{6}$")

    @field_validator("key", "opc", "amf", "sd")
    @classmethod
    def uppercase_hex(cls, value):
        return value.upper() if value else value


class SubscriberUpdate(BaseModel):
    key: str | None = Field(default=None, min_length=32, max_length=32, pattern=r"^[0-9A-Fa-f]+$")
    opc: str | None = Field(default=None, min_length=32, max_length=32, pattern=r"^[0-9A-Fa-f]+$")
    amf: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{4}$")
    apn_dnn: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9.-]+$")
    sst: int | None = Field(default=None, ge=0, le=255)
    sd: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{6}$")

    @field_validator("key", "opc", "amf", "sd")
    @classmethod
    def uppercase_hex(cls, value):
        return value.upper() if value else value


class TraceLimits(BaseModel):
    name: str = Field(default="Nueva tarea de traza", min_length=3, max_length=80)
    scenario_id: str = "5g-sa"
    testbed_id: str = Field(default="local", min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    capture_agent_id: str = Field(default="primary", pattern=r"^[a-zA-Z0-9._-]+$")
    duration_seconds: int = Field(default=60, ge=5, le=300)
    max_megabytes: int = Field(default=25, ge=1, le=100)


class InterfaceTraceStart(TraceLimits):
    node_id: str
    component_id: str
    capture_point: str
    identifier_type: Literal["imsi", "supi"] | None = None
    identifier: str | None = Field(default=None, min_length=14, max_length=15)

    @model_validator(mode="after")
    def valid_node_user_selector(self):
        import re

        if (self.identifier_type is None) != (self.identifier is None):
            raise ValueError("Tipo e identificador deben proporcionarse juntos")
        if self.identifier and not re.fullmatch(r"\d{14,15}", self.identifier):
            raise ValueError("El SUPI/IMSI debe contener 14 o 15 dígitos")
        return self


class SubscriberTraceStart(TraceLimits):
    scenario_id: Literal["5g-sa"] = "5g-sa"
    identifier_type: Literal["imsi", "supi", "ue-ip"] = "imsi"
    identifier: str = Field(min_length=3, max_length=64)
    procedures: list[Literal["registration", "authentication", "pdu-session", "user-plane"]] = Field(
        default_factory=lambda: ["registration", "authentication", "pdu-session"]
    )
    include_user_plane: bool = True
    include_sbi: bool = False
    auto_trigger: bool = False

    @model_validator(mode="after")
    def valid_identifier(self):
        import ipaddress
        import re

        if self.identifier_type in {"imsi", "supi"}:
            if not re.fullmatch(r"\d{14,15}", self.identifier):
                raise ValueError("El SUPI/IMSI debe contener 14 o 15 dígitos")
        else:
            try:
                ipaddress.ip_address(self.identifier)
            except ValueError as exc:
                raise ValueError("La dirección IP del UE no es válida") from exc
        if not self.procedures:
            raise ValueError("Seleccione al menos un procedimiento")
        return self


class TraceStart(TraceLimits):
    """Contrato legado conservado para clientes de la primera versión."""

    capture_point: str | None = None
    interface: str | None = None
    protocol: str | None = None
    procedure: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def capture_target(self):
        if not self.capture_point and not (self.interface and self.protocol):
            raise ValueError("Debe indicar capture_point o interface/protocol")
        return self


class AuditEvent(BaseModel):
    id: int
    username: str
    role: str
    testbed: str | None
    action: str
    parameters: dict[str, Any]
    result: str
    created_at: datetime


class PolicyAuthorityToken(BaseModel):
    token: int = Field(strict=True, ge=1)
    expected_version: int = Field(strict=True, ge=0)
    action_id: str = Field(pattern=r"^[a-zA-Z0-9._-]{8,100}$")


class OperationExecute(BaseModel):
    scenario_id: str = Field(pattern=r"^[a-zA-Z0-9._-]+$")
    component_id: str = Field(pattern=r"^[a-zA-Z0-9._-]+$")
    operation_id: str = Field(pattern=r"^[a-zA-Z0-9._-]+$")
    parameters: dict[str, Any] = Field(default_factory=dict)
    authority: PolicyAuthorityToken | None = None
