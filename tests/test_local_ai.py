import json

import httpx
import pytest

from order_extractor.local_ai import MODEL, infer_page
from order_extractor.manual import CachedText, CachedWord


def test_ollama_receives_schema_and_returns_source_grounded_rows():
    page = CachedText(engine="testo PDF", lines=[[
        CachedWord(text=text, x0=x, x1=x + .03, y0=.2, y1=.22)
        for text, x in [("ART-101", .05), ("Guarnizione", .20), ("2,00", .60), ("8,20", .80)]
    ]])

    def responder(request):
        assert request.url.host == "127.0.0.1"
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": MODEL}]})
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["format"]["additionalProperties"] is False
        assert body["options"]["num_gpu"] == 0
        assert json.loads(body["messages"][1]["content"])[0][0] == ["c1", .05, "ART-101"]
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps({"rows": [{
            "article": "c1", "description": ["c2"], "quantity": "c3", "unit_price": "c4"
        }]})}})

    rows, document = infer_page(page, 0, "it", transport=httpx.MockTransport(responder))
    assert rows[0].unit_price == "8,20"
    assert document.rows[0].unit_price == "8.20"
    assert document.rows[0].article == "ART-101"


def test_model_missing_prevents_sending_any_page_content():
    page = CachedText(engine="testo PDF", lines=[[
        CachedWord(text="Riservato", x0=.1, x1=.2, y0=.2, y1=.22)
    ]])

    def responder(request):
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": []})

    with pytest.raises(ValueError, match="Installa il modello locale"):
        infer_page(page, 0, "it", transport=httpx.MockTransport(responder))


def test_rapidocr_converts_pixel_boxes_to_page_coordinates():
    from PIL import Image

    from order_extractor.extraction import rapid_words

    def engine(data):
        assert data.startswith(b"\x89PNG")
        return [[[[10, 20], [50, 20], [50, 40], [10, 40]], "A-123", .99]], []

    words = rapid_words(Image.new("RGB", (100, 200)), engine)
    assert [(word.text, word.x0, word.y0, word.x1, word.y1) for word in words] == [
        ("A-123", .1, .1, .5, .2)
    ]
