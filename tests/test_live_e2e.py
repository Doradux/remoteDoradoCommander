"""Live end-to-end release validation on loopback only, with disposable credentials.

Starts a temporary relay + real device agent + OAuth client under TEST secrets
on 127.0.0.1. Never connects to or edits the user's existing relay.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest


def available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until(check, timeout=12):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (httpx.RequestError, OSError, ValueError) as exc:
            last_error = exc
        time.sleep(0.1)
    pytest.fail(f"Loopback integration condition not met: {last_error}")


def test_real_relay_pairing_agent_oauth_and_capability_gate(tmp_path):
    port = available_loopback_port()
    base = f"http://127.0.0.1:{port}"
    folder = tmp_path / "agent-workspace"
    folder.mkdir()
    target = folder / "readme.txt"
    target.write_text("end-to-end local file")
    password = "test-" + secrets.token_urlsafe(40)
    server_env = {**os.environ,
                  "DORADO_PUBLIC_URL": base,
                  "DORADO_ADMIN_PASSWORD": password,
                  "DORADO_DB": str(tmp_path / "relay.sqlite3"),
                  "DORADO_BIND_HOST": "127.0.0.1",
                  "DORADO_PORT": str(port),
                  "DORADO_COMMERCIAL_MODE": "0",
                  "DORADO_MAX_DEVICES": "0"}
    for key in ("DORADO_AGENT_TOKEN", "DORADO_ENTITLEMENT_FILE",
                "DORADO_ENTITLEMENT_SECRET", "DORADO_READ_ONLY"):
        server_env.pop(key, None)

    relay = subprocess.Popen(
        [sys.executable, "-m", "remote_dorado.server"],
        env=server_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    agent = None
    try:
        with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as api:
            wait_until(lambda: api.get(base + "/health").status_code == 200)
            name = "e2e-test-device"
            initial = api.post(base + "/pair/device", json={"device_name": name})
            assert initial.status_code == 200, initial.text
            details = initial.json()
            # UI is served by the real HTTP service, not a fake test client.
            pairing_html = api.get(base + "/pair?code=" + details["user_code"])
            assert pairing_html.status_code == 200
            assert "/ui-assets/assets/" in pairing_html.text
            approved = api.post(base + "/pair/approve", data={
                "user_code": details["user_code"], "password": password,
            })
            assert approved.status_code == 200
            pairing = api.post(base + "/pair/token", json={
                "device_code": details["device_code"],
            })
            assert pairing.status_code == 200, pairing.text
            assert len(pairing.json()["agent_token"]) >= 32
            agent_env = {**server_env,
                         "DORADO_AGENT_TOKEN": pairing.json()["agent_token"],
                         "DORADO_DEVICE_NAME": name,
                         "DORADO_ALLOWED_ROOTS": str(folder),
                         "DORADO_AGENT_PERMISSION": "files",
                         "DORADO_CONFIG_FILE": str(tmp_path / "temp-agent.json")}
            agent = subprocess.Popen(
                [sys.executable, "-m", "remote_dorado.agent",
                 "--name", name, "--permission", "files"],
                env=agent_env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            wait_until(lambda: api.get(base + "/health").json()["online_devices"] == 1)

            verifier = "a" * 64
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            redirect = "http://localhost:9899/callback"
            registration = api.post(base + "/oauth/register", json={"redirect_uris": [redirect]})
            assert registration.status_code == 201
            cid = registration.json()["client_id"]
            authorization = api.get(base + "/oauth/authorize", params={
                "client_id": cid, "redirect_uri": redirect,
                "response_type": "code", "state": "demo-test-state",
                "code_challenge": challenge, "code_challenge_method": "S256",
            })
            assert authorization.status_code == 200
            # The request ID lives in inert escaped JSON in the Animate UI shell.
            from tests.ui_helpers import page_data
            request_id = page_data(authorization.text)["requestId"]
            consent = api.post(base + "/oauth/approve", data={
                "request_id": request_id, "password": password,
            })
            assert consent.status_code == 303
            code = parse_qs(urlsplit(consent.headers["location"]).query)["code"][0]
            token = api.post(base + "/oauth/token", data={
                "grant_type": "authorization_code", "client_id": cid,
                "redirect_uri": redirect, "code": code,
                "code_verifier": verifier,
            })
            assert token.status_code == 200
            headers = {"Authorization": "Bearer " + token.json()["access_token"]}

            def call(tool, args):
                response = api.post(base + "/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "id": 7, "method": "tools/call",
                    "params": {"name": tool, "arguments": {"device": name, **args}},
                })
                assert response.status_code == 200, response.text
                result = response.json()["result"]
                return result, result["content"][0]["text"]

            result, raw = call("read_file", {"path": str(target)})
            assert result.get("isError") is not True
            assert json.loads(raw)["content"] == "end-to-end local file"

            result, raw = call("execute_command", {"cwd": str(folder), "command": "echo forbidden"})
            assert result["isError"] is True
            assert "forbids remote shell" in raw

            result, raw = call("edit_file", {
                "path": str(target), "old_text": "local", "new_text": "validated",
            })
            assert result.get("isError") is not True
            assert target.read_text() == "end-to-end validated file"

            revoked = api.post(base + "/admin/devices/revoke",
                               headers={"Authorization": "Bearer " + password},
                               json={"device_name": name})
            assert revoked.status_code == 200
            wait_until(lambda: api.get(base + "/health").json()["online_devices"] == 0)
    finally:
        if agent is not None:
            agent.terminate()
            try:
                agent.wait(timeout=3)
            except subprocess.TimeoutExpired:
                agent.kill()
                agent.wait(timeout=3)
        relay.terminate()
        try:
            relay.wait(timeout=3)
        except subprocess.TimeoutExpired:
            relay.kill()
            relay.wait(timeout=3)
