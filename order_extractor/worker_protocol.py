"""Bounded files exchanged with the PDF worker."""

import os
import stat
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .core import MAX_ROWS, Coordinate, Row

MAX_RESULT = 32 * 1024**2
MAX_PREVIEW = 8 * 1024**2
MAX_HEADER_ANCHORS = 64


class Progress(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    checked: int = Field(ge=0, le=100)
    total: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def count(self):
        if self.checked > self.total:
            raise ValueError("Progresso fuori intervallo.")
        return self


class HeaderAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(ge=0, lt=100, strict=True)
    key: str = Field(pattern=r"^m1:[0-9a-f]{64}$")
    bottom: Coordinate


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_count: int = Field(ge=1, le=100, strict=True)
    rows: list[Row] = Field(max_length=MAX_ROWS)
    notices: list[Annotated[str, Field(max_length=300)]] = Field(max_length=300)
    engines: list[Literal["testo PDF", "OCR Tesseract", "OCR RapidOCR"]] = Field(max_length=100)
    layout_key: str | None = Field(default=None, pattern=r"^v1:[0-9a-f]{64}$")
    mappable_pages: list[Annotated[int, Field(ge=0, lt=100, strict=True)]] = Field(default_factory=list, max_length=100)
    header_anchors: list[HeaderAnchor] = Field(default_factory=list, max_length=100 * MAX_HEADER_ANCHORS)

    @model_validator(mode="after")
    def page_metadata(self):
        if len(self.engines) != self.page_count or any(
            not 0 <= span.page_index < self.page_count for row in self.rows for span in row.source_spans
        ) or any(page >= self.page_count for page in self.mappable_pages) or any(
            anchor.page not in self.mappable_pages for anchor in self.header_anchors
        ):
            raise ValueError("Metadati delle pagine non validi.")
        return self


class Failure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error: str = Field(max_length=500)


result_protocol = TypeAdapter(Result | Failure)


def read_artifact(path, limit):
    if path.is_symlink() or path.is_junction() or path.parent.is_symlink() or path.parent.is_junction():
        raise ValueError("Collegamento all’artefatto del worker.")
    if path.resolve(strict=True).parent != path.parent.resolve(strict=True):
        raise ValueError("Artefatto del worker fuori directory.")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1 or info.st_size > limit:
            raise ValueError("Tipo o dimensione artefatto non valido.")
        payload = source.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("Dimensione artefatto oltre il limite.")
    return payload
