"""Animate UI build, static delivery, CSP and XSS regression tests."""
import json
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient

from remote_dorado.server import app
from remote_dorado.ui import STATIC_DIR, ui_markup
from tests.ui_helpers import page_data

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_PUBLIC_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("DORADO_ADMIN_PASSWORD", "secure-test-admin-password-12345")
    monkeypatch.delenv("DORADO_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("DORADO_COMMERCIAL_MODE", raising=False)
    monkeypatch.delenv("DORADO_ENTITLEMENT_FILE", raising=False)
    monkeypatch.setenv("DORADO_DB", str(tmp_path / "ui.sqlite3"))
    with TestClient(app) as api:
        yield api


def test_animate_ui_source_is_the_only_animated_component_framework():
    source = (ROOT / "frontend/src/app.jsx").read_text()
    assert "@animate-ui/react/primitives/buttons/button" in source
    assert "@animate-ui/react/primitives/base-ui/tabs" in source
    assert "@animate-ui/react/primitives/base-ui/dialog" in source
    assert "@animate-ui/react/primitives/texts/counting-number" in source
    assert "<Dialog." in source
    assert "<Tabs." in source
    assert "data-[active]" in source
    assert "window?.__DORADO" not in source
    assert "_STYLE =" not in (ROOT / "remote_dorado/portal.py").read_text()
    assert "style-src 'unsafe-inline'" not in (ROOT / "remote_dorado/server.py").read_text()

    packages = json.loads((ROOT / "frontend/package.json").read_text())
    assert "@animate-ui/react" in packages["dependencies"]
    assert "motion" in packages["dependencies"]
    assert "tailwindcss" in packages["devDependencies"]
    assert (ROOT / "docs/ANIMATE_UI_CATALOG.md").exists()


def test_static_animate_ui_bundle_served_from_same_origin(client):
    manifest = json.loads((STATIC_DIR / ".vite/manifest.json").read_text())
    files = [manifest["index.html"]["file"]] + manifest["index.html"]["css"]
    assert files
    for file in files:
        assert file.startswith("assets/")
        response = client.get("/ui-assets/" + file)
        assert response.status_code == 200
        assert response.content
        assert response.headers["content-type"].startswith(("text/css", "text/javascript", "application/javascript"))

    page = client.get("/pair")
    assert page.status_code == 200
    assert page_data(page.text)["page"] == "pair"
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert "style-src 'self'" in page.headers["content-security-policy"]
    assert "'unsafe-inline'" not in page.headers["content-security-policy"]
    assert "<style>" not in page.text
    assert 'type="module"' in page.text or "type='module'" in page.text
    assert "https://fonts.googleapis.com" not in page.text
    assert "http://localhost:5173" not in page.text


def test_backend_json_bootstrap_is_escaped_not_executable():
    attacker = "</script><img src=x onerror=alert(1)>"
    html = ui_markup("pair", deviceName=attacker, csrf="quote' & value")
    assert attacker not in html
    assert "<img src=x" not in html
    assert r"\u003c/script\u003e" in html
    assert page_data(html)["deviceName"] == attacker
    assert page_data(html)["csrf"] == "quote' & value"


def test_vendor_commercial_license_notice_is_distributed(client):
    notice = client.get("/ui-assets/licenses/animate-ui-LICENSE.md")
    assert notice.status_code == 200
    assert "Commons Clause" in notice.text
    assert "Elliot Sutton" in notice.text
    assert (ROOT / "frontend/public/licenses/animate-ui-LICENSE.md").exists()
