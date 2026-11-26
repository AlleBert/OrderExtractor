import json

import httpx
from reportlab.pdfgen.canvas import Canvas

from order_extractor.core import Row
from order_extractor.extraction import extract
from order_extractor.local_ai import MODEL, infer_page, pull_model, read_ai_page
from scripts.offline_csv import run


def test_layout_is_cropped_and_inference_reports_stream_progress(tmp_path):
    path = tmp_path / "order.pdf"
    pdf = Canvas(str(path), pagesize=(595, 842))
    pdf.drawString(40, 800, "Anagrafica cliente fittizio")
    pdf.drawString(40, 765, "Consegna")
    for y, fields in [(720, ("Articolo", "Descrizione", "Quantita", "Prezzo")),
                      (700, ("A-123", "Rondella", "2,00", "4,50"))]:
        for x, text in zip((40, 145, 330, 430), fields, strict=True):
            pdf.drawString(x, y, text)
    pdf.drawString(40, 670, "Totale ordine 9,00")
    pdf.drawString(40, 650, "Note riservate")
    pdf.save()

    baseline = extract(path, tmp_path, lambda *_: None, text_only=True)
    assert baseline["rows"] == []
    assert baseline["engines"] == ["testo PDF"]
    progress = []

    def mock(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": MODEL}]})
        data = json.loads(request.content)
        assert data["stream"] and data["options"]["num_ctx"] == 4096
        assert set(data["format"]["$defs"]["RowSelection"]["properties"]) == {"a", "d", "q", "p"}
        prompt = data["messages"][1]["content"]
        assert all(text not in prompt for text in ("Anagrafica", "Totale ordine", "Note riservate"))
        cells = {text: identifier for line in json.loads(prompt) for identifier, _, text in line}
        row = {"a": cells["A-123"], "d": [cells["Rondella"]], "q": cells["2,00"], "p": cells["4,50"]}
        encoded = json.dumps({"rows": [row]})
        events = [{"message": {"content": encoded[:12]}, "done": False},
                  {"message": {"content": encoded[12:]}, "done": False}, {"done": True}]
        return httpx.Response(200, content="\n".join(map(json.dumps, events)))

    rows, document = infer_page(read_ai_page(tmp_path, 0), 0, "it", transport=httpx.MockTransport(mock),
                                progress=lambda *status: progress.append(status))
    assert (rows[0].article, rows[0].quantity, document.rows[0].unit_price) == ("A-123", "2,00", "4.50")
    assert progress[0] == (1, 1, 0)
    assert progress[-1][2] > progress[1][2] > 0


def test_batch_creates_folders_and_exports_without_overwriting(tmp_path, monkeypatch):
    assert run(tmp_path, "it", init=True) == 0
    source = tmp_path / "input" / "order.pdf"
    source.write_bytes(b"%PDF-")
    monkeypatch.setattr("scripts.offline_csv.extract", lambda *_, **__: {})
    monkeypatch.setattr("scripts.offline_csv.infer_document", lambda *_: [
        Row(row_id="1", article="A-123", description="Rondella", quantity="2,00", unit_price="4,50")
    ])
    assert run(tmp_path, "it") == 0
    target = tmp_path / "output" / "order.csv"
    assert b"A-123;;Rondella;2.00;4.50\r\n" in target.read_bytes()
    assert run(tmp_path, "it") == 1


def test_model_setup_reports_download_progress():
    responses = [
        {"status": "downloading", "digest": "a", "total": 100, "completed": 30},
        {"status": "downloading", "digest": "a", "total": 100, "completed": 100},
        {"status": "success"},
    ]

    def mock(request):
        assert request.url.path == "/api/pull"
        assert json.loads(request.content) == {"model": MODEL, "stream": True}
        return httpx.Response(200, content="\n".join(map(json.dumps, responses)))

    seen = []
    pull_model(lambda done, total: seen.append((done, total)), transport=httpx.MockTransport(mock))
    assert seen == [(30, 100), (100, 100), (100, 100)]
