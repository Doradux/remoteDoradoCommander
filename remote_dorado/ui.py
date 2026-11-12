"""Animate UI / React presentation shell for all Dorado web pages.

All rendered controls live in frontend/src/app.jsx and use Animate UI.
No hand-crafted Python HTML forms, styles, or interpolated JS are allowed.
The server remains authoritative over authentication, CSRF and OAuth state.
"""
from __future__ import annotations

from functools import lru_cache
from html import escape
import json
from pathlib import Path
import re
from typing import Any

from fastapi.responses import HTMLResponse

STATIC_DIR = Path(__file__).resolve().parent / "static"
_MANIFEST = STATIC_DIR / ".vite" / "manifest.json"
_SAFE_ASSET = re.compile(r"assets/[a-zA-Z0-9._-]+\Z")

HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    ),
}


@lru_cache(maxsize=1)
def _assets() -> tuple[str, tuple[str, ...]]:
    try:
        entry = json.loads(_MANIFEST.read_text(encoding="utf-8"))["index.html"]
        js = entry["file"]
        css = entry.get("css", [])
        if not isinstance(js, str) or not _SAFE_ASSET.fullmatch(js):
            raise ValueError("Unexpected Vite JS filename")
        if not isinstance(css, list) or any(
            not isinstance(file, str) or not _SAFE_ASSET.fullmatch(file) for file in css
        ):
            raise ValueError("Unexpected Vite CSS filename")
        return js, tuple(css)
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise RuntimeError(
            "Animate UI assets are missing or invalid. Build frontend with npm run build."
        ) from exc


def ui_markup(page: str, **payload: Any) -> str:
    js, css = _assets()
    data = json.dumps({"page": page, **payload}, ensure_ascii=True, separators=(",", ":"))
    data = (data.replace("&", "\\u0026").replace("<", "\\u003c")
            .replace(">", "\\u003e").replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))
    styles = "".join(
        f"<link rel='stylesheet' href='/ui-assets/{escape(file, quote=True)}'>"
        for file in css
    )
    return (
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(page.title())} | Dorado Relay</title>"
        f"{styles}</head><body><div id='root'></div>"
        f"<script id='dorado-data' type='application/json'>{data}</script>"
        f"<script type='module' src='/ui-assets/{escape(js, quote=True)}'></script>"
        "</body></html>"
    )


def ui_response(page: str, *, status_code: int = 200,
                headers: dict[str, str] | None = None, **payload: Any) -> HTMLResponse:
    return HTMLResponse(
        ui_markup(page, **payload), status_code=status_code,
        headers={**HEADERS, **(headers or {})},
    )


def pairing_page(
    code: str, device_name: str | None = None, *,
    logged_in: bool = False, csrf: str = "",
) -> str:
    return ui_markup(
        "pair", code=code, deviceName=device_name,
        loggedIn=logged_in, csrf=csrf,
    )


def authorization_page(
    request_id: str, callback_origin: str, *,
    logged_in: bool = False, csrf: str = "",
) -> str:
    return ui_markup(
        "oauth", requestId=request_id, callbackOrigin=callback_origin,
        loggedIn=logged_in, csrf=csrf,
    )
