import pytest
from pydantic import ValidationError

from order_extractor.structured_rows import PageSelection, RowSelection, resolve_page


def test_output_copies_referenced_cells_and_normalizes_decimals():
    selection = PageSelection(rows=[RowSelection(article="c1", description=["c2", "c3"],
                                                  quantity="c4", unit_price="c5")])
    cells = {"c1": "ART-001", "c2": "Rondella", "c3": "larga", "c4": "12,50", "c5": "€ 1.240,75"}
    result = resolve_page(selection, cells, 2, "it")
    assert result.model_dump() == {"schema_version": 1, "rows": [
        {"article": "ART-001", "description": "Rondella larga", "quantity": "12.50",
         "unit_price": "1240.75", "page": 2}
    ], "notices": []}


def test_missing_and_invalid_source_values_are_reported():
    selection = PageSelection(rows=[RowSelection(article="missing", description=[],
                                                  quantity="c1", unit_price=None)])
    result = resolve_page(selection, {"c1": "2x"}, 1, "it")
    assert result.rows[0].article is None
    assert result.rows[0].quantity is None
    assert {notice.field: notice.code for notice in result.notices} == {
        "article": "unknown_source", "description": "missing", "quantity": "invalid_number", "unit_price": "missing",
    }


def test_model_schema_rejects_added_raw_values():
    with pytest.raises(ValidationError):
        PageSelection.model_validate_json(
            '{"rows":[{"article":"c1","description":[],"quantity":"c2","unit_price":"c3","raw_price":10}]}'
        )
