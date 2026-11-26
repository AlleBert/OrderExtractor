import json
import sqlite3
from contextlib import closing

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from order_extractor.extraction import extract
from order_extractor.local import LocalStore, Profile
from order_extractor.manual import ColumnRules
from scripts.fixtures import page


def test_profiles_migrate_and_only_keep_reviewed_settings_bound(tmp_path):
    with closing(sqlite3.connect(tmp_path / "local.sqlite")) as db, db:
        db.execute("CREATE TABLE profiles(id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, style TEXT NOT NULL, currency TEXT NOT NULL)")
        db.execute("INSERT INTO profiles VALUES ('legacy-id', 'Storico', 'it', 'EUR')")
    store = LocalStore(tmp_path)
    assert store.query("SELECT style FROM profiles WHERE id='legacy-id'") == [{"style": "it"}]
    assert store.profile_for_layout("v1:first") is None

    settings = Profile(name="Storico", number_style="it", currency="EUR")
    store.save_profile(settings, "v1:first")
    store.save_profile(settings, "v1:second")
    assert LocalStore(tmp_path).profile_for_layout("v1:first")["id"] == "legacy-id"
    assert store.profile_for_layout("v1:second")["currency"] == "EUR"
    assert store.profile_for_layout("v1:unrelated") is None

    store.save_profile(Profile(name="Storico", number_style="en", currency="USD"), "v1:second")
    assert store.profile_for_layout("v1:first") is None
    assert store.profile_for_layout("v1:second")["style"] == "en"
    store.query("DELETE FROM profiles WHERE id='legacy-id'")
    assert not store.query("SELECT * FROM profile_layouts")


def test_document_with_different_table_headers_has_no_single_layout(tmp_path):
    path = tmp_path / "mixed-layout.pdf"
    document = canvas.Canvas(str(path), pagesize=A4)
    page(document, variant=0)
    page(document, variant=1)
    document.save()
    result = extract(path, tmp_path, lambda *_: None)
    assert len(result["rows"]) == 6
    assert result["layout_key"] is None


def test_manual_profile_persists_column_edges_and_no_row_ranges(tmp_path):
    store = LocalStore(tmp_path)
    profile = Profile(name="Formati", number_style="it", currency="EUR")
    rules = ColumnRules(columns={"article": .10, "description": .40, "quantity": .70, "unit_price": .85})
    key = "m1:" + "a" * 64
    store.save_profile(profile, manuals={key: rules})
    reopened = LocalStore(tmp_path)
    matching = reopened.column_rules([key])
    assert matching[key]["name"] == "Formati"
    assert matching[key]["columns"] == rules.columns
    payload = reopened.query("SELECT rules FROM profile_columns")[0]["rules"]
    assert set(json.loads(payload)) == {"right", "columns"}
    reopened.query("DELETE FROM profiles WHERE id=?", (matching[key]["profile_id"],))
    assert reopened.column_rules([key]) == {}
