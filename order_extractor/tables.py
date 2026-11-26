import hashlib
import json
import re
import uuid
from dataclasses import dataclass

from rapidfuzz import fuzz

from .core import MAX_ROWS, fold

ALIASES = {
    "article": ("articolo", "codice", "codice articolo", "cod articolo", "sku", "item", "item code", "materiale", "part number"),
    "description": ("descrizione", "descrizione articolo", "description", "product description", "prodotto"),
    "quantity": ("qta", "qtà", "q ta", "quantità", "qty", "quantity", "q ty", "quantita ordinata"),
    "unit_price": ("prezzo", "prezzo unitario", "unit price", "prezzo un", "price", "prezzo unit", "prezzo netto"),
    "lot": ("lotto", "batch", "lot", "lot number"),
    "source_row_number": ("riga", "n riga", "numero riga", "pos", "posizione", "line", "nr", "n"),
    "line_total": ("totale", "totale riga", "importo", "amount", "line total", "subtotal"),
    "ignored": ("um", "u m", "unità", "uom", "sconto", "discount", "iva", "vat", "delivery", "consegna"),
}
REQUIRED = {"article", "description", "quantity", "unit_price"}
FOOTER = re.compile(r"^(?:subtotale|totale|total|grand total|trasporto|imponibile|iva|pagina|page)(?:\s|[:.]|$)", re.I)
CURRENCY = {"€": "EUR", "EUR": "EUR", "$": "USD", "USD": "USD", "£": "GBP", "GBP": "GBP", "CHF": "CHF"}


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


def lines_for(words):
    lines = []
    for word in sorted(words, key=lambda word: (word.y0 + word.y1, word.x0)):
        mid = (word.y0 + word.y1) / 2
        if lines and abs(mid - lines[-1][0]) <= max(0.003, (word.y1 - word.y0) * 0.48):
            lines[-1][1].append(word)
        else:
            lines.append((mid, [word]))
    return [sorted(words, key=lambda word: word.x0) for _, words in lines]


def alias(text):
    normalized = fold(text)
    if len(normalized) < 4:
        return next((field for field, names in ALIASES.items() if normalized in map(fold, names)), None)
    ranks = sorted(((max(fuzz.ratio(normalized, fold(name)) for name in names), field)
                    for field, names in ALIASES.items()), reverse=True)
    return ranks[0][1] if ranks[0][0] >= 90 and ranks[0][0] - ranks[1][0] >= 10 else None


def header(line):
    matches = []
    position = 0
    while position < len(line):
        for size in range(min(3, len(line) - position), 0, -1):
            words = line[position:position + size]
            if any(right.x0 - left.x1 > .045 for left, right in zip(words, words[1:])):
                continue
            field = alias(" ".join(word.text for word in words))
            if field:
                matches.append((words[0].x0, field))
                position += size - 1
                break
        position += 1
    fields = {field for _, field in matches}
    return matches if len(fields & REQUIRED) >= 3 and {"article", "description"} & fields else []


def split_cells(line, columns, *, exact=False):
    cells = {}
    for word in line:
        field = columns[0][1]
        for index, (x, candidate) in enumerate(columns[1:], 1):
            previous_x = columns[index - 1][0]
            tolerance = 0 if exact else min(.024, (x - previous_x) * .30)
            if word.x0 >= x - tolerance:
                field = candidate
            else:
                break
        cells.setdefault(field, []).append(word.text)
    return {field: " ".join(words) for field, words in cells.items()}


def layout_key(line, columns):
    fields = [field for _, field in columns]
    if any(fields.count(required) != 1 for required in REQUIRED):
        return None
    labels = split_cells(line, columns)
    geometry = [(field, fold(labels.get(field, "")), round(position * 64)) for position, field in columns]
    encoded = json.dumps(geometry, separators=(",", ":")).encode("utf-8")
    return "v1:" + hashlib.sha256(encoded).hexdigest()


def strip_currency(value):
    for token, currency in CURRENCY.items():
        if value.startswith(token) or value.endswith(token):
            return (value.removeprefix(token).removesuffix(token).strip(), currency)
    return value, ""


def source(line, page, engine):
    return dict(page_index=page, engine=engine, raw_text=" ".join(word.text for word in line)[:3000],
                bbox=[round(max(0, min(1, point)), 5) for point in
                      (min(w.x0 for w in line), min(w.y0 for w in line), max(w.x1 for w in line), max(w.y1 for w in line))])


def parse_page(lines, page, engine, inherited, rows, layouts=None, *, manual=False):
    columns = inherited
    previous_line = None
    for line in lines:
        found = [] if manual else header(line)
        if found:
            if layouts is not None:
                layouts.add(layout_key(line, found))
            columns = found
            previous_line = None
            continue
        if not manual and FOOTER.match(" ".join(word.text for word in line)):
            columns = []
            previous_line = None
            continue
        if not columns:
            continue
        cells = split_cells(line, columns, exact=manual)
        if cells.get("article") or cells.get("quantity") or cells.get("unit_price"):
            price, currency = strip_currency(cells.get("unit_price", ""))
            total, total_currency = strip_currency(cells.get("line_total", ""))
            rows.append(dict(row_id=str(uuid.uuid4()), article=cells.get("article", "")[:160],
                             lot=cells.get("lot", "")[:160], description=cells.get("description", "")[:3000],
                             quantity=cells.get("quantity", "")[:80], unit_price=price[:80],
                             line_total=total[:80], currency="MIXED" if currency and total_currency
                             and currency != total_currency else currency or total_currency,
                             source_row_number=cells.get("source_row_number", "")[:80],
                             source_spans=[source(line, page, engine)]))
            previous_line = line
        elif set(cells) == {"description"} and rows and len(rows[-1]["source_spans"]) < 20 and (
            previous_line and line[0].y0 - previous_line[0].y1 < .035
            or line[0].y0 < .15 and rows[-1]["source_spans"][-1]["page_index"] == page - 1
        ):
            rows[-1]["description"] = (rows[-1]["description"] + " " + cells["description"]).strip()[:3000]
            rows[-1]["source_spans"].append(source(line, page, engine))
            previous_line = line
        if len(rows) > MAX_ROWS:
            raise ValueError("Il documento supera il limite di 1500 righe. Suddividilo prima di elaborarlo.")
    return columns
