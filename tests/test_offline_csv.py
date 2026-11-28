import json

import httpx

from order_extractor.core import Row
from order_extractor.local_ai import MODEL, pull_model
from scripts.offline_csv import run


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
