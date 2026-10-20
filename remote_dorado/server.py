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
import string
import sqlite3
import time
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.websockets import WebSocketDisconnect
import uvicorn

VERSION = "2025-06-18"
APP_VERSION = "0.4.0"
PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAIR_LIFETIME_SECONDS = 600
PAIR_POLL_INTERVAL = 3
TOOLS: list[dict[str, Any]] = [
    {"name": "list_devices", "description": "List online computers paired with this private server.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ping_device", "description": "Check the connection and OS of a paired computer.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}}, "required": ["device"]}},
    {"name": "list_directory", "description": "List directory entries on a paired computer.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["device", "path"]}},
    {"name": "read_file", "description": "Read UTF-8 text from a file. Returns up to 250000 characters.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "path": {"type": "string"}, "max_chars": {"type": "integer"}}, "required": ["device", "path"]}},
    {"name": "write_file", "description": "Create a text file; overwrite requires explicit overwrite=true.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "path": {"type": "string"}, "content": {"type": "string"}, "overwrite": {"type": "boolean"}}, "required": ["device", "path", "content"]}, "annotations": {"destructiveHint": True}},
    {"name": "edit_file", "description": "Replace an exact unique text block in an existing UTF-8 file.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["device", "path", "old_text", "new_text"]}, "annotations": {"destructiveHint": True}},
    {"name": "search_files", "description": "Search filenames with a glob (e.g. *.py) beneath a directory.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "path": {"type": "string"}, "pattern": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["device", "path"]}},
    {"name": "execute_command", "description": "Execute an arbitrary shell command as the paired computer's OS user; high risk. Max 120 seconds and 250 KB per stream.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "cwd": {"type": "string"}, "command": {"type": "string"}, "timeout_seconds": {"type": "integer"}}, "required": ["device", "cwd", "command"]}, "annotations": {"destructiveHint": True, "openWorldHint": True}},
    {"name": "list_all_sessions", "description": "Discover every agent-owned persistent terminal across all connected PCs. Returns device and session_id for each; offline machines cannot be inspected.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_active_sessions", "description": "List persistent interactive shells and processes on one PC, including their running/exited state and output cursors.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}}, "required": ["device"]}},
    {"name": "start_process", "description": "Start a persistent shell (command omitted) or a persistent command on one PC; returns session_id. Survives MCP calls and connection interruptions while agent runs.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "cwd": {"type": "string", "description": "Allowed working directory. Defaults to home."}, "command": {"type": "string", "description": "Optional command. Omit for interactive shell."}}, "required": ["device"]}, "annotations": {"openWorldHint": True}},
    {"name": "send_input_to_process", "description": "Write input to an existing session on a named PC. For a shell, this executes the command (newline appended by default).", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "session_id": {"type": "string"}, "input": {"type": "string"}, "append_newline": {"type": "boolean", "default": True}, "wait_ms": {"type": "integer", "minimum": 0, "maximum": 5000, "description": "Wait briefly for command output; default 200ms."}}, "required": ["device", "session_id", "input"]}, "annotations": {"openWorldHint": True, "destructiveHint": True}},
    {"name": "read_process_output", "description": "Read buffered terminal output without consuming it. Pass last cursor to continue reading; up to 100 KB per call.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "session_id": {"type": "string"}, "cursor": {"type": "integer", "minimum": 0}, "max_bytes": {"type": "integer", "minimum": 1, "maximum": 100000}}, "required": ["device", "session_id"]}},
    {"name": "terminate_session", "description": "Terminate an agent-owned persistent process on a named PC. Optionally force kill.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "session_id": {"type": "string"}, "force": {"type": "boolean"}}, "required": ["device", "session_id"]}, "annotations": {"destructiveHint": True}},
    {"name": "forget_session", "description": "Remove an already-exited session and its buffered output from the agent.", "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}, "session_id": {"type": "string"}}, "required": ["device", "session_id"]}, "annotations": {"destructiveHint": True}},
]
OPS = {t["name"] for t in TOOLS} - {"list_devices", "list_all_sessions"}


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
    for var in ("DORADO_PUBLIC_URL", "DORADO_ADMIN_PASSWORD"):
        if not os.environ.get(var):
            raise RuntimeError(f"Missing required environment variable: {var}")
    if not base_url().startswith("https://") and not base_url().startswith("http://127.0.0.1") and not base_url().startswith("http://localhost"):
        raise RuntimeError("Use public HTTPS for DORADO_PUBLIC_URL; HTTP is allowed only on loopback for local tests")
    if len(os.environ["DORADO_ADMIN_PASSWORD"]) < 16:
        raise RuntimeError("DORADO_ADMIN_PASSWORD must be 16+ chars")
    if os.environ.get("DORADO_AGENT_TOKEN") and len(os.environ["DORADO_AGENT_TOKEN"]) < 32:
        raise RuntimeError("Optional legacy DORADO_AGENT_TOKEN must be 32+ chars")
    db_path().parent.mkdir(parents=True, exist_ok=True)
    with database() as conn:
        conn.executescript("""
          CREATE TABLE IF NOT EXISTS clients (id TEXT PRIMARY KEY, redirect_uris TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS authorization_requests (id_hash TEXT PRIMARY KEY, client_id TEXT, redirect_uri TEXT, state TEXT, challenge TEXT, resource TEXT, expires INTEGER);
          CREATE TABLE IF NOT EXISTS codes (code_hash TEXT PRIMARY KEY, client_id TEXT, redirect_uri TEXT, challenge TEXT, resource TEXT, expires INTEGER);
          CREATE TABLE IF NOT EXISTS tokens (token_hash TEXT PRIMARY KEY, client_id TEXT, kind TEXT, expires INTEGER);
          CREATE TABLE IF NOT EXISTS known_devices (name TEXT PRIMARY KEY, last_seen INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS paired_devices (
            name TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, created INTEGER NOT NULL
          );
          CREATE TABLE IF NOT EXISTS pairing_requests (
            device_hash TEXT PRIMARY KEY, user_hash TEXT UNIQUE NOT NULL,
            device_name TEXT NOT NULL, expires INTEGER NOT NULL,
            created INTEGER NOT NULL, origin TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0
          );
        """)


@dataclass
class Device:
    socket: WebSocket
    pending: dict[str, asyncio.Future]
    send_lock: asyncio.Lock


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
                async with device.send_lock:
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


# The local executable never needs the relay's shared administrator/agent token.
# Pairing requires explicit browser confirmation with the self-hosted relay administrator.
# This authorizes a machine on YOUR relay; it is not an OpenAI/ChatGPT sign-in.
def normalize_user_code(code: str) -> str:
    return "".join(c for c in code.upper() if c in PAIR_ALPHABET)


def pairing_headers() -> dict[str, str]:
    return {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}


@app.post("/pair/device")
async def pairing_start(request: Request):
    # No login needed to *request* pairing; approval is administrator protected.
    try:
        data = await request.json()
    except ValueError:
        return oauth_error("invalid_request")
    name = data.get("device_name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        return oauth_error("invalid_device_name")
    now = int(time.time())
    origin = request.client.host if request.client else "unknown"
    with database() as conn:
        conn.execute("DELETE FROM pairing_requests WHERE expires < ?", (now,))
        # Rate-limit unauthenticated issuance (do not trust spoofable forwarded headers).
        count = conn.execute("SELECT count(*) FROM pairing_requests WHERE origin=? AND created > ?", (origin, now - 600)).fetchone()[0]
        if count >= 30:
            return JSONResponse({"error": "slow_down"}, status_code=429, headers=pairing_headers())
        device_code = secrets.token_urlsafe(48)
        user = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(12))
        conn.execute("INSERT INTO pairing_requests (device_hash,user_hash,device_name,expires,created,origin,status,attempts) VALUES (?,?,?,?,?,?,'pending',0)",
                     (digest(device_code), digest(user), name, now + PAIR_LIFETIME_SECONDS, now, origin))
    pretty = "-".join(user[i:i + 4] for i in range(0, 12, 4))
    return JSONResponse({"device_code": device_code, "user_code": pretty,
                         "verification_uri": base_url() + "/pair",
                         "verification_uri_complete": base_url() + "/pair?code=" + pretty,
                         "expires_in": PAIR_LIFETIME_SECONDS, "interval": PAIR_POLL_INTERVAL},
                        headers=pairing_headers())


@app.get("/pair")
async def pairing_form(request: Request):
    candidate = html.escape(request.query_params.get("code", "")[:32], quote=True)
    page = """<!doctype html><html lang='es'><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Vincular PC | Dorado Commander</title>
<body style='font:16px system-ui;max-width:34rem;margin:4rem auto;padding:1rem'>
<h1>Vincular un ordenador</h1>
<p>Aprueba solo códigos que veas en TU equipo. Autorizar concede acceso a archivos y terminal mediante el MCP.</p>
<form action='/pair/approve' method='post'>
<label>Código del equipo <input name='user_code' required maxlength='20' autocomplete='off' value='""" + candidate + """'></label>
<p><label>Contraseña de administración del servidor <input type='password' name='password' required autocomplete='current-password'></label></p>
<button type='submit'>Autorizar ordenador</button></form></body></html>"""
    return HTMLResponse(page, headers={**pairing_headers(), "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"})


@app.post("/pair/approve")
async def pairing_approve(request: Request):
    form = await request.form()
    user_code = normalize_user_code(str(form.get("user_code", "")))
    password = str(form.get("password", ""))
    if len(user_code) != 12:
        return oauth_error("invalid_user_code", 400)
    now = int(time.time())
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM pairing_requests WHERE user_hash=?", (digest(user_code),)).fetchone()
        if not row or row["expires"] <= now or row["status"] != "pending" or row["attempts"] >= 5:
            return oauth_error("expired_or_invalid_code", 403)
        if not secrets.compare_digest(password, os.environ["DORADO_ADMIN_PASSWORD"]):
            conn.execute("UPDATE pairing_requests SET attempts=attempts+1 WHERE user_hash=?", (digest(user_code),))
            return oauth_error("access_denied", 403)
        conn.execute("UPDATE pairing_requests SET status='approved' WHERE user_hash=?", (digest(user_code),))
    return HTMLResponse("<h1>Equipo autorizado</h1><p>Puedes cerrar esta pestaña. Vuelve al programa; se conectará automáticamente.</p>", headers=pairing_headers())


@app.post("/pair/token")
async def pairing_token(request: Request):
    try:
        data = await request.json()
    except ValueError:
        return oauth_error("invalid_request")
    device_code = data.get("device_code") if isinstance(data, dict) else None
    if not isinstance(device_code, str) or not 32 <= len(device_code) <= 256:
        return oauth_error("invalid_device_code")
    now = int(time.time())
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM pairing_requests WHERE device_hash=?", (digest(device_code),)).fetchone()
        if not row:
            return oauth_error("invalid_device_code", 400)
        if row["expires"] <= now:
            conn.execute("DELETE FROM pairing_requests WHERE device_hash=?", (digest(device_code),))
            return oauth_error("expired_token", 400)
        if row["status"] == "pending":
            return oauth_error("authorization_pending", 400)
        if row["status"] != "approved":
            return oauth_error("access_denied", 400)
        # One-time exchange. Explicit approval on this relay rotates any older device credential.
        token = secrets.token_urlsafe(48)
        conn.execute("INSERT INTO paired_devices(name,token_hash,created) VALUES (?,?,?) "
                     "ON CONFLICT(name) DO UPDATE SET token_hash=excluded.token_hash,created=excluded.created",
                     (row["device_name"], digest(token), now))
        conn.execute("DELETE FROM pairing_requests WHERE device_hash=?", (digest(device_code),))
    return JSONResponse({"device_name": row["device_name"], "agent_token": token}, headers=pairing_headers())


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
        return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": {"protocolVersion": VERSION, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "remote-dorado-commander", "version": APP_VERSION}}})
    if method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = body.get("params", {})
        name = params.get("name") if isinstance(params, dict) else None
        args = params.get("arguments", {}) if isinstance(params, dict) else {}
        if name not in OPS | {"list_devices", "list_all_sessions"}:
            return JSONResponse(rpc_error(id_, -32602, "Unknown tool"))
        if not isinstance(args, dict):
            return JSONResponse(rpc_error(id_, -32602, "Invalid tool arguments"))
        try:
            if name == "list_devices":
                with database() as conn:
                    known = conn.execute("SELECT name, last_seen FROM known_devices ORDER BY name").fetchall()
                output = {"devices": sorted(hub.devices), "count": len(hub.devices),
                          "known_devices": [{"name": d["name"], "online": d["name"] in hub.devices,
                                             "last_seen": d["last_seen"]} for d in known]}
            elif name == "list_all_sessions":
                # Parallel per-device queries so a slow/offline PC does not block others.
                device_names = sorted(hub.devices)
                responses = await asyncio.gather(
                    *(hub.ask(device, "list_active_sessions", {}) for device in device_names),
                    return_exceptions=True,
                )
                all_sessions = []
                errors = {}
                for device, response in zip(device_names, responses):
                    if isinstance(response, BaseException):
                        errors[device] = str(response)
                    else:
                        all_sessions.extend({"device": device, **session} for session in response.get("sessions", []))
                output = {"online_devices": device_names, "sessions": all_sessions, "count": len(all_sessions), "errors": errors}
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
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        await ws.close(code=4401)
        return
    token = bearer[7:] if bearer.startswith("Bearer ") else ""
    with database() as conn:
        row = conn.execute("SELECT token_hash FROM paired_devices WHERE name=?", (name,)).fetchone()
    valid_pair = bool(row and token and secrets.compare_digest(row["token_hash"], digest(token)))
    legacy = os.environ.get("DORADO_AGENT_TOKEN", "")
    valid_legacy = bool(legacy and secrets.compare_digest(token, legacy))
    if not (valid_pair or valid_legacy):
        await ws.close(code=4401)
        return
    if name in hub.devices:
        await ws.close(code=4409)
        return
    await ws.accept()
    with database() as conn:
        conn.execute("INSERT INTO known_devices(name, last_seen) VALUES (?, ?) "
                     "ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen", (name, int(time.time())))
    device = Device(socket=ws, pending={}, send_lock=asyncio.Lock())
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
        if hub.devices.get(name) is device:
            hub.devices.pop(name, None)
        with database() as conn:
            conn.execute("UPDATE known_devices SET last_seen=? WHERE name=?", (int(time.time()), name))
        for fut in device.pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("Agent disconnected"))


def main():
    uvicorn.run("remote_dorado.server:app", host=os.environ.get("DORADO_BIND_HOST", "127.0.0.1"), port=int(os.environ.get("DORADO_PORT", "8765")), proxy_headers=False)


if __name__ == "__main__":
    main()
