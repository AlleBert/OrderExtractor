import os
import shutil
import subprocess
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from order_extractor.core import Revision, Row, export, parse_decimal, validate
from order_extractor.extraction import compare_ocr, extract
from order_extractor.tables import Word, parse_page
from scripts.fixtures import build

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = (ROOT / "fixtures/synthetic/expected.csv.txt").read_text().replace("\n", "\r\n").encode()


def test_golden(tmp_path):
    result = extract(ROOT / "order_extractor/sample.pdf", tmp_path, lambda *_: None)
    assert export(Revision(revision=0, rows=result["rows"], reviewed=True)) == GOLDEN
    assert result["page_count"] == 1
    assert all(all(0 <= x <= 1 for x in row["source_spans"][0]["bbox"]) for row in result["rows"])


@pytest.mark.parametrize("variant", range(3))
def test_native_multipage_and_duplicate_rows(tmp_path, variant):
    path = tmp_path / "order.pdf"
    build(path, variant, multipage=True, grid=True, multiline=True)
    result = extract(path, tmp_path, lambda *_: None)
    assert result["page_count"] == 2
    assert [row["article"] for row in result["rows"]] == ["00042", "ART003", "A-0009"] * 2
    assert result["rows"][0]["description"] == "Vite M6 zincata Finitura opaca"


@pytest.mark.parametrize("mixed", [False, True])
def test_ocr(tmp_path, mixed):
    path = tmp_path / "order.pdf"
    build(path, scan=not mixed, mixed=mixed)
    result = extract(path, tmp_path, lambda *_: None)
    assert result["engines"] == (["testo PDF"] if mixed else []) + ["OCR Tesseract"]
    assert [row["article"] for row in result["rows"]][-3:] == ["00042", "ART003", "A-0009"]
    assert [row["unit_price"] for row in result["rows"]][-3:] == ["0,25", "0,45", "1,75"]


def test_ocr_reads_coordinates_from_model_only_directory(tmp_path, monkeypatch):
    executable = os.environ.get("OE_TESSERACT", "tesseract")
    header = subprocess.check_output([executable, "--list-langs"], text=True).splitlines()[0].split('"')
    if len(header) < 3 or not (Path(header[1]) / "eng.traineddata").is_file():
        pytest.skip("Directory del modello inglese non rilevabile dal runtime.")
    models = tmp_path / "models"
    models.mkdir()
    shutil.copyfile(Path(header[1]) / "eng.traineddata", models / "eng.traineddata")
    monkeypatch.setenv("TESSDATA_PREFIX", str(models))
    monkeypatch.setenv("OE_OCR_LANGS", "eng")
    path = tmp_path / "scan.pdf"
    build(path, scan=True)
    result = extract(path, tmp_path, lambda *_: None)
    assert [row["article"] for row in result["rows"]] == ["00042", "ART003", "A-0009"]
    assert result["rows"][0]["source_spans"][0]["bbox"][1] > 0


@pytest.mark.parametrize("value,style,expected", [("1.234,56","it","1234.56"), ("1,234.56","en","1234.56"),
    ("0","auto","0"), ("0,2500","it","0.2500"), ("-0.00","en","0.00")])
def test_decimal(value, style, expected):
    assert format(parse_decimal(value, style), "f") == expected


@pytest.mark.parametrize("value,style", [("1.234","auto"), ("1,234","auto"), ("1.234,56","auto"), ("1e4","en"),
    ("12,00","en"), ("1.2.3","it"), ("1,","it")])
def test_decimal_invalid(value, style):
    with pytest.raises(ValueError):
        parse_decimal(value, style)


@pytest.mark.parametrize("value", ["=SUM(A1)", "   @example", "\rnormal", "-001", "＋abc"])
def test_formula(value):
    data = Revision(revision=0, rows=[Row(row_id="row", article=value, description="s", quantity="1", unit_price="0")])
    assert any(item["field"] == "article" and item["severity"] == "blocking" for item in validate(data))
    with pytest.raises(ValueError):
        export(data.model_copy(update={"reviewed": True}))


def test_quotes_currency_and_confirmation():
    data = Revision(revision=0, rows=[Row(row_id="row", article="01", description='a;"c"\nz', quantity="1",
                                              unit_price="0.00", currency="USD")])
    assert validate(data)[0]["severity"] == "blocking"
    with pytest.raises(ValueError):
        export(data.model_copy(update={"currency": "USD"}))
    payload = export(data.model_copy(update={"currency": "USD", "reviewed": True}))
    assert b'1;01;;"a;""c""\nz";1;0.00\r\n' in payload


def test_damaged_and_encrypted_pdf(tmp_path):
    file = tmp_path / "damaged.pdf"
    file.write_bytes(b"%PDF-1.7\ninvalid")
    with pytest.raises(ValueError, match="danneggiato"):
        extract(file, tmp_path, lambda *_: None)
    file = tmp_path / "encrypted.pdf"
    doc = canvas.Canvas(str(file), encrypt="test-only")
    doc.drawString(20, 20, "Sintetico")
    doc.showPage()
    doc.save()
    with pytest.raises(ValueError, match="cifrato"):
        extract(file, tmp_path, lambda *_: None)


def test_ocr_alternative_preserves_draft_and_source():
    columns = [(0.1, "source_row_number"), (0.2, "article"), (0.3, "lot"),
               (0.4, "description"), (0.7, "quantity"), (0.8, "unit_price")]

    def read(row_number, lot):
        data = [row_number, "A-01", lot, "Vite", "12", "0,25"]
        line = [Word(value, x, .24, x + .01, .25) for (x, _), value in zip(columns, data) if value]
        rows = []
        parse_page([line], 0, "OCR", columns, rows)
        return rows

    sparse = read("", "L007")
    block = read("1", "LOO7")
    compare_ocr(sparse, block)
    draft = Revision(revision=0, rows=sparse)
    row = draft.rows[0]
    assert row.source_row_number == "1"
    assert row.lot == "L007"
    assert row.ocr_alternatives == {"lot": "LOO7"}
    assert "LOO7" in row.source_spans[-1].raw_text
    assert [(item["field"], item["severity"]) for item in validate(draft)] == [("lot", "warning")]
    altered = draft.model_copy(update={"rows": [row.model_copy(update={"lot": "LOO7"})]})
    assert not validate(altered)
