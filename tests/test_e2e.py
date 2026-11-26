import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from scripts.fixtures import ROWS, build
from scripts.fixtures import page as draw_order

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def demo(tmp_path):
    command = [sys.executable, "-m", "order_extractor", "demo", "--no-browser", "--data-dir", str(tmp_path)]
    with subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, text=True, bufsize=1) as process:
        try:
            start = {}
            for _ in range(4):
                line = process.stdout.readline().strip()
                assert ": " in line, f"Launcher non avviato: {line}"
                key, value = line.split(": ", 1)
                start[key] = value
            yield start
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=12)


def test_browser_extract_revise_csv(demo, tmp_path):
    chromium = os.environ.get("OE_CHROMIUM") or shutil.which("chromium")
    with sync_playwright() as browser_tools:
        browser = browser_tools.chromium.launch(executable_path=chromium, args=["--no-sandbox"])
        page = browser.new_page()
        console_errors = []
        page.on("pageerror", lambda error: console_errors.append(str(error)))
        page.goto(demo["Apri nel browser"])
        page.get_by_label("Seriale della licenza").fill(demo["Seriale demo"])
        page.get_by_label("Nome postazione").fill("Verifica automatizzata")
        page.get_by_role("button", name="Attiva licenza").click()
        page.get_by_role("button", name="Usa l’ordine demo").click()
        expect(page.get_by_role("heading", name="Rivedi l’ordine")).to_be_visible(timeout=30000)
        expect(page.get_by_label("Articolo riga 1")).to_have_value("00042")
        expect(page.get_by_label("Prezzo unitario riga 2")).to_have_value("0,45")
        page.get_by_label("Numeri").select_option("it")
        origin = page.url.rstrip("/")
        current = page.request.get(origin + "/api/jobs/current").json()
        rejected = page.request.post(origin + "/api/profiles", headers={"Origin": origin}, data={
            "name": "Profilo test", "number_style": "it", "currency": "", "layout_token": current["token"],
        })
        assert rejected.status == 409
        page.get_by_label("Descrizione riga 1").fill("Vite M6 verificata")
        expect(page.get_by_role("img", name="Anteprima pagina 1")).to_be_visible()
        page.get_by_role("button", name="Esporta CSV").click()
        expect(page.get_by_role("dialog")).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role("button", name="Conferma e scarica").click()
        payload = download.value.path().read_bytes()
        assert payload == (ROOT / "fixtures/synthetic/expected.csv.txt").read_text().replace(
            "Vite M6 zincata", "Vite M6 verificata").replace("\n", "\r\n").encode()
        expect(page.get_by_text("CSV scaricato.", exact=False)).to_be_visible()
        page.get_by_role("button", name="Storico").click()
        expect(page.get_by_text("Esportato", exact=True)).to_be_visible()
        page.get_by_role("button", name="Impostazioni").click()
        page.get_by_label("Nome del profilo").fill("Profilo test")
        page.get_by_label("Suggerisci il profilo per ordini con questo layout").check()
        page.get_by_role("button", name="Salva impostazioni correnti").click()
        expect(page.get_by_text("Profilo test · IT", exact=False)).to_be_visible()
        page.get_by_role("button", name="Estrazione").click()
        page.get_by_role("button", name="Chiudi ordine").click()
        same_layout = tmp_path / "same-layout.pdf"
        document = canvas.Canvas(str(same_layout), pagesize=A4)
        draw_order(document, rows=[(*ROWS[0][:4], "55", "0,30"), *ROWS[1:]])
        document.save()
        page.locator('input[type="file"]').set_input_files(same_layout)
        expect(page.get_by_role("button", name="Applica profilo suggerito")).to_be_visible()
        expect(page.get_by_label("Numeri")).to_have_value("auto")
        page.get_by_role("button", name="Applica profilo suggerito").click()
        expect(page.get_by_label("Numeri")).to_have_value("it")
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Chiudi ordine").click()
        different_layout = tmp_path / "new-layout.pdf"
        build(different_layout, variant=1)
        page.locator('input[type="file"]').set_input_files(different_layout)
        expect(page.get_by_role("heading", name="Rivedi l’ordine")).to_be_visible()
        expect(page.get_by_role("button", name="Applica profilo suggerito")).to_have_count(0)
        page.get_by_role("button", name="Impostazioni").click()
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Disattiva postazione").click()
        expect(page.get_by_role("alert")).to_have_text("Completa o chiudi l’ordine aperto prima di disattivare la postazione.")
        page.get_by_role("button", name="Estrazione").click()
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Chiudi ordine").click()
        admin = browser.new_page()
        admin.goto(demo["Dashboard demo"])
        admin.get_by_label("Token di accesso").fill(demo["Token admin demo"])
        admin.get_by_role("button", name="Accedi").click()
        admin.get_by_role("button", name="Postazioni").click()
        expect(admin.get_by_text("Verifica automatizzata")).to_be_visible()
        admin.once("dialog", lambda dialog: dialog.accept())
        admin.get_by_role("button", name="Revoca").click()
        expect(admin.get_by_text("revoked", exact=True)).to_be_visible()
        page.get_by_role("button", name="Impostazioni").click()
        page.get_by_role("button", name="Verifica licenza online").click()
        expect(page.get_by_text("Licenza sospesa, scaduta o revocata dal server.")).to_be_visible()
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Disattiva postazione").click()
        expect(page.get_by_text("Postazione disattivata.", exact=False)).to_be_visible()
        expect(page.get_by_text("Profilo test · IT", exact=False)).to_be_visible()
        page.get_by_role("button", name="Storico").click()
        expect(page.get_by_text("Esportato", exact=True)).to_be_visible()
        assert not console_errors
        browser.close()


