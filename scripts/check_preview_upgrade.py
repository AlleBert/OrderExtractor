"""Exercise the published Preview's UI and launcher on a clean Windows runner."""

import argparse
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright

PORT = 39671


def application_page(playwright, process, *, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and process.poll() is None:
        try:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}", timeout=2500)
            for context in browser.contexts:
                for page in context.pages:
                    if page.url.startswith("http://127.0.0.1:"):
                        return page
            browser.close()
        except Error:
            pass
        time.sleep(1)
    raise AssertionError(f"L’interfaccia non è disponibile entro {timeout}s, launcher={process.poll()}.")


def close_application(process):
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                    "Get-Process -Name OrderExtractor -ErrorAction Stop | "
                    "ForEach-Object { if (!$_.CloseMainWindow()) { throw 'Finestra non chiusa' } }"],
                   check=True, timeout=15)
    if process.wait(timeout=30):
        raise AssertionError("Il launcher non si è chiuso correttamente.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("installed")
    parser.add_argument("expected")
    args = parser.parse_args()
    local = Path(os.environ["LOCALAPPDATA"])
    launcher = local / "Programs" / "Order Extractor Preview" / "OrderExtractorLauncher.exe"
    database = local / "OrderExtractorPreview" / "demo" / "local.sqlite"
    environment = {**os.environ, "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={PORT}"}
    process = subprocess.Popen([launcher], cwd=launcher.parent, env=environment)
    try:
        with sync_playwright() as playwright:
            page = application_page(playwright, process, timeout=75)
            expect(page.get_by_text("Licenza attiva", exact=True)).to_be_visible(timeout=20000)
            assert page.evaluate("async () => (await (await fetch('/api/status')).json()).version") == args.installed
            page.get_by_role("button", name="Impostazioni", exact=True).click()
            page.get_by_role("button", name="Verifica aggiornamenti").click()
            expect(page.get_by_text(f"Versione {args.expected}", exact=True)).to_be_visible(timeout=30000)
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="Installa al prossimo avvio").click()
            expect(page.get_by_text("Aggiornamento confermato.", exact=False)).to_be_visible(timeout=30000)
            close_application(process)

        with closing(sqlite3.connect(database)) as db, db:
            db.execute("INSERT INTO settings(key,value) VALUES ('upgrade_test','retained')")

        # The launcher's hidden verification window uses its own WebView2 session.
        # Wait for activation before attaching to the visible application.
        process = subprocess.Popen([launcher], cwd=launcher.parent, env=environment)
        result = launcher.parent / "last-update.json"
        deadline = time.monotonic() + 300
        while not result.is_file() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(1)
        outcome = result.read_text(encoding="utf-8") if result.is_file() else "(nessun esito)"
        assert f"Versione {args.expected} installata." in outcome, outcome

        with sync_playwright() as playwright:
            page = application_page(playwright, process, timeout=75)
            expect(page.get_by_text("Licenza attiva", exact=True)).to_be_visible(timeout=20000)
            status = page.evaluate("async () => await (await fetch('/api/status')).json()")
            assert status["version"] == args.expected and not status["update_pending"], status
            close_application(process)

        with closing(sqlite3.connect(database)) as db:
            assert db.execute("SELECT value FROM settings WHERE key='upgrade_test'").fetchone() == ("retained",)
    finally:
        if process.poll() is None:
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], timeout=15, check=False)
    print(f"Upgrade {args.installed} → {args.expected} verificato dalla UI con dati conservati.")


if __name__ == "__main__":
    main()
