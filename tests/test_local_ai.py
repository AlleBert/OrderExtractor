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
        assert body["messages"][1]["content"] == "ART-101 | Guarnizione | 2,00 | 8,20"
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps({"rows": [{
            "a": "ART-101", "d": "Guarnizione", "q": "2,00", "p": "8,20"
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


@pytest.mark.parametrize("bad_price", [False, True])
def test_wrapped_rows_match_literals_and_reject_cross_row_values(bad_price):
    lines = [
        [("Articolo", .06), ("Descrizione", .22), ("Quantita", .60), ("PREZZO", .79)],
        [("UNIT.", .8)],
        [("Componente standard -", .22)],
        [("A-01", .06), ("2,00", .60), ("€ 8,20", .79)],
        [("finitura zincata", .22)],
        [("B-02", .06), ("Guarnizione", .22), ("6,00", .60), ("€ 12,50", .79)],
    ]
    page = CachedText(engine="testo PDF", lines=[
        [CachedWord(text=text, x0=x, x1=x + .06, y0=.16 + i * .012, y1=.167 + i * .012)
         for text, x in line] for i, line in enumerate(lines)
    ])

    def responder(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": MODEL}]})
        sent = json.loads(request.content)["messages"][1]["content"]
        assert "PREZZO UNIT." in sent
        assert "A-01 | Componente standard - finitura zincata | 2,00 | € 8,20" in sent
        candidate = {"rows": [
            {"a": "A-01", "d": "Componente standard - finitura zincata", "q": "2,00",
             "p": "12,50" if bad_price else "8,20"},
            {"a": "B-02", "d": "Guarnizione", "q": "6,00", "p": "12,50"},
        ]}
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps(candidate)}})

    if bad_price:
        with pytest.raises(ValueError, match="riga verificabile"):
            infer_page(page, 0, "it", transport=httpx.MockTransport(responder))
    else:
        _, document = infer_page(page, 0, "it", transport=httpx.MockTransport(responder))
        assert [(row.article, row.quantity, row.unit_price) for row in document.rows] == [
            ("A-01", "2.00", "8.20"), ("B-02", "6.00", "12.50"),
        ]