def test_local_origin_host_and_one_use_bootstrap(demo):
    url = demo["Apri nel browser"]
    origin = url.split("/open?", 1)[0]
    with httpx.Client(trust_env=False) as client:
        assert client.get(origin + "/").status_code == 403
        assert client.get(url, headers={"Host": "external.example"}).status_code == 421
        assert client.get(url).status_code == 303
        assert client.get(url).status_code == 403
        assert client.post(origin + "/api/license/refresh", json={}, headers={"Origin": "https://external.example"}).status_code == 403
        assert client.get(origin + "/api/jobs/current").status_code == 200
        assert client.post(origin + "/api/jobs", files={"file": ("order.pdf", b"other data")},
                           headers={"Origin": origin}).status_code == 403


def test_manual_mapping_of_unrecognized_columns(demo, tmp_path):
    chromium = os.environ.get("OE_CHROMIUM") or shutil.which("chromium")
    source = tmp_path / "unknown-layout.pdf"
    initial = [("00042", "Vite M6", "100", "0,25"), ("ART003", "Bullone", "200", "0,45")]

    def write_order(path, rows, label="Oggetto"):
        document = canvas.Canvas(str(path), pagesize=A4)
        document.setFont("Helvetica", 11)
        for position, heading in zip((89, 253, 423, 478), ("Riferimento", label, "Richiesta", "Costo"), strict=True):
            document.drawString(position, 719, heading)
        for index, row in enumerate(rows):
            for position, value in zip((89, 253, 423, 478), row, strict=True):
                document.drawString(position, 690 - index * 30, value)
        document.showPage()
        document.save()

    write_order(source, initial)
    longer = tmp_path / "longer.pdf"
    write_order(longer, [("A1", "Dado", "8", "0,15"), *initial])
    different = tmp_path / "other-header.pdf"
    write_order(different, initial, "Merce")

    with sync_playwright() as browser_tools:
        browser = browser_tools.chromium.launch(executable_path=chromium, args=["--no-sandbox"])
        page = browser.new_page()
        page.goto(demo["Apri nel browser"])
        page.get_by_label("Seriale della licenza").fill(demo["Seriale demo"])
        page.get_by_role("button", name="Attiva licenza").click()
        page.locator('input[type="file"]').set_input_files(source)
        expect(page.get_by_role("heading", name="Rivedi l’ordine")).to_be_visible(timeout=30000)
        expect(page.get_by_text("Nessuna riga disponibile.", exact=False)).to_be_visible()
        page.get_by_role("button", name="Mappa colonne").click()
        page.get_by_label("Riferimento da segnare").select_option("article")
        image = page.get_by_role("img", name="Anteprima pagina 1")
        box = image.bounding_box()
        assert box
        image.click(position={"x": box["width"] * .14, "y": box["height"] * .17})
        article_edge = float(page.get_by_label("Articolo (%)", exact=True).input_value())
        assert 13.5 <= article_edge <= 14.5
        for field, value in (("Inizio righe", "16"), ("Fine righe", "25"),
                             ("Descrizione", "42"), ("Quantità", "70"), ("Prezzo unitario", "79")):
            page.get_by_label(field + " (%)", exact=True).fill(value)
        page.get_by_role("button", name="Rileggi questa pagina").click()
        expect(page.get_by_text("Mappatura manuale")).to_be_visible()
        expect(page.get_by_label("Articolo riga 1")).to_have_value("00042")
        expect(page.get_by_label("Descrizione riga 2")).to_have_value("Bullone")
        expect(page.get_by_label("Prezzo unitario riga 2")).to_have_value("0,45")
        page.get_by_role("button", name="Esporta CSV").click()
        with page.expect_download() as download:
            page.get_by_role("button", name="Conferma e scarica").click()
        assert b"2;ART003;;Bullone;200;0.45\r\n" in download.value.path().read_bytes()
        expect(page.get_by_text("CSV scaricato.", exact=False)).to_be_visible()
        page.get_by_role("button", name="Impostazioni").click()
        page.get_by_label("Nome del profilo").fill("Formato ricorrente")
        page.get_by_label("Suggerisci il profilo per ordini con questo layout").check()
        page.get_by_role("button", name="Salva impostazioni correnti").click()
        expect(page.get_by_text("Formato ricorrente · AUTO", exact=False)).to_be_visible()
        page.get_by_role("button", name="Estrazione").click()
        origin = page.url.rstrip("/")
        current = page.request.get(origin + "/api/jobs/current").json()
        endpoint = origin + "/api/jobs/" + current["token"]
        request = {"revision": current["revision"], "top": .16, "bottom": .25, "right": 1,
                   "columns":{"article":.14,"description":.42,"quantity":.70,"unit_price":.79}}
        headers = {"Origin": origin}
        assert page.request.put(endpoint + "/pages/0/mapping", data=request, headers=headers).status == 200
        assert page.request.put(endpoint + "/pages/0/mapping", data=request, headers=headers).status == 409
        assert page.request.get(endpoint + "/csv").status == 409
        page.get_by_role("button", name="Chiudi ordine").click()
        page.locator('input[type="file"]').set_input_files(longer)
        page.get_by_role("button", name="Usa colonne salvate").click()
        assert page.get_by_label("Fine righe (%)", exact=True).input_value() == ""
        expect(page.get_by_label("Quantità (%)", exact=True)).to_have_value("70.0")
        page.get_by_label("Fine righe (%)", exact=True).fill("27")
        page.get_by_role("button", name="Rileggi questa pagina").click()
        expect(page.get_by_label("Articolo riga 1")).to_have_value("A1")
        expect(page.get_by_label("Articolo riga 3")).to_have_value("ART003")
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Chiudi ordine").click()
        page.locator('input[type="file"]').set_input_files(different)
        expect(page.get_by_role("heading", name="Rivedi l’ordine")).to_be_visible()
        expect(page.get_by_role("button", name="Usa colonne salvate")).to_have_count(0)
        browser.close()
