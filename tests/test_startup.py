import os
import shutil
import sys
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from order_extractor.launcher import check_webview, serve
from order_extractor.local import create_local


class DemoLicense:
    def status(self):
        return {"active": True}


def test_trial_requires_the_webview2_renderer(monkeypatch, tmp_path):
    ready = threading.Event()
    ready.set()

    def create(*args, **kwargs):
        assert kwargs["hidden"]
        return SimpleNamespace(destroy=lambda: None)

    webview = SimpleNamespace(renderer="mshtml", create_window=create,
                              start=lambda callback, **kwargs: callback())
    monkeypatch.setitem(sys.modules, "webview", webview)
    with pytest.raises(RuntimeError, match="WebView2"):
        check_webview("http://127.0.0.1/", ready, tmp_path)
    webview.renderer = "edgechromium"
    check_webview("http://127.0.0.1/", ready, tmp_path)


def test_readiness_requires_authenticated_same_origin_probe(tmp_path):
    origin = "http://127.0.0.1:8105"
    ready = threading.Event()
    app = create_local(tmp_path, origin, "bootstrap", DemoLicense(), background=False, startup_ready=ready)
    headers = {"Origin": origin}
    with TestClient(app, base_url=origin) as client:
        assert client.post("/api/startup/ready", headers=headers, json={}).status_code == 403
        assert client.get("/open?ticket=bootstrap", follow_redirects=False).status_code == 303
        assert client.get("/api/status").json()["startup_check"] is True
        assert client.put("/api/privacy", headers=headers, json={"diagnostics": True}).status_code == 403
        assert client.post("/api/startup/ready", headers={"Origin": "https://other.example"}, json={}).status_code == 403
        assert not ready.is_set()
        assert client.post("/api/startup/ready", headers=headers, json={}).json() == {"ready": True}
        assert ready.is_set()
        assert client.get("/api/status").json()["diagnostics"] is False


@pytest.mark.parametrize("profiles_available", [True, False])
def test_application_frontend_confirms_after_initial_requests(tmp_path, profiles_available):
    ready = threading.Event()
    origin, service, thread = serve(lambda address: create_local(
        tmp_path, address, "bootstrap", DemoLicense(), background=False, startup_ready=ready
    ))
    chromium = os.environ.get("OE_CHROMIUM") or shutil.which("chromium")
    try:
        with sync_playwright() as browser_tools:
            browser = browser_tools.chromium.launch(executable_path=chromium, args=["--no-sandbox"])
            page = browser.new_page()
            if not profiles_available:
                page.route("**/api/profiles", lambda route: route.fulfill(status=503, json={"detail": "Profili non disponibili."}))
            page.goto(origin + "/open?ticket=bootstrap")
            if profiles_available:
                assert ready.wait(10)
            else:
                expect(page.get_by_role("alert")).to_have_text("Profili non disponibili.")
                assert not ready.is_set()
            browser.close()
    finally:
        service.should_exit = True
        thread.join(6)
