import csv
import io
import os
import re
import subprocess
import time
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import ImageChops

from .manual import MAX_DOCUMENT_CACHE, cache_page, header_anchors
from .tables import Word, header, lines_for, parse_page

MAX_PAGES = 100
MAX_PIXELS = 25_000_000
def native_words(page):
    return [Word(word["text"], word["x0"] / page.width, word["top"] / page.height,
                 word["x1"] / page.width, word["bottom"] / page.height)
            for word in page.dedupe_chars().extract_words() if word["text"].strip()]


def raster(page, dpi):
    width, height = page.get_size()
    if not (0 < width <= 14400 and 0 < height <= 14400):
        raise ValueError("Dimensioni della pagina non supportate.")
    scale = min(dpi / 72, 0.995 * (MAX_PIXELS / (width * height)) ** .5)
    bitmap = page.render(scale=scale)
    try:
        return bitmap.to_pil().convert("RGB")
    finally:
        bitmap.close()


def without_rules(image):
    gray = image.convert("L")
    ink = gray.point(lambda value: 255 if value < 155 else 0)
    rules = ink.point(lambda _: 0)
    for horizontal, length in ((True, image.width // 8), (False, image.height // 22)):
        mask = ink
        hop = 1
        while hop < length:
            shift = (hop, 0) if horizontal else (0, hop)
            mask = ImageChops.darker(mask, ImageChops.offset(mask, *shift))
            hop *= 2
        hop //= 2
        while hop:
            shift = (-hop, 0) if horizontal else (0, -hop)
            mask = ImageChops.lighter(mask, ImageChops.offset(mask, *shift))
            hop //= 2
        rules = ImageChops.lighter(rules, mask)
    return ImageChops.lighter(gray, rules)


def raster_pymupdf(path, index):
    import pymupdf
    from PIL import Image

    with pymupdf.open(path) as source:
        page = source[index]
        scale = min(200 / 72, (MAX_PIXELS / page.rect.get_area()) ** .5 * .995)
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def rapid_words(image, engine):
    buffered = io.BytesIO()
    image.save(buffered, "PNG")
    detected, _ = engine(buffered.getvalue())
    width, height = image.size
    words = []
    for points, text, _ in detected or []:
        if text.strip():
            xs, ys = zip(*points)
            words.append(Word(text.strip(), max(0, min(xs)) / width, max(0, min(ys)) / height,
                              min(width, max(xs)) / width, min(height, max(ys)) / height))
    return words


def ocr_words(image, directory, mode=6, *, timeout=15):
    executable = os.environ.get("OE_TESSERACT", "tesseract")
    languages = os.environ.get("OE_OCR_LANGS", "eng")
    input_path = directory / "ocr.png"
    image.save(input_path)
    try:
        result = subprocess.run([executable, str(input_path), "stdout", "-l", languages, "--oem", "1",
                                 "--psm", str(mode), "-c", "tessedit_create_tsv=1"],
                                cwd=directory, capture_output=True, timeout=timeout, check=True,
                                env={**os.environ, "OMP_THREAD_LIMIT": "2"})
    finally:
        input_path.unlink(missing_ok=True)
    width, height = image.size
    words = []
    for item in csv.DictReader(io.StringIO(result.stdout.decode("utf-8", "replace")), delimiter="\t", quoting=csv.QUOTE_NONE):
        if item.get("text", "").strip() and item.get("level") == "5":
            x, y, w, h = (int(item[key]) for key in ("left", "top", "width", "height"))
            words.append(Word(item["text"], x / width, y / height, (x + w) / width, (y + h) / height))
    return words


OCR_FIELDS = ("article", "lot", "quantity", "unit_price")


def ocr_score(rows):
    complete = 0
    fields = 0
    for row in rows:
        found = sum(bool(row[key].strip()) for key in ("article", "description"))
        found += sum(bool(re.fullmatch(r"[+-]?[0-9]+(?:[.,][0-9]+)*", row[key].strip()))
                     for key in ("quantity", "unit_price"))
        fields += found
        complete += found == 4
    return complete, fields, complete - len(rows)


def ocr_candidates(image, directory, index, inherited):
    deadline = time.monotonic() + 28

    def read(source):
        for mode in (11, 6):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                lines = lines_for(ocr_words(source, directory, mode, timeout=min(remaining, 15)))
                rows = []
                parse_page(lines, index, "OCR Tesseract", inherited, rows)
                yield lines, rows
            except (OSError, subprocess.SubprocessError, ValueError):
                continue

    found = list(read(image))
    if not any(ocr_score(rows)[0] for _, rows in found):
        found.extend(read(without_rules(image)))
    return sorted(found, key=lambda reading: ocr_score(reading[1]), reverse=True)


def compare_ocr(rows, alternate):
    def center(row):
        _, top, _, bottom = row["source_spans"][0]["bbox"]
        return (top + bottom) / 2

    index = 0
    for row in rows:
        position = center(row)
        while index < len(alternate) and center(alternate[index]) < position - .004:
            index += 1
        if index == len(alternate) or abs(center(alternate[index]) - position) > .004:
            continue
        other = alternate[index]
        index += 1
        if not row["source_row_number"] and re.fullmatch(r"[0-9]{1,12}", other["source_row_number"]):
            row["source_row_number"] = other["source_row_number"]
        if ocr_score([row])[0] and ocr_score([other])[0]:
            differences = {key: other[key] for key in OCR_FIELDS if row[key] and other[key] and row[key] != other[key]}
            if differences:
                row["ocr_alternatives"] = differences
                if len(row["source_spans"]) < 20:
                    row["source_spans"].extend(other["source_spans"][:1])


def extract(path: Path, directory: Path, progress, *, rapid_ocr=False, text_only=False, use_pymupdf=False):
    rows, notices, engines = [], [], []
    layouts = set()
    mappable_pages = []
    anchors = []
    cache_budget = MAX_DOCUMENT_CACHE
    columns = []
    rapid_engine = None
    try:
        document = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as error:
        message = "Il documento è cifrato o richiede una password." if getattr(error, "err_code", None) == 4 else "PDF danneggiato o non valido."
        raise ValueError(message) from error
    with document, pdfplumber.open(path) as native:
        count = len(document)
        if not 1 <= count <= MAX_PAGES:
            raise ValueError("Sono supportati PDF da 1 a 100 pagine.")
        for index in range(count):
            progress(index, count)
            page = document[index]
            try:
                preview = raster(page, 120)
                preview.thumbnail((1260, 1600))
                preview.save(directory / f"{index}.png", optimize=False)
                if text_only:
                    native_page = native.pages[index]
                    layout = native_page.extract_text(layout=True) or ""
                    words = native_words(native_page) if layout.strip() else []
                    readable = bool(words) and sum("�" in word.text or "(cid:" in word.text for word in words) * 8 < len(words)
                    engine = "testo PDF"
                    if not readable:
                        from rapidocr_onnxruntime import RapidOCR

                        if rapid_engine is None:
                            rapid_engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
                        image = raster_pymupdf(path, index) if use_pymupdf else raster(page, 200)
                        words = rapid_words(image, rapid_engine)
                        engine = "OCR RapidOCR"
                    rules = []
                    if readable:
                        rules = sorted({
                            tuple(round(max(0, min(1, coord)), 4) for coord in
                                  (edge["top"] / native_page.height, edge["x0"] / native_page.width,
                                   edge["x1"] / native_page.width))
                            for edge in native_page.horizontal_edges if edge["width"] >= .14 * native_page.width
                        })[:1024]
                    cached = cache_page(directory, index, lines_for(words), cache_budget, engine, rules=rules)
                    if cached:
                        cache_budget -= cached
                        mappable_pages.append(index)
                    elif words:
                        raise ValueError(f"Pagina {index + 1}: testo oltre i limiti della pipeline AI.")
                    engines.append(engine)
                    continue
                words = native_words(native.pages[index])
                lines = lines_for(words)
                has_header = any(header(line) for line in lines)
                plausible = len(words) >= 8 and sum("�" in word.text or "(cid:" in word.text for word in words) * 8 < len(words)
                native_numbers = sum(any(char.isdigit() for char in word.text) for word in words)
                engine = "testo PDF"
                alternatives = []
                if rapid_ocr and not plausible:
                    try:
                        if rapid_engine is None:
                            from rapidocr_onnxruntime import RapidOCR

                            rapid_engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
                        rapid = rapid_words(raster(page, 200), rapid_engine)
                    except Exception:
                        rapid = []
                        rapid_ocr = False
                        notices.append("RapidOCR non disponibile. Verrà usato Tesseract.")
                    if rapid:
                        lines = lines_for(rapid)
                        engine = "OCR RapidOCR"
                        notices.append(f"Pagina {index + 1}: verifica nel PDF codici e valori letti con OCR.")
                if engine != "OCR RapidOCR" and (not plausible or not (has_header or columns and native_numbers >= 2)):
                    candidates = ocr_candidates(raster(page, 260), directory, index, columns)
                    if candidates and candidates[0][0] and (candidates[0][1] or not plausible):
                        lines = candidates[0][0]
                        alternatives = next((c[1] for c in candidates[1:] if ocr_score(c[1])[0]), [])
                        engine = "OCR Tesseract"
                        notices.append(f"Pagina {index + 1}: verifica nel PDF codici e valori letti con OCR.")
                    elif not candidates and not plausible:
                        notices.append(f"Pagina {index + 1}: OCR non disponibile o non riuscito. Controlla l’anteprima.")
                before = len(rows)
                active = parse_page(lines, index, engine, columns, rows, layouts)
                if alternatives:
                    compare_ocr(rows[before:], alternatives)
                if active:
                    columns = active
                if len(rows) == before:
                    notices.append(f"Pagina {index + 1}: nessuna nuova riga individuata.")
                cached = cache_page(directory, index, lines, cache_budget, engine)
                if cached:
                    cache_budget -= cached
                    mappable_pages.append(index)
                    anchors.extend(header_anchors(index, lines))
                engines.append(engine)
            finally:
                page.close()
    progress(count, count)
    return dict(rows=rows, page_count=count, notices=notices, engines=engines,
                layout_key=next(iter(layouts)) if rows and len(layouts) == 1 else None, mappable_pages=mappable_pages,
                header_anchors=anchors)
