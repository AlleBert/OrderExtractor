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
