import csv
import io
import re
import unicodedata
from decimal import Decimal, localcontext
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_ROWS = 1500
FIELDS = ("article", "lot", "description", "quantity", "unit_price")
LABELS = dict(zip(FIELDS, ("Articolo", "Lotto", "Descrizione", "Quantità", "Prezzo unitario")))
CSV_HEADER = ("numero_riga", "articolo", "lotto", "descrizione", "quantita", "prezzo_unitario")
NumberStyle = Literal["auto", "it", "en"]
OcrField = Literal["article", "lot", "quantity", "unit_price"]
Coordinate = Annotated[float, Field(ge=0, le=1)]


class Span(BaseModel):
    page_index: int = Field(ge=0, lt=100)
    bbox: tuple[Coordinate, Coordinate, Coordinate, Coordinate]
    engine: str = Field(max_length=80)
    raw_text: str = Field(max_length=3000)


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row_id: str = Field(max_length=64)
    source_row_number: str = Field(default="", max_length=80)
    article: str = Field(default="", max_length=160)
    lot: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=3000)
    quantity: str = Field(default="", max_length=80)
    unit_price: str = Field(default="", max_length=80)
    line_total: str = Field(default="", max_length=80)
    currency: str = Field(default="", max_length=10)
    source_spans: list[Span] = Field(default_factory=list, max_length=20)
    ocr_alternatives: dict[OcrField, Annotated[str, Field(max_length=160)]] = Field(default_factory=dict, max_length=4)


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    rows: list[Row] = Field(max_length=MAX_ROWS)
    number_style: NumberStyle = "auto"
    currency: Literal["", "EUR", "USD", "GBP", "CHF"] = ""
    reviewed: bool = False


def fold(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", text.casefold())
                               .encode("ascii", "ignore").decode()))


def parse_decimal(value: str, style: NumberStyle) -> Decimal:
    value = value.strip().replace("−", "-")
    if not re.fullmatch(r"[+-]?[0-9][0-9.,]{0,60}", value):
        raise ValueError("Inserisci un numero decimale valido.")
    if style == "auto":
        if "." in value and "," in value:
            raise ValueError("Seleziona il formato numerico del documento.")
        if re.fullmatch(r"[+-]?[0-9]{1,3}[.,][0-9]{3}", value):
            raise ValueError("Separatore ambiguo. Scegli il formato del documento.")
        separator = "," if "," in value else "."
        group = ""
    else:
        separator, group = (",", ".") if style == "it" else (".", ",")
    integer, point, fraction = value.partition(separator)
    sign = integer[:1] if integer[:1] in {"+", "-"} else ""
    digits = integer[len(sign):]
    if group and group in digits:
        if not re.fullmatch(rf"[0-9]{{1,3}}(?:{re.escape(group)}[0-9]{{3}})+", digits):
            raise ValueError("Raggruppamento delle migliaia non valido.")
        digits = digits.replace(group, "")
    if not digits.isdigit() or (point and not fraction.isdigit()):
        raise ValueError("Separatore decimale non valido.")
    result = Decimal(sign + digits + ("." + fraction if point else ""))
    return result.copy_abs() if result.is_zero() else result


def validate(data: Revision) -> list[dict]:
    issues = []

    def issue(row_id, field, message, severity="blocking"):
        issues.append(dict(row_id=row_id, field=field, message=message, severity=severity))

    if not data.rows:
        issue(None, None, "Aggiungi almeno una riga.")
    if len(set(row.row_id for row in data.rows)) != len(data.rows):
        issue(None, None, "Identificatori di riga duplicati.")
    for row in data.rows:
        for field in ("article", "lot", "description"):
            value = getattr(row, field)
            if not value.strip() and field != "lot":
                issue(row.row_id, field, f"{LABELS[field]} obbligatorio.")
            if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@", "＝", "＋", "－", "＠")):
                issue(row.row_id, field, "Valore interpretabile come formula da un foglio di calcolo.")
            if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
                issue(row.row_id, field, "Carattere di controllo non consentito.")
        numbers = {}
        for field in ("quantity", "unit_price"):
            try:
                numbers[field] = parse_decimal(getattr(row, field), data.number_style)
            except ValueError as error:
                issue(row.row_id, field, str(error))
        if row.currency and (not data.currency or row.currency != data.currency):
            issue(row.row_id, "unit_price", f"Valuta {row.currency} da verificare nel profilo di esportazione.")
        if row.line_total and row.currency and row.currency == data.currency and len(numbers) == 2:
            try:
                total = parse_decimal(row.line_total, data.number_style)
                with localcontext() as context:
                    context.prec = 128
                    if abs(total - numbers["quantity"] * numbers["unit_price"]) > Decimal("0.01"):
                        issue(row.row_id, "unit_price", "Totale diverso da quantità × prezzo. Verifica sconti e unità.", "warning")
            except ValueError:
                issue(row.row_id, "unit_price", "Totale di riga da verificare.", "warning")
        for field, alternate in row.ocr_alternatives.items():
            if getattr(row, field) != alternate:
                issue(row.row_id, field, f"{LABELS[field]} da verificare. Lettura OCR alternativa: «{alternate}».", "warning")
    return issues


def export(data: Revision) -> bytes:
    if not data.reviewed or any(issue["severity"] == "blocking" for issue in validate(data)):
        raise ValueError("Conferma la revisione e risolvi gli errori prima di esportare.")
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for index, row in enumerate(data.rows, 1):
        writer.writerow((index, row.article, row.lot, row.description,
                         format(parse_decimal(row.quantity, data.number_style), "f"),
                         format(parse_decimal(row.unit_price, data.number_style), "f")))
    return output.getvalue().encode("utf-8")
