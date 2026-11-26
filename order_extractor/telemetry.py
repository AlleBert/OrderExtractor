from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    timestamp: datetime
    event_code: Literal["PDF_INVALID", "EXPORT_SUCCESS", "COLUMN_UNMAPPED"]
    severity: Literal["info", "warning"]
    app_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=24)
    page_count: int | None = Field(default=None, ge=1, le=100)
    row_count: int | None = Field(default=None, ge=0, le=1500)

    @field_validator("timestamp")
    @classmethod
    def utc(cls, value):
        if value.utcoffset() is None:
            raise ValueError("Indicare il fuso orario.")
        return value.astimezone(timezone.utc)


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[Event] = Field(min_length=1, max_length=40)
