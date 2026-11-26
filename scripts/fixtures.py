import argparse
import io
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageEnhance
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

ROWS = [
    ("1", "00042", "", "Vite M6 zincata", "100", "0,25"),
    ("2", "ART003", "L250", "Bullone M10", "200", "0,45"),
    ("3", "A-0009", "L007", "Rondella piana", "12", "1,75"),
]
NAMES = (
    ("Riga", "Articolo", "Lotto", "Descrizione", "Q.tà", "Prezzo unitario"),
    ("Pos", "Codice articolo", "Lotto", "Descrizione", "Quantità", "Prezzo"),
    ("Line", "Item code", "Batch", "Description", "Qty", "Unit price"),
)


def page(output, rows=ROWS, variant=0, *, grid=False, multiline=False):
    names = NAMES[variant % len(NAMES)]
    positions = (36, 89, 191, 253, 423, 478)
    output.setFont("Helvetica-Bold", 18)
    output.drawString(35, 790, "ORDINE DI ACQUISTO")
    output.setFont("Helvetica", 10)
    output.drawString(36, 769, "Documento sintetico • OE-2026-001")
    output.setFont("Helvetica-Bold", 10)
    for x, text in zip(positions, names):
        output.drawString(x, 719, text)
    output.line(35, 712, 560, 712)
    output.setFont("Helvetica", 10)
    for index, record in enumerate(rows):
        y = 690 - index * (33 if multiline else 23)
        for x, text in zip(positions, record):
            output.drawString(x, y, text)
        if multiline and index == 0:
            output.drawString(positions[3], y - 12, "Finitura opaca")
        if grid:
            output.line(35, y - 7, 560, y - 7)
    if grid:
        for x in (35, 83, 185, 247, 417, 472, 560):
            output.line(x, 732, x, 616)
    output.drawString(35, 550, "Totale documento")
    output.drawString(490, 550, "136,00")
    output.drawString(35, 44, f"Pagina {output.getPageNumber()}")
    output.showPage()


def build(path, variant=0, *, scan=False, mixed=False, multipage=False, tilt=0, grid=False, multiline=False):
    raw = io.BytesIO()
    output = canvas.Canvas(raw, pagesize=A4, invariant=True)
    page(output, variant=variant, grid=grid, multiline=multiline)
    output.save()
    final = canvas.Canvas(str(path), pagesize=A4, invariant=True)
    if not scan:
        page(final, variant=variant, grid=grid, multiline=multiline)
        if multipage and not mixed:
            page(final, variant=variant)
    if scan or mixed:
        with pdfium.PdfDocument(raw.getvalue()) as document:
            item = document[0]
            bitmap = item.render(scale=3)
            image = bitmap.to_pil().convert("RGB")
            bitmap.close()
            item.close()
        image = ImageEnhance.Contrast(image).enhance(.85)
        if tilt:
            image = image.rotate(tilt, expand=False, fillcolor="white", resample=Image.Resampling.BICUBIC)
        final.drawImage(ImageReader(image), 0, 0, *A4)
        final.showPage()
    final.save()


def corpus(folder):
    folder.mkdir(parents=True, exist_ok=True)
    cases = {}
    for index in range(30):
        path = folder / f"ordine-{index + 1:02}.pdf"
        scan = 18 <= index < 25
        mixed = 25 <= index < 28
        multipage = 10 <= index < 15
        multiline = index in {2, 5, 8, 16}
        build(path, index, scan=scan, mixed=mixed, multipage=multipage,
              tilt=.35 if index in {22, 23} else 0, grid=index % 4 == 1, multiline=multiline)
        cases[path] = {"scan": scan, "mixed": mixed, "multipage": multipage, "multiline": multiline}
    return cases


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    corpus(args.directory)
