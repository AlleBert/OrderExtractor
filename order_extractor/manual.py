"""Map a user-selected table from bounded text geometry, without reopening a PDF."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .core import MAX_ROWS, Coordinate, Row, fold
from .tables import REQUIRED, Word, parse_page
from .worker_protocol import MAX_HEADER_ANCHORS, read_artifact

MAX_WORDS = 16_000
MAX_PAGE_CACHE = 2 * 1024**2
MAX_DOCUMENT_CACHE = 24 * 1024**2
FieldName = Literal["article", "lot", "description", "quantity", "unit_price", "line_total", "source_row_number"]


class ColumnRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    right: Coordinate = 1
    columns: dict[FieldName, Coordinate] = Field(max_length=7)

    @model_validator(mode="after")
    def check_edges(self):
        edges = sorted(self.columns.values())
        if not REQUIRED.issubset(self.columns):
            raise ValueError("Indica articolo, descrizione, quantità e prezzo unitario.")
        if edges[-1] >= self.right or any(round(b - a, 7) < .002 for a, b in zip(edges, edges[1:])):
            raise ValueError("Controlla i limiti della tabella e le posizioni delle colonne.")
        return self


class PageMapping(ColumnRules):
    revision: int = Field(ge=0)
    top: Coordinate
    bottom: Coordinate

    @model_validator(mode="after")
    def check_vertical(self):
        if self.top >= self.bottom:
            raise ValueError("La fine delle righe deve essere sotto l’inizio.")
        return self


class CachedWord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=500)
    x0: Coordinate
    y0: Coordinate
    x1: Coordinate
    y1: Coordinate

    @model_validator(mode="after")
    def check_edges(self):
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("Coordinate del testo fuori intervallo.")
        return self


class CachedText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: Literal["testo PDF", "OCR Tesseract", "OCR RapidOCR"]
    lines: list[list[CachedWord]] = Field(max_length=MAX_WORDS)


def cache_page(directory, index, lines, remaining, engine):
    count = sum(len(line) for line in lines)
    if not 0 < count <= MAX_WORDS or remaining <= 0:
        return 0
    if sum(len(word.text) for line in lines for word in line) > 350_000:
        return 0
    encoded = {"engine": engine, "lines": [[{"text": word.text[:500], **{
        key: round(max(0, min(1, getattr(word, key))), 5) for key in ("x0", "y0", "x1", "y1")
    }} for word in line] for line in lines if line]}
    payload = json.dumps(encoded, ensure_ascii=False, separators=(",", ":")).encode()
    if len(payload) > min(remaining, MAX_PAGE_CACHE):
        return 0
    (directory / f"text-{index}.json").write_bytes(payload)
    return len(payload)


def header_anchors(index, lines):
    result = []
    for line in lines:
        if not 3 <= len(line) <= 20:
            continue
        labels = [(fold(word.text), round(word.x0 * 64)) for word in line]
        words = [label for label, _ in labels if label]
        if (len(words) < 3 or any(character.isdigit() for label in words for character in label)
                or sum(map(len, words)) > 140 or line[-1].x0 - line[0].x0 < .20):
            continue
        digest = hashlib.sha256(json.dumps(labels, separators=(",", ":")).encode()).hexdigest()
        result.append(dict(page=index, key="m1:" + digest, bottom=round(max(0, min(1, max(word.y1 for word in line))), 5)))
        if len(result) >= MAX_HEADER_ANCHORS:
            break
    return result


def confirmed_header(anchors, index, mapping):
    candidates = [anchor for anchor in anchors if anchor.page == index and 0 <= mapping.top - anchor.bottom <= .08]
    if not candidates:
        return None
    closest = max(candidates, key=lambda anchor: anchor.bottom)
    if sum(anchor.page == index and anchor.key == closest.key for anchor in anchors) != 1:
        return None
    return closest.key


def read_mapping(directory, index, mapping):
    payload = read_artifact(directory / f"text-{index}.json", MAX_PAGE_CACHE)
    text = CachedText.model_validate_json(payload)
    if sum(len(line) for line in text.lines) > MAX_WORDS:
        raise ValueError("Testo della pagina oltre il limite.")
    columns = sorted((position, field) for field, position in mapping.columns.items())
    left = columns[0][0]
    selection = [[Word(**word.model_dump()) for word in line
                  if left <= word.x0 < mapping.right and mapping.top <= (word.y0 + word.y1) / 2 <= mapping.bottom]
                 for line in text.lines]
    rows = []
    parse_page([line for line in selection if line], index, text.engine, columns, rows, manual=True)
    if not rows:
        raise ValueError("Non ci sono righe nella zona scelta. Controlla i limiti della tabella.")
    return [Row.model_validate(row) for row in rows]


def replace_page(rows, index, mapped):
    remaining = []
    insert_at = None
    for row in rows:
        pages = {span.page_index for span in row.source_spans}
        if index in pages:
            if len(pages) > 1:
                raise ValueError("Una riga attraversa più pagine. Usa la griglia per correggere le righe della pagina.")
            if insert_at is None:
                insert_at = len(remaining)
            continue
        if insert_at is None and pages and min(pages) > index:
            insert_at = len(remaining)
        remaining.append(row)
    if len(remaining) + len(mapped) > MAX_ROWS:
        raise ValueError("La nuova tabella supera il limite complessivo di righe.")
    if insert_at is None:
        insert_at = len(remaining)
    return remaining[:insert_at] + mapped + remaining[insert_at:]
