"""Private MCP Streamable HTTP bridge with OAuth PKCE and outgoing WebSocket agents.

This is an independent implementation, not the proprietary Desktop Commander service.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
import hashlib
import html
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.websockets import WebSocketDisconnect
import uvicorn

VERSION = "2025-06-18"
TOOLS: list[dict[str, Any]] = [
    {"name": "list_devices", "description": "List online computers paired with this private server.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ping_device", "description": "Check the connection and OS of a paired computer.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}}, "required": ["device"]}},
    {"name": "list_directory", "description": "List directory entries on a paired computer.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["device", "path"]}},
    {"name": "read_file", "description": "Read UTF-8 text from a file. Returns up to 250000 characters.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "path": {"type": "string"}, "max_chars": {"type": "integer"}}, "required": ["device", "path"]}},
    {"name": "write_file", "description": "Create a text file; overwrite requires explicit overwrite=true.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "path": {"type": "string"}, "content": {"type": "string"}, "overwrite": {"type": "boolean"}}, "required": ["device", "path", "content"]}, "annotations": {"destructiveHint": True}},
    {"name": "edit_file", "description": "Replace an exact unique text block in an existing UTF-8 file.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["device", "path", "old_text", "new_text"]}, "annotations": {"destructiveHint": True}},
    {"name": "search_files", "description": "Search filenames with a glob (e.g. *.py) beneath a directory.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "path": {"type": "string"}, "pattern": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["device", "path"]}},
    {"name": "execute_command", "description": "Execute an arbitrary shell command as the paired computer's OS user; high risk. Max 120 seconds and 250 KB per stream.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "cwd": {"type": "string"}, "command": {"type": "string"}, "timeout_seconds": {"type": "integer"}}, "required": ["device", "cwd", "command"]}, "annotations": {"destructiveHint": True, "openWorldHint": True}},
]
OPS = {t["name"] for t in TOOLS} - {"list_devices"}


def base_url() -> str:
    return os.environ.get("DORADO_PUBLIC_URL", "").rstrip("/")


def resource_url() -> str:
    return base_url() + "/mcp"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def db_path() -> Path:
    return Path(os.environ.get("DORADO_DB", "./data/dorado.sqlite3"))


@contextmanager
def database():
    connection = sqlite3.connect(db_path(), timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    for var in ("DORADO_PUBLIC_URL", "DORADO_AGENT_TOKEN", "DORADO_ADMIN_PASSWORD"):
        if not os.environ.get(var):
            raise RuntimeError(f"Missing required environment variable: {var}")
    if not base_url().startswith("https://") and not base_url().startswith("http://127.0.0.1") and not base_url().startswith("http://localhost"):
        raise RuntimeError("Use public HTTPS for DORADO_PUBLIC_URL; HTTP is allowed only on loopback for local tests")
    if len(os.environ["DORADO_AGENT_TOKEN"]) < 32 or len(os.environ["DORADO_ADMIN_PASSWORD"]) < 16:
        raise RuntimeError("DORADO_AGENT_TOKEN must be 32+ chars; DORADO_ADMIN_PASSWORD 16+ chars")
    db_path().parent.mkdir(parents=True, exist_ok=True)
    with database() as conn:
        conn.executescript("""
          CREATE TABLE IF NOT EXISTS clients (id TEXT PRIMARY KEY, redirect_uris TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS authorization_requests (id_hash TEXT PRIMARY KEY, client_id TEXT, redirect_uri TEXT, state TEXT, challenge TEXT, resource TEXT, expires INTEGER);
          CREATE TABLE IF NOT EXISTS codes (code_hash TEXT PRIMARY KEY, client_id TEXT, redirect_uri TEXT, challenge TEXT, resource TEXT, expires INTEGER);
          CREATE TABLE IF NOT EXISTS tokens (token_hash TEXT PRIMARY KEY, client_id TEXT, kind TEXT, expires INTEGER);
        """)


@dataclass
class Device:
    socket: WebSocket
    pending: dict[str, asyncio.Future]


class DeviceHub:
    def __init__(self) -> None:
        self.devices: dict[str, Device] = {}
        self.capacity = asyncio.Semaphore(16)

    async def ask(self, name: str, op: str, args: dict[str, Any]) -> dict[str, Any]:
        device = self.devices.get(name)
        if not device:
            raise RuntimeError(f"Device '{name}' is offline")
        async with self.capacity:
            request_id = secrets.token_hex(16)
            future = asyncio.get_running_loop().create_future()
            device.pending[request_id] = future
            try:
                await device.socket.send_json({"id": request_id, "op": op, "args": args})
                reply = await asyncio.wait_for(future, timeout=135)
                if "error" in reply:
                    raise RuntimeError(str(reply["error"]))
                return reply.get("result", {})
            finally:
                device.pending.pop(request_id, None)


hub = DeviceHub()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Remote Dorado Commander", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def oauth_error(error: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status, headers={"Cache-Control": "no-store"})


def check_redirect(value: str) -> bool:
    parsed = urlsplit(value)
    return (parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password and not parsed.fragment) or (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "[::1]") and not parsed.fragment)


def require_resource(resource: str | None) -> bool:
    return resource in (None, "", resource_url())


@app.get("/health")
async def health():
    return {"status": "ok", "online_devices": len(hub.devices)}


@app.get("/.well-known/oauth-protected-resource")
@app.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource():
    return {"resource": resource_url(), "authorization_servers": [base_url()], "scopes_supported": ["remote"]}


@app.get("/.well-known/oauth-authorization-server")
async def authorization_metadata():
    return {"issuer": base_url(), "authorization_endpoint": base_url() + "/oauth/authorize", "token_endpoint": base_url() + "/oauth/token", "registration_endpoint": base_url() + "/oauth/register", "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"], "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"], "scopes_supported": ["remote", "offline_access"]}


@app.post("/oauth/register")
async def register(req: Request):
    try:
        data = await req.json()
        redirects = data["redirect_uris"]
        if not isinstance(redirects, list) or not 1 <= len(redirects) <= 10 or any(not isinstance(u, str) or not check_redirect(u) for u in redirects):
            return oauth_error("invalid_redirect_uri")
        if data.get("token_endpoint_auth_method", "none") != "none":
            return oauth_error("invalid_client_metadata")
        client_id = secrets.token_urlsafe(32)
        with database() as conn:
            conn.execute("INSERT INTO clients VALUES (?,?)", (client_id, json.dumps(redirects)))
        return JSONResponse({"client_id": client_id, "client_id_issued_at": int(time.time()), "redirect_uris": redirects, "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]}, status_code=201, headers={"Cache-Control": "no-store"})
    except (ValueError, KeyError, TypeError):
        return oauth_error("invalid_client_metadata")


def client_allows(client_id: str, redirect_uri: str) -> bool:
    with database() as conn:
        client = conn.execute("SELECT redirect_uris FROM clients WHERE id=?", (client_id,)).fetchone()
    return bool(client and redirect_uri in json.loads(client["redirect_uris"]))


@app.get("/oauth/authorize")
async def authorize(req: Request):
    q = req.query_params
    cid, redirect = q.get("client_id", ""), q.get("redirect_uri", "")
    challenge, state = q.get("code_challenge", ""), q.get("state", "")
    if not client_allows(cid, redirect) or q.get("response_type") != "code" or q.get("code_challenge_method") != "S256" or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", challenge) or not state or len(state) > 512:
        return oauth_error("invalid_request")
    if not require_resource(q.get("resource")):
        return oauth_error("invalid_target")
    login_request = secrets.token_urlsafe(32)
    with database() as conn:
        conn.execute("INSERT INTO authorization_requests VALUES (?,?,?,?,?,?,?)", (digest(login_request), cid, redirect, state, challenge, resource_url(), int(time.time()) + 300))
    page = """<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Authorize Remote Dorado Commander</title><body style='font:16px system-ui;max-width:28rem;margin:5rem auto;padding:1rem'>
<h1>Authorize access to your computers</h1><p>This MCP connection can run shell commands and modify files on every paired computer. Only approve an AI client you trust.</p>
<form action='/oauth/approve' method='post'><input type='hidden' name='request_id' value='""" + html.escape(login_request, quote=True) + """'>
<label>Administrator password <input name='password' type='password' required autofocus autocomplete='current-password'></label>
<button type='submit'>Approve access</button></form></body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"})


@app.post("/oauth/approve")
async def approve(req: Request):
    form = await req.form()
    request_id, password = str(form.get("request_id", "")), str(form.get("password", ""))
    # Consume the request even when password is incorrect; no replay of approval form.
    with database() as conn:
        row = conn.execute("SELECT * FROM authorization_requests WHERE id_hash=?", (digest(request_id),)).fetchone()
        conn.execute("DELETE FROM authorization_requests WHERE id_hash=?", (digest(request_id),))
    if not row or row["expires"] <= time.time():
        return oauth_error("invalid_request", 403)
    if not secrets.compare_digest(password, os.environ["DORADO_ADMIN_PASSWORD"]):
        return oauth_error("access_denied", 403)
    code = secrets.token_urlsafe(40)
    with database() as conn:
        conn.execute("INSERT INTO codes VALUES (?,?,?,?,?,?)", (digest(code), row["client_id"], row["redirect_uri"], row["challenge"], row["resource"], int(time.time()) + 120))
    parts = urlsplit(row["redirect_uri"])
    query = parts.query + ("&" if parts.query else "") + urlencode({"code": code, "state": row["state"], "iss": base_url()})
    return RedirectResponse(parts._replace(query=query).geturl(), status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def issue_tokens(conn: sqlite3.Connection, cid: str) -> dict[str, Any]:
    access, refresh = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    now = int(time.time())
    conn.execute("INSERT INTO tokens VALUES (?,?,?,?)", (digest(access), cid, "access", now + 3600))
    conn.execute("INSERT INTO tokens VALUES (?,?,?,?)", (digest(refresh), cid, "refresh", now + 30 * 86400))
    return {"access_token": access, "token_type": "Bearer", "expires_in": 3600, "refresh_token": refresh, "scope": "remote offline_access"}


@app.post("/oauth/token")
async def token(req: Request):
    form = await req.form()
    cid, grant = str(form.get("client_id", "")), str(form.get("grant_type", ""))
    if not require_resource(str(form.get("resource", ""))):
        return oauth_error("invalid_target")
    now = time.time()
    if grant == "authorization_code":
        code, verifier, redirect = str(form.get("code", "")), str(form.get("code_verifier", "")), str(form.get("redirect_uri", ""))
        if not 43 <= len(verifier) <= 128 or not re.fullmatch(r"[A-Za-z0-9_.~-]+", verifier):
            return oauth_error("invalid_grant")
        with database() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM codes WHERE code_hash=?", (digest(code),)).fetchone()
            if row:
                conn.execute("DELETE FROM codes WHERE code_hash=?", (digest(code),))
            if not row or row["expires"] <= now or row["client_id"] != cid or row["redirect_uri"] != redirect or row["challenge"] != base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="):
                return oauth_error("invalid_grant")
            result = issue_tokens(conn, cid)
    elif grant == "refresh_token":
        refresh = str(form.get("refresh_token", ""))
        with database() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM tokens WHERE token_hash=? AND kind='refresh'", (digest(refresh),)).fetchone()
            if not row or row["expires"] <= now or row["client_id"] != cid:
                return oauth_error("invalid_grant")
            conn.execute("DELETE FROM tokens WHERE token_hash=?", (digest(refresh),))
            result = issue_tokens(conn, cid)
    else:
        return oauth_error("unsupported_grant_type")
    return JSONResponse(result, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def authenticated(req: Request) -> bool:
    auth = req.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return False
    with database() as conn:
        row = conn.execute("SELECT expires FROM tokens WHERE token_hash=? AND kind='access'", (digest(auth[7:]),)).fetchone()
    return bool(row and row["expires"] > time.time())


def unauthorized() -> JSONResponse:
    return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{base_url()}/.well-known/oauth-protected-resource", scope="remote"', "Cache-Control": "no-store"})


def rpc_error(id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


@app.post("/mcp")
async def mcp(req: Request):
    if not authenticated(req):
        return unauthorized()
    try:
        body = await req.json()
    except ValueError:
        return JSONResponse(rpc_error(None, -32700, "Parse error"))
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or not isinstance(body.get("method"), str):
        return JSONResponse(rpc_error(body.get("id") if isinstance(body, dict) else None, -32600, "Invalid Request"))
    method, id_ = body["method"], body.get("id")
    if "id" not in body:
        return Response(status_code=202)
    if method == "initialize":
        return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": {"protocolVersion": VERSION, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "remote-dorado-commander", "version": "0.1.0"}}})
    if method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = body.get("params", {})
        name = params.get("name") if isinstance(params, dict) else None
        args = params.get("arguments", {}) if isinstance(params, dict) else {}
        if name not in OPS | {"list_devices"}:
            return JSONResponse(rpc_error(id_, -32602, "Unknown tool"))
        if not isinstance(args, dict):
            return JSONResponse(rpc_error(id_, -32602, "Invalid tool arguments"))
        try:
            if name == "list_devices":
                output = {"devices": sorted(hub.devices)}
            else:
                device = args.get("device")
                if not isinstance(device, str) or not device:
                    raise ValueError("device is required")
                output = await hub.ask(device, name, {k: v for k, v in args.items() if k != "device"})
            result = {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}
        except (ValueError, RuntimeError, KeyError, TimeoutError) as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        return JSONResponse(rpc_error(id_, -32601, "Method not found"))
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result}, headers={"Cache-Control": "no-store"})


@app.get("/mcp")
async def mcp_get(req: Request):
    return unauthorized() if not authenticated(req) else Response(status_code=405, headers={"Allow": "POST"})


@app.websocket("/agent/ws")
async def agent_socket(ws: WebSocket):
    bearer = ws.headers.get("authorization", "")
    name = ws.headers.get("x-device-name", "")
    if not secrets.compare_digest(bearer, "Bearer " + os.environ["DORADO_AGENT_TOKEN"]) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        await ws.close(code=4401)
        return
    if name in hub.devices:
        await ws.close(code=4409)
        return
    await ws.accept()
    device = Device(socket=ws, pending={})
    hub.devices[name] = device
    try:
        while True:
            response = await ws.receive_json()
            if not isinstance(response, dict) or not isinstance(response.get("id"), str):
                continue
            fut = device.pending.get(response["id"])
            if fut and not fut.done():
                fut.set_result(response)
    except WebSocketDisconnect:
        pass
    finally:
        hub.devices.pop(name, None)
        for fut in device.pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("Agent disconnected"))


def main():
    uvicorn.run("remote_dorado.server:app", host=os.environ.get("DORADO_BIND_HOST", "127.0.0.1"), port=int(os.environ.get("DORADO_PORT", "8765")), proxy_headers=False)


if __name__ == "__main__":
    main()
