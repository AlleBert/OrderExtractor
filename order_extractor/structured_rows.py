"""Validate model-selected source cells and build the versioned JSON output."""

import re
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from .core import MAX_ROWS, NumberStyle, parse_decimal
from .tables import strip_currency

CellID = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RowSelection(StrictModel):
    article: CellID | None = Field(validation_alias=AliasChoices("a", "article"))
    description: list[CellID] = Field(max_length=20, validation_alias=AliasChoices("d", "description"))
    quantity: CellID | None = Field(validation_alias=AliasChoices("q", "quantity"))
    unit_price: CellID | None = Field(validation_alias=AliasChoices("p", "unit_price"))


class PageSelection(StrictModel):
    rows: list[RowSelection] = Field(max_length=MAX_ROWS)


class StructuredRow(StrictModel):
    article: str | None
    description: str | None
    quantity: str | None
    unit_price: str | None
    page: int = Field(ge=1)


class ExtractionNotice(StrictModel):
    page: int = Field(ge=1)
    row: int = Field(ge=1)
    field: Literal["article", "description", "quantity", "unit_price"]
    code: Literal["missing", "unknown_source", "invalid_number"]


class StructuredDocument(StrictModel):
    schema_version: Literal[1] = 1
    rows: list[StructuredRow] = Field(max_length=MAX_ROWS)
    notices: list[ExtractionNotice]


def clean_number(value):
    value = " ".join(value.split())
    if re.fullmatch(r"[+-]?[0-9]{1,3}(?: [0-9]{3})+(?:[.,][0-9]+)?", value):
        return value.replace(" ", "")
    return value


def resolve_page(
    selection: PageSelection, cells: Mapping[str, str], page: int, number_style: NumberStyle
) -> StructuredDocument:
    rows, notices = [], []
    for index, selected in enumerate(selection.rows, 1):
        resolved = {}
        for field, references in selected.model_dump().items():
            identifiers = references if field == "description" else [references] if references else []
            code = None
            value = None
            if not identifiers:
                code = "missing"
            elif any(identifier not in cells for identifier in identifiers):
                code = "unknown_source"
            else:
                value = " ".join(cells[identifier].strip() for identifier in identifiers).strip() or None
                if value is None:
                    code = "missing"
                elif field in {"quantity", "unit_price"}:
                    raw = strip_currency(value)[0] if field == "unit_price" else value
                    try:
                        value = format(parse_decimal(clean_number(raw), number_style), "f")
                    except ValueError:
                        value = None
                        code = "invalid_number"
            if code is not None:
                notices.append(ExtractionNotice(page=page, row=index, field=field, code=code))
            resolved[field] = value
        rows.append(StructuredRow(page=page, **resolved))
    return StructuredDocument(rows=rows, notices=notices)
