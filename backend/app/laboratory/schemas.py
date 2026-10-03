from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExperimentCreate(StrictModel):
    title: str = Field(min_length=3, max_length=160)
    question: str = Field(min_length=10, max_length=2000)
    hypothesis: str = Field(min_length=10, max_length=4000)


class Descriptor(StrictModel):
    schema_version: Literal[1] = 1
    template: Literal["qoe_closed_loop"] = "qoe_closed_loop"
    template_version: Literal[1] = 1
    scenario: Literal["5g-sa"] = "5g-sa"
    observed_ue: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    competing_ue: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    load_mbps: list[float] = Field(default_factory=list, max_length=12)
    repetitions: int | None = Field(default=None, ge=1, le=30, strict=True)
    measurement_seconds: int | None = Field(default=None, ge=10, le=300, strict=True)
    campaign_budget_bytes: int | None = Field(default=None, ge=1, le=10**12, strict=True)
    capture_budget_bytes: int | None = Field(default=None, ge=1, le=10**11, strict=True)
    seed: int = Field(default=42017, ge=0, le=2**31 - 1, strict=True)
    primary_metric: Literal["player_startup_delay_seconds"] = "player_startup_delay_seconds"
    design: Literal["paired_randomized_blocks"] = "paired_randomized_blocks"

    @field_validator("load_mbps", mode="before")
    @classmethod
    def numeric_levels(cls, values):
        if not isinstance(values, list) or any(type(v) not in (int, float) for v in values):
            raise ValueError("Los niveles deben ser números finitos.")
        return values

    @field_validator("load_mbps")
    @classmethod
    def bounded_levels(cls, values):
        import math

        if any(not math.isfinite(v) or not 0 <= v <= 1000 for v in values):
            raise ValueError("Carga admitida para diseño: 0 a 1000 Mbps; requiere calibración real.")
        if len(set(values)) != len(values):
            raise ValueError("Los niveles de carga no pueden repetirse.")
        return values


class CampaignCreate(StrictModel):
    revision_id: str = Field(min_length=1, max_length=64)


class AssignmentCreate(StrictModel):
    username: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    observed_ue: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    competing_ue: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    mode: Literal["dry_run", "real"] = "dry_run"
    template: Literal["qoe_closed_loop"] = "qoe_closed_loop"
    validity_hours: int = Field(default=24, ge=1, le=168, strict=True)
    max_runs: int = Field(default=24, ge=2, le=720, strict=True)
    max_load_mbps: float = Field(default=100, ge=0, le=1000, allow_inf_nan=False)
    max_traffic_bytes: int = Field(default=1_000_000_000, ge=1, le=10**12, strict=True)
    max_capture_bytes: int = Field(default=100_000_000, ge=1, le=10**11, strict=True)
    max_jobs: int = Field(default=3, ge=1, le=100, strict=True)


class ExecutionStart(StrictModel):
    assignment_id: str = Field(min_length=1, max_length=64)
    mode: Literal["dry_run", "real"] = "dry_run"


class BindingCreate(StrictModel):
    observed_supi: str = Field(pattern=r"^imsi-\d{14,15}$")
    competing_supi: str = Field(pattern=r"^imsi-\d{14,15}$")
    dnn: Literal["internet"] = "internet"


class PreflightCreate(StrictModel):
    assignment_id: str = Field(min_length=1, max_length=64)


class NotebookCreate(StrictModel):
    prediction: str = Field(min_length=10, max_length=4000)
    conclusion: str = Field(max_length=8000, default="")
    next_test: str = Field(max_length=4000, default="")
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)
    outcome: Literal["pending", "supported", "not_supported", "inconclusive"] = "pending"
