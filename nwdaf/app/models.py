from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Snssai(StrictModel):
    sst: Annotated[int, Field(strict=True, ge=0, le=255)]
    sd: Annotated[str, Field(pattern=r'^[A-Fa-f0-9]{6}$')] | None = None

    @field_validator('sd')
    @classmethod
    def normalize_sd(cls, value):
        return value.upper() if value else value


class SliceLoad(StrictModel):
    loadLevelInformation: Annotated[int, Field(strict=True, ge=0, le=100)]
    snssais: Annotated[list[Snssai], Field(min_length=1)]


class SliceRequest(StrictModel):
    snssai: Snssai
    values: Annotated[list[FiniteFloat], Field(min_length=1, max_length=10000)]
    timestamps: Annotated[list[FiniteFloat], Field(min_length=1, max_length=10000)]
    interval_seconds: Literal[300, 1800] = 300
    period: Annotated[int, Field(ge=2, le=288)] = 12
    source: Annotated[str, Field(min_length=1, max_length=200)]
    # Explicit capacity is required; a generic host CPU counter is not slice load.
    capacity_definition: Annotated[str, Field(min_length=1, max_length=500)]


class AnomalyRequest(StrictModel):
    history: Annotated[list[tuple[FiniteFloat, FiniteFloat]], Field(max_length=10000)]
    value: Annotated[FiniteFloat, Field(ge=0)]
    timestamp: FiniteFloat
    scale_floor: Annotated[FiniteFloat, Field(gt=0)] = 1.0


class TargetedAnomalyRequest(AnomalyRequest):
    supi: Annotated[str, Field(pattern=r'^imsi-[0-9]{5,15}$')]
    metric: Literal['service_access_rate', 'data_rate']
    source: Annotated[str, Field(min_length=1, max_length=200)]


class ExperienceRequest(StrictModel):
    supi: Annotated[str, Field(pattern=r'^imsi-[0-9]{5,15}$')]
    app_id: Annotated[str, Field(min_length=1, max_length=100)]
    timestamp: FiniteFloat
    document: dict


class EventSubscription(StrictModel):
    # Preserve the actual field spelling from the pinned V16.7.0 Annex A.2.
    event: Literal['SLICE_LOAD_LEVEL']
    snssaia: Annotated[list[Snssai], Field(min_length=1, max_length=1)]
    notificationMethod: Literal['PERIODIC']
    repetitionPeriod: Annotated[int, Field(strict=True, ge=1, le=3600)]


class Subscription(StrictModel):
    notificationURI: Annotated[str, Field(min_length=1, max_length=2048)]
    eventSubscriptions: Annotated[list[EventSubscription], Field(min_length=1, max_length=1)]


class DecisionEvent(StrictModel):
    event_id: UUID
    decision_id: UUID
    stage: Literal['DETECTED','N7_SENT','N7_ACK','ENFORCEMENT_VERIFIED','FAILED','CANCELLED']
    supi: Annotated[str, Field(pattern=r'^imsi-[0-9]{5,15}$')]
    snssai: Snssai
    policy_id: Annotated[str, Field(min_length=1,max_length=128)]
    action: Literal['mitigate','restore']
    nominal_mbr_bps: Annotated[int, Field(strict=True,gt=0)]
    target_mbr_bps: Annotated[int, Field(strict=True,gt=0)]
    evidence_ref: Annotated[str, Field(min_length=1,max_length=500)]
    # Only populated from a measured interval, never server receipt-time delta.
    elapsed_ms: Annotated[FiniteFloat, Field(ge=0)] | None = None
