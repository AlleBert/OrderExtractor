import json

import httpx
import pytest
from reportlab.pdfgen.canvas import Canvas

from order_extractor.core import Revision, export
from order_extractor.extraction import extract
from order_extractor.local_ai import MODEL, infer_page, read_ai_page


def synthetic(path, *, borders, article="CODICE ART.", price="PREZZO UNIT.", multiline=False):
    pdf = Canvas(str(path), pagesize=(595, 842))
    pdf.setFont("Helvetica", 9)
    for x, text in zip((40, 102, 185, 390, 486), ("POS.", article, "DESCRIZIONE", "QTA", price), strict=True):
        pdf.drawString(x, 720, text)
    for y, row in [
        (685, ("10", "A-01-" if multiline else "A-01", "Componente standard", "2,00", "€ 1.250,00")),
        (605, ("20", "B-02", "Guarnizione", "6,00", "€ 12,50")),
    ]:
        for x, text in zip((40, 102, 185, 390, 486), row, strict=True):
            pdf.drawString(x, y, text)
    if borders:
        for y in (705, 620, 560):
            pdf.line(181, y, 345, y)
    if multiline:
        pdf.drawString(102, 671, "INOX")
        pdf.drawString(185, 632, "finitura zincata")
    pdf.drawString(40, 533, "Totale ordine")
    pdf.showPage()
    pdf.drawString(40, 720, "Castelletto IVA")
    pdf.drawString(40, 700, "Descrizione aliquota")
    pdf.drawString(300, 700, "Imponibile")
    pdf.drawString(40, 680, "22,00")
    pdf.drawString(300, 680, "120,00")
    pdf.save()


def unavailable(request):
    pytest.fail(f"An unnecessary request reached Ollama: {request.url.path}")


@pytest.mark.parametrize("borders", [True, False])
def test_known_headers_use_no_model_and_export_verified_rows(tmp_path, borders):
    source = tmp_path / "order.pdf"
    synthetic(source, borders=borders, multiline=borders)
    metadata = extract(source, tmp_path, lambda *_: None, text_only=True)
    assert metadata["mappable_pages"] == [0, 1]
    debug = {}
    rows, _ = infer_page(read_ai_page(tmp_path, 0), 0, "auto", transport=httpx.MockTransport(unavailable), debug=debug)
    assert debug["mode"] == ("bordi_pdf" if borders else "coordinate")
    assert [(row.article, row.quantity, row.unit_price) for row in rows] == [
        ("A-01-INOX" if borders else "A-01", "2.00", "1250.00"), ("B-02", "6.00", "12.50"),
    ]
    if borders:
        assert rows[0].description == "Componente standard finitura zincata"
        assert rows[1].description == "Guarnizione"
    csv = export(Revision(revision=0, rows=rows, currency="EUR", number_style="en", reviewed=True))
    assert b"2.00;1250.00" in csv
    rows, _ = infer_page(read_ai_page(tmp_path, 1), 1, "auto", transport=httpx.MockTransport(unavailable))
    assert not rows


def test_missing_unit_price_rejects_total_without_model(tmp_path):
    source = tmp_path / "order.pdf"
    synthetic(source, borders=True, price="IMPORTO NETTO")
    extract(source, tmp_path, lambda *_: None, text_only=True)
    debug = {}
    with pytest.raises(ValueError, match="unit_price"):
        infer_page(read_ai_page(tmp_path, 0), 0, "auto", transport=httpx.MockTransport(unavailable), debug=debug)
    assert {"code": "missing_column", "field": "unit_price"} in debug["issues"]


def test_model_receives_only_the_unresolved_heading(tmp_path):
    source = tmp_path / "order.pdf"
    synthetic(source, borders=True, article="RIFERIMENTO")
    extract(source, tmp_path, lambda *_: None, text_only=True)
    debug, calls = {}, []

    def mock(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": MODEL}]})
        calls.append(request.url.path)
        payload = json.loads(request.content)
        explained = json.loads(payload["messages"][1]["content"])
        assert explained == {"article": [{"id": 2, "label": "RIFERIMENTO", "examples": ["A-01", "B-02"]}]}
        assert payload["format"]["properties"] == {"article": {"enum": [None, 2]}}
        return httpx.Response(200, json={"done": True, "message": {"content": '{"article":2}'}, "eval_count": 6})

    rows, _ = infer_page(read_ai_page(tmp_path, 0), 0, "auto", transport=httpx.MockTransport(mock), debug=debug)
    assert calls == ["/api/chat"]
    assert debug["ollama"]["eval_count"] == 6
    assert [row.article for row in rows] == ["A-01", "B-02"]


def test_ocr_scan_reaches_column_validation(tmp_path):
    pytest.importorskip("rapidocr_onnxruntime")
    from reportlab.lib.utils import ImageReader

    from order_extractor.extraction import pdfium

    source = tmp_path / "native.pdf"
    synthetic(source, borders=True)
    scan = tmp_path / "scan.pdf"
    output = Canvas(str(scan), pagesize=(595, 842))
    with pdfium.PdfDocument(source) as document:
        page = document[0]
        output.drawImage(ImageReader(page.render(scale=2).to_pil()), 0, 0, width=595, height=842)
        output.save()
        page.close()
    result = extract(scan, tmp_path, lambda *_: None, text_only=True)
    assert result["engines"] == ["OCR RapidOCR"]
    page = read_ai_page(tmp_path, 0)
    # The diagnostic must classify the page, even when the scan needs manual confirmation.
    diagnostics = {}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"models": []}))
    try:
        rows, _ = infer_page(page, 0, "auto", transport=transport, debug=diagnostics)
        assert [row.article for row in rows] == ["A-01", "B-02"]
    except ValueError:
        assert diagnostics["issues"]
    assert diagnostics["engine"] == "OCR RapidOCR" and "DESCRIZIONE" in diagnostics["text"].upper()


def test_diagnostic_report_is_opt_in_and_summary_has_no_order_text(tmp_path):
    import time

    from fastapi.testclient import TestClient

    from order_extractor.local import create_local

    class License:
        def status(self):
            return {"active": True}

    source = tmp_path / "order.pdf"
    synthetic(source, borders=True, price="IMPORTO NETTO")
    origin = "http://127.0.0.1:8105"
    app = create_local(tmp_path / "data", origin, "ticket", License(), local_ai=True, background=False)
    with TestClient(app, base_url=origin) as browser:
        assert browser.get("/open?ticket=ticket", follow_redirects=False).status_code == 303
        headers = {"Origin": origin}
        response = browser.post("/api/jobs", headers=headers, files={"file": ("order.pdf", source.read_bytes())},
                                data={"diagnostics": "true"})
        assert response.status_code == 200
        url = "/api/jobs/" + response.json()["token"]
        for _ in range(100):
            state = browser.get(url).json()
            if state["status"] != "running":
                break
            time.sleep(.05)
        assert state["status"] == "needs_review" and state["unresolved"][0]["page"] == 0
        approved = browser.post(url + "/approve", headers=headers, json={"revision": 0, "rows": [],
                               "number_style": "en", "currency": "", "reviewed": True})
        assert approved.status_code == 422
        full = browser.get(url + "/diagnostics").json()
        summary = browser.get(url + "/diagnostics?summary=true")
        assert full["ollama_calls"] == 0 and "A-01" in full["pages"][0]["text"]
        assert "A-01" not in summary.text and "missing_column" in summary.text
        assert browser.delete(url, headers=headers).status_code == 200
        assert browser.get(url + "/diagnostics").status_code == 404
