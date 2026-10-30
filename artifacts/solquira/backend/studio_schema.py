"""Versioned, English-only admission contracts for the Solquira Studio."""
from typing import Literal
from datetime import date, datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StudioScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    profile_id: Literal["valle", "secano", "precordillera"]
    water_percent: int = Field(ge=40, le=120)
    previous_crop: Literal["cereal", "leguminosa", "cubierta", "descanso"]

    @field_validator("water_percent")
    @classmethod
    def finite_demo_steps(cls, value: int) -> int:
        if value % 10:
            raise ValueError("Water must use ten-percent steps.")
        return value


class StudioFieldRecord(BaseModel):
    """Explicitly attached, self-reported categories; never free-text notes."""
    model_config = ConfigDict(extra="forbid", strict=True)
    observed_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    soil_cover: Literal["unknown", "mostly_bare", "patchy", "well_covered"]
    water_access: Literal["unknown", "reliable", "limited", "unreliable"]
    constraint: Literal["none", "water", "cost", "tools", "time", "advice", "permission"]

    @field_validator("observed_at")
    @classmethod
    def observation_date(cls, value: str) -> str:
        observed = date.fromisoformat(value)
        age = (datetime.now(timezone.utc).date() - observed).days
        if not 0 <= age <= 365:
            raise ValueError("Use an observation date within the past year, not a future date.")
        return value


class StudioQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scenario: StudioScenario
    query: str = Field(min_length=1, max_length=1000)
    context_ref: str | None = Field(default=None, min_length=24, max_length=100, pattern=r"^slq-context-[A-Za-z0-9_-]+$")
    field_record: StudioFieldRecord | None = None

    @field_validator("query")
    @classmethod
    def useful_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter a question about this scenario.")
        return value


class StudioLocation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

