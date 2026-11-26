import pytest

from order_extractor.core import Row, Span
from order_extractor.manual import PageMapping, replace_page


def row(code, *pages):
    return Row(row_id=code, article=code, source_spans=[Span(page_index=page, bbox=(.1, .2, .3, .4),
                                                          engine="testo PDF", raw_text="sintetico") for page in pages])


def test_replacement_preserves_other_pages_and_manual_rows():
    before, replace, after, manual = row("before", 0), row("replace", 1), row("after", 2), row("manual")
    replacement = row("inserted", 1)
    assert replace_page([before, replace, after, manual], 1, [replacement]) == [before, replacement, after, manual]
    assert replace_page([before, after, manual], 1, [replacement]) == [before, replacement, after, manual]
    with pytest.raises(ValueError, match="attraversa"):
        replace_page([row("merged", 0, 1)], 1, [replacement])


@pytest.mark.parametrize("top,bottom,columns", [
    (.2, .1, {"article": .1, "description": .3, "quantity": .5, "unit_price": .8}),
    (.1, .2, {"article": .1, "description": .3, "quantity": .5}),
    (.1, .2, {"article": .1, "description": .3, "quantity": .5, "unit_price": .5}),
])
def test_mapping_requires_distinct_ordered_boundaries(top, bottom, columns):
    with pytest.raises(ValueError):
        PageMapping(revision=0, top=top, bottom=bottom, columns=columns)
