"""Schedule through the Preview UI and exercise the published Windows launcher."""

import argparse
import json
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from order_extractor import local
from order_extractor.launcher import serve
from order_extractor.preview_updates import GitHubUpdates, verifier_path
from order_extractor.version_store import VersionStore, durable_json


class PreviewLicense:
    def status(self):
        return {"active": True}


def schedule(home, data, installed, expected):
    local.VERSION = installed
    ticket = "ci-synthetic-preview"
    updates = GitHubUpdates(installed, home, preview=True)
    origin, service, thread = serve(lambda address: local.create_local(
        data, address, ticket, PreviewLicense(), installation=home,
        github_updates=updates, background=False,
    ))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge")
            page = browser.new_page()
            page.goto(f"{origin}/open?ticket={ticket}")
            page.get_by_role("button", name="Impostazioni", exact=True).click()
            page.get_by_role("button", name="Verifica aggiornamenti").click()
            expect(page.get_by_text(f"Versione {expected}", exact=True)).to_be_visible(timeout=30000)
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="Installa al prossimo avvio").click()
            expect(page.get_by_text("Aggiornamento confermato.", exact=False)).to_be_visible(timeout=30000)
            browser.close()
    finally:
        service.should_exit = True
        thread.join(10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("installed")
    parser.add_argument("expected")
    args = parser.parse_args()
    appdata = Path(os.environ["LOCALAPPDATA"])
    home = appdata / "Programs" / "Order Extractor Preview"
    data = appdata / "OrderExtractorPreview"
    store = VersionStore(home, data, data_subdir="demo")
    launcher = home / "OrderExtractorLauncher.exe"
    assert launcher.is_file() and verifier_path(home).is_file()
    assert store.executable(args.installed).is_file() and not store.executable(args.expected).exists()
    durable_json(store.pointer, {"version": args.installed})
    schedule(home, data / "demo", args.installed, args.expected)
    database = data / "demo" / "local.sqlite"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("INSERT INTO settings(key,value) VALUES ('upgrade_test','retained')")

    probe_log = Path(os.environ["RUNNER_TEMP"]) / "oe-upgrade-probe.log"
    process = subprocess.Popen([launcher], cwd=home, env={**os.environ, "OE_PROBE_LOG": str(probe_log)})
    result = home / "last-update.json"
    try:
        deadline = time.monotonic() + 300
        while not result.is_file() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(1)
        outcome = json.loads(result.read_text(encoding="utf-8")) if result.is_file() else {}
        if not outcome.get("success") and probe_log.is_file():
            print(probe_log.read_text(encoding="utf-8", errors="replace"), flush=True)
        assert outcome.get("success"), outcome
        assert store.current() == args.expected
        assert not (home / "requested-update.json").exists()
        assert store.executable(args.installed).is_file()
        with closing(sqlite3.connect(database)) as db:
            assert db.execute("SELECT value FROM settings WHERE key='upgrade_test'").fetchone() == ("retained",)
        print(f"Upgrade {args.installed} -> {args.expected} verified with data retained.")
    finally:
        if process.poll() is None:
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], timeout=15, check=False)


if __name__ == "__main__":
    main()
