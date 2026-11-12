"""Single-owner, self-service portal for ONE isolated Dorado relay.

Pilot scope: not a shared SaaS. The account is created by an operator using
the local bootstrap command; public signup is intentionally disabled.
"""
from __future__ import annotations

from dataclasses import dataclass
from getpass import getpass
from html import escape
import hashlib
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

router = APIRouter()
COOKIE = "dorado_portal"
SESSION_SECONDS = 8 * 60 * 60
FAILED_WINDOW_SECONDS = 15 * 60
MAX_FAILED = 8
EMAIL_PATTERN = re.compile(r"[^\s@]{1,64}@[^\s@]{1,189}\.[^\s@]{2,63}\Z")

# A separate credentials store is used for the user-facing portal. Never store
# plaintext passwords, bearer tokens or CSRF values in the SQLite database.
def init_portal_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS portal_owner (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS portal_sessions (
        token_hash TEXT PRIMARY KEY,
        csrf_hash TEXT NOT NULL,
        expires INTEGER NOT NULL,
        created INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_portal_sessions_expiry ON portal_sessions(expires);
    CREATE TABLE IF NOT EXISTS portal_login_failures (
        email_hash TEXT NOT NULL,
        origin TEXT NOT NULL,
        created INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_portal_login_failures_age ON portal_login_failures(created);
    CREATE TABLE IF NOT EXISTS portal_invitation (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        email TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        expires INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS portal_invitation_failures (
        origin TEXT NOT NULL,
        created INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_invitation_failures_age ON portal_invitation_failures(created);
    """)


def valid_email(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid email")
    value = value.strip().lower()
    if len(value) > 254 or not EMAIL_PATTERN.fullmatch(value):
        raise ValueError("Invalid email")
    return value


def validate_password(value: str) -> None:
    if not isinstance(value, str) or len(value) < 12 or len(value) > 1024:
        raise ValueError("Password must contain between 12 and 1024 characters")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def password_hash(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt-v1$" + salt.hex() + "$" + key.hex()


def password_matches(candidate: str, encoded: str) -> bool:
    if len(candidate) > 1024:
        return False
    try:
        algorithm, salt, expected = encoded.split("$")
        if algorithm != "scrypt-v1":
            return False
        found = hashlib.scrypt(candidate.encode("utf-8"), salt=bytes.fromhex(salt),
                               n=2**14, r=8, p=1, dklen=32)
        return secrets.compare_digest(found, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def create_owner(conn: sqlite3.Connection, email: str, password: str) -> None:
    email = valid_email(email)
    hashed = password_hash(password)
    conn.execute("BEGIN IMMEDIATE")
    if conn.execute("SELECT 1 FROM portal_owner WHERE id=1").fetchone():
        raise ValueError("Owner already provisioned; use password rotation instead")
    conn.execute("INSERT INTO portal_owner(id,email,password_hash,created) VALUES (1,?,?,?)",
                 (email, hashed, int(time.time())))


def issue_invite(conn: sqlite3.Connection, email: str) -> str:
    """Operator-only one-time account activation; do not log invitation codes."""
    email = valid_email(email)
    code = secrets.token_urlsafe(32)
    conn.execute("BEGIN IMMEDIATE")
    if conn.execute("SELECT 1 FROM portal_owner WHERE id=1").fetchone():
        raise ValueError("Owner already provisioned")
    conn.execute(
        "INSERT INTO portal_invitation (id,email,code_hash,expires) VALUES (1,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET email=excluded.email, code_hash=excluded.code_hash, "
        "expires=excluded.expires",
        (email, _digest(code), int(time.time()) + 24 * 60 * 60),
    )
    return code


def invitation_pending() -> bool:
    from .server import database
    with database() as conn:
        row = conn.execute("SELECT expires FROM portal_invitation WHERE id=1").fetchone()
    return bool(row and row["expires"] > time.time())


def owner_exists() -> bool:
    from .server import database
    with database() as conn:
        return bool(conn.execute("SELECT 1 FROM portal_owner WHERE id=1").fetchone())


def commercial_portal_enabled() -> bool:
    return os.environ.get("DORADO_COMMERCIAL_MODE") == "1"


@dataclass(frozen=True)
class OwnerSession:
    csrf: str
    token_hash: str


def owner_session(request: Request) -> OwnerSession | None:
    """Session tokens are HTTPS-only cookies on the public relay.

    CSRF is derived deterministically from a high-entropy cookie secret. Only
    its hash is stored. Both validation and rendering require a live session.
    """
    from .server import database
    cookie = request.cookies.get(COOKIE, "")
    if len(cookie) < 48 or len(cookie) > 192 or not re.fullmatch(r"[A-Za-z0-9_-]+", cookie):
        return None
    token_hash = _digest(cookie)
    with database() as conn:
        record = conn.execute(
            "SELECT csrf_hash, expires FROM portal_sessions WHERE token_hash=?",
            (token_hash,),
        ).fetchone()
    if not record or record["expires"] <= time.time():
        return None
    csrf = hashlib.sha256(("dorado-csrf:" + cookie).encode()).hexdigest()
    if not secrets.compare_digest(record["csrf_hash"], _digest(csrf)):
        return None
    return OwnerSession(csrf=csrf, token_hash=token_hash)


def csrf_ok(session: OwnerSession | None, candidate: str) -> bool:
    return bool(session and candidate and secrets.compare_digest(session.csrf, candidate))


def safe_next(value: str | None) -> str:
    """Only allow local portal or approval paths after authentication."""
    if not isinstance(value, str) or len(value) > 3500:
        return "/portal"
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/portal"
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return "/portal"
    path = value.split("?", 1)[0].split("#", 1)[0]
    if path in ("/portal", "/pair", "/oauth/authorize"):
        return value.split("#", 1)[0]
    return "/portal"


def login_redirect(destination: str = "/portal") -> RedirectResponse:
    return RedirectResponse("/portal/login?" + urlencode({"next": safe_next(destination)}),
                            status_code=303, headers=_HEADERS)


_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
}

_STYLE = """
:root{color-scheme:dark;font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
--bg:#070c15;--card:#121d2d;--soft:#192639;--border:#2b3a50;--text:#edf2f8;--muted:#9aaec2;
--accent:#f2bd65;--green:#68ddb5}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(ellipse at 30% -15%,#20334c 0,#070c15 50%);
color:var(--text)}.shell{width:min(1120px,100%);margin:auto;padding:26px 24px 70px}
nav{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:48px}
.brand{display:flex;align-items:center;gap:12px;font-size:17px;font-weight:760;letter-spacing:-.3px}
.logo{display:grid;place-items:center;width:41px;height:41px;border-radius:11px;background:#f2bd651a;border:1px solid #f2bd6555;
color:var(--accent);font-size:22px;font-weight:850}
.navright{display:flex;align-items:center;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:13px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
h1{margin:0 0 10px;font-size:clamp(28px,4vw,38px);letter-spacing:-.95px}
h2{font-size:17px;letter-spacing:-.35px;margin:0}
.lead{color:var(--muted);line-height:1.65;margin:0 0 30px;max-width:720px}
.kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:28px}
.kpi,.panel{background:var(--card);border:1px solid var(--border);border-radius:17px}
.kpi{padding:23px}.kpi .num{font-size:34px;letter-spacing:-1px;font-weight:760}
.muted{color:var(--muted)}.small{font-size:12px}.kpi .name{color:var(--muted);font-size:13px;margin-top:5px}
.panel{padding:24px;margin-bottom:18px}.panel-head{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:18px}
table{border-collapse:collapse;width:100%;text-align:left}th{font-size:11px;color:var(--muted);font-weight:650;text-transform:uppercase;letter-spacing:1px}
td,th{padding:15px 10px;border-bottom:1px solid #2b3a506f;vertical-align:middle}
tr:last-child td{border-bottom:0}td{font-size:13px;overflow-wrap:anywhere}
.status{display:inline-flex;align-items:center;gap:7px;font-size:12px}
.dot{width:7px;height:7px;border-radius:100%;background:#778599}
.status.online .dot{background:var(--green);box-shadow:0 0 10px #68ddb54d}
.code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11px;color:var(--muted);word-break:break-all}
button{font:inherit;cursor:pointer;border-radius:9px;padding:10px 13px;background:var(--accent);color:#0f1723;border:0;font-weight:760}
button:hover{filter:brightness(1.06)}button.ghost{border:1px solid var(--border);background:transparent;color:var(--text)}
button.danger{border:1px solid #db7c7c55;background:#db7c7c0e;color:#ffaaa9}
form{margin:0}label{display:block;color:var(--muted);font-size:13px;margin-bottom:8px}
input{display:block;width:100%;background:#0b1422;color:var(--text);font:inherit;border:1px solid #40516b;
padding:13px;border-radius:10px;margin-bottom:19px;outline:none}
input:focus{border-color:var(--accent);box-shadow:0 0 0 3px #f2bd6529}
.auth{max-width:455px;margin:8vh auto 0}.auth .panel{padding:31px}
.auth h1{font-size:27px}.auth button{width:100%}
.error{background:#6b282833;border:1px solid #c6656555;color:#ffb7b7;padding:13px;border-radius:10px;margin:0 0 19px;font-size:13px}
.notice{background:#f2bd650c;color:#e9cf9b;border:1px solid #f2bd6530;border-radius:11px;padding:16px;
font-size:13px;line-height:1.6}
.empty{color:var(--muted);font-size:13px;padding:24px 8px}
.actions{display:flex;gap:8px;justify-content:flex-end}.scroll{overflow-x:auto}
@media(max-width:650px){nav{margin-bottom:30px;align-items:flex-start;flex-direction:column}
.shell{padding:20px 15px 55px}.kpis{grid-template-columns:1fr 1fr}.panel{padding:16px}.kpi{padding:16px}
.kpis .kpi:last-child{grid-column:1 / -1}td,th{padding:12px 7px}h1{font-size:28px}}
"""


def _page(title: str, body: str, email: str | None = None, csrf: str = "") -> HTMLResponse:
    logout = (
        f"<form method='post' action='/portal/logout'><input type='hidden' name='csrf' value='{escape(csrf)}'>"
        "<button class='ghost' type='submit'>Cerrar sesión</button></form>"
        if email else ""
    )
    account = (
        f"<div class='navright'><span>{escape(email)}</span><a href='/portal/security'>Seguridad</a>{logout}</div>"
        if email else "<span class='muted small'>Panel de cliente · acceso privado</span>"
    )
    html = (
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)} | Dorado Relay</title><style>{_STYLE}</style></head>"
        "<body><div class='shell'><nav><div class='brand'><div class='logo'>D</div>"
        "<span>Dorado Relay</span></div>" + account + "</nav>" + body +
        "</div></body></html>"
    )
    return HTMLResponse(html, headers=_HEADERS)


def _owner_email() -> str | None:
    from .server import database
    with database() as conn:
        row = conn.execute("SELECT email FROM portal_owner WHERE id=1").fetchone()
        return row["email"] if row else None


def _login_page(next_path: str, error: str = "") -> HTMLResponse:
    error_html = "<div class='error'>No se pudo iniciar sesión. Revisa los datos o inténtalo más tarde.</div>" if error else ""
    body = (
        "<section class='auth'><div class='panel'><h1>Accede a tu espacio</h1>"
        "<p class='lead'>Gestiona tus ordenadores y conexiones MCP con seguridad.</p>"
        + error_html +
        "<form method='post' action='/portal/login'>"
        f"<input type='hidden' name='next' value='{escape(safe_next(next_path), quote=True)}'>"
        "<label for='email'>Correo electrónico</label>"
        "<input name='email' id='email' type='email' maxlength='254' autocomplete='username' required>"
        "<label for='password'>Contraseña</label>"
        "<input name='password' id='password' type='password' autocomplete='current-password' required>"
        "<button type='submit'>Iniciar sesión</button></form></div>"
        "<p class='muted small'>Tu cuenta se crea al contratar un relay aislado. "
        "No se permiten registros públicos en esta versión piloto.</p></section>"
    )
    return _page("Iniciar sesión", body)


def _activation_page(error: bool = False) -> HTMLResponse:
    error_html = (
        "<div class='error'>No se ha podido activar la cuenta. Comprueba el código "
        "y el correo, o solicita uno nuevo al administrador.</div>" if error else ""
    )
    body = (
        "<section class='auth'><div class='panel'><h1>Activa tu espacio</h1>"
        "<p class='lead'>Introduce el código de un solo uso que te ha facilitado "
        "Dorado Relay. Tu contraseña la eliges tú.</p>" + error_html +
        "<form method='post' action='/portal/activate'>"
        "<label for='email'>Correo de la invitación</label>"
        "<input id='email' name='email' type='email' required maxlength='254' autocomplete='email'>"
        "<label for='code'>Código de activación</label>"
        "<input id='code' name='code' type='text' required maxlength='120' spellcheck='false'>"
        "<label for='password'>Crea una contraseña (mínimo 12 caracteres)</label>"
        "<input id='password' name='password' type='password' required minlength='12' autocomplete='new-password'>"
        "<label for='repeat'>Repite la contraseña</label>"
        "<input id='repeat' name='repeat' type='password' required minlength='12' autocomplete='new-password'>"
        "<button type='submit'>Activar cuenta</button></form></div>"
        "<p class='muted small'>Por seguridad, las cuentas nuevas requieren una invitación. "
        "No compartas tu código con otras personas.</p></section>"
    )
    return _page("Activar cuenta", body)


@router.get("/portal/activate")
async def activate_form(request: Request):
    if owner_exists():
        return RedirectResponse("/portal/login", status_code=303, headers=_HEADERS)
    if not invitation_pending():
        return Response("Invitación no disponible", status_code=503, headers=_HEADERS)
    return _activation_page()


@router.post("/portal/activate")
async def activate_submit(request: Request):
    from .server import database
    if owner_exists():
        return Response("Cuenta ya activada", status_code=403, headers=_HEADERS)
    form = await request.form()
    try:
        email = valid_email(str(form.get("email", "")))
        password = str(form.get("password", ""))
        code = str(form.get("code", ""))
        if password != str(form.get("repeat", "")):
            return Response("Las contraseñas no coinciden", status_code=400, headers=_HEADERS)
        validate_password(password)
    except ValueError:
        return _activation_page(error=True)
    if not 30 <= len(code) <= 120 or not re.fullmatch(r"[A-Za-z0-9_-]+", code):
        return _activation_page(error=True)

    now = int(time.time())
    origin = request.client.host if request.client else "unknown"
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM portal_invitation_failures WHERE created<?",
                     (now - FAILED_WINDOW_SECONDS,))
        failures = conn.execute(
            "SELECT count(*) FROM portal_invitation_failures WHERE origin=?", (origin,)
        ).fetchone()[0]
        if failures >= MAX_FAILED:
            return Response("Demasiados intentos. Solicita un código nuevo más tarde.",
                            status_code=429, headers=_HEADERS)
        row = conn.execute(
            "SELECT email,code_hash,expires FROM portal_invitation WHERE id=1"
        ).fetchone()
        if not row or row["expires"] <= now or not (
            secrets.compare_digest(email, row["email"])
            and secrets.compare_digest(_digest(code), row["code_hash"])
        ):
            conn.execute(
                "INSERT INTO portal_invitation_failures(origin,created) VALUES (?,?)",
                (origin, now),
            )
            return _activation_page(error=True)
        if conn.execute("SELECT 1 FROM portal_owner WHERE id=1").fetchone():
            return Response("Cuenta ya activada", status_code=403, headers=_HEADERS)
        # Hash only after the one-time invite and rate limit are verified.
        # Invalid unauthenticated requests must not trigger the memory-hard KDF.
        hashed_password = password_hash(password)
        conn.execute(
            "INSERT INTO portal_owner(id,email,password_hash,created) VALUES (1,?,?,?)",
            (email, hashed_password, now),
        )
        conn.execute("DELETE FROM portal_invitation")
        conn.execute("DELETE FROM portal_invitation_failures")
    return RedirectResponse("/portal/login", status_code=303, headers=_HEADERS)


@router.get("/portal/login")
async def login_form(request: Request):
    if owner_session(request):
        return RedirectResponse("/portal", status_code=303, headers=_HEADERS)
    if not owner_exists():
        return (RedirectResponse("/portal/activate", status_code=303, headers=_HEADERS)
                if invitation_pending()
                else Response("Portal no configurado", status_code=503, headers=_HEADERS))
    return _login_page(request.query_params.get("next", "/portal"))


@router.post("/portal/login")
async def login_submit(request: Request):
    from .server import database
    if not owner_exists():
        return Response("Portal no configurado", status_code=503, headers=_HEADERS)
    form = await request.form()
    next_path = safe_next(str(form.get("next", "/portal")))
    raw_email = str(form.get("email", ""))
    password = str(form.get("password", ""))
    email = raw_email.strip().lower()
    if len(email) > 254 or len(password) > 1024:
        return _login_page(next_path, "invalid")
    origin = request.client.host if request.client else "unknown"
    now = int(time.time())
    email_hash = _digest(email)
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM portal_login_failures WHERE created<?", (now - FAILED_WINDOW_SECONDS,))
        per_email = conn.execute(
            "SELECT count(*) FROM portal_login_failures WHERE email_hash=?",
            (email_hash,),
        ).fetchone()[0]
        per_ip = conn.execute(
            "SELECT count(*) FROM portal_login_failures WHERE origin=?", (origin,),
        ).fetchone()[0]
        if per_email >= MAX_FAILED or per_ip >= MAX_FAILED * 6:
            return Response("Demasiados intentos. Inténtalo más tarde.", status_code=429, headers=_HEADERS)
        owner = conn.execute("SELECT email,password_hash FROM portal_owner WHERE id=1").fetchone()
        accepted = bool(owner and secrets.compare_digest(email, owner["email"]) and
                        password_matches(password, owner["password_hash"]))
        if not accepted:
            conn.execute("INSERT INTO portal_login_failures VALUES (?,?,?)", (email_hash, origin, now))
            return _login_page(next_path, "invalid")
        # A successful login does not delete failed attempts from other source IPs.
        conn.execute("DELETE FROM portal_login_failures WHERE email_hash=? AND origin=?", (email_hash, origin))
        conn.execute("DELETE FROM portal_sessions WHERE expires<=?", (now,))
        cookie = secrets.token_urlsafe(48)
        csrf = hashlib.sha256(("dorado-csrf:" + cookie).encode()).hexdigest()
        conn.execute("INSERT INTO portal_sessions VALUES (?,?,?,?)",
                     (_digest(cookie), _digest(csrf), now + SESSION_SECONDS, now))
        # Bound the number of simultaneously active sessions.
        conn.execute(
            "DELETE FROM portal_sessions WHERE token_hash NOT IN "
            "(SELECT token_hash FROM portal_sessions ORDER BY created DESC,rowid DESC LIMIT 8)"
        )
    response = RedirectResponse(next_path, status_code=303, headers=_HEADERS)
    response.set_cookie(COOKIE, cookie, max_age=SESSION_SECONDS, path="/",
                        httponly=True, samesite="lax",
                        secure=os.environ.get("DORADO_PUBLIC_URL", "").startswith("https://"))
    return response


def _portal_or_login(request: Request):
    session = owner_session(request)
    if not session:
        return None, login_redirect(request.url.path)
    return session, None


@router.get("/portal")
async def dashboard(request: Request):
    from .server import database, hub, max_devices
    session, redirect = _portal_or_login(request)
    if redirect:
        return redirect
    email = _owner_email() or "Cuenta"
    with database() as conn:
        devices = conn.execute(
            "SELECT name,created FROM paired_devices ORDER BY name"
        ).fetchall()
        clients = conn.execute(
            "SELECT id,redirect_uris FROM clients ORDER BY id"
        ).fetchall()
        activity = conn.execute(
            "SELECT created,tool,device,status FROM action_audit "
            "ORDER BY created DESC,id DESC LIMIT 25"
        ).fetchall()
    used, maximum = len(devices), max_devices()
    connected = sum(row["name"] in hub.devices for row in devices)
    seats = f"{used} / {maximum}" if maximum else str(used)
    device_rows = []
    for row in devices:
        name = escape(row["name"])
        online = row["name"] in hub.devices
        label = "Conectado" if online else "Desconectado"
        state_class = "online" if online else ""
        device_rows.append(
            f"<tr><td><strong>{name}</strong></td><td><span class='status {state_class}'>"
            f"<i class='dot'></i>{label}</span></td>"
            "<td><form method='post' action='/portal/devices/revoke' class='actions'>"
            f"<input type='hidden' name='csrf' value='{session.csrf}'>"
            f"<input type='hidden' name='device_name' value='{escape(row['name'], quote=True)}'>"
            "<button type='submit' class='danger'>Desvincular</button></form></td></tr>"
        )
    client_rows = []
    for row in clients:
        client_id = escape(row["id"])
        client_rows.append(
            f"<tr><td><span class='code'>{client_id}</span></td>"
            "<td><form method='post' action='/portal/oauth/revoke' class='actions'>"
            f"<input type='hidden' name='csrf' value='{session.csrf}'>"
            f"<input type='hidden' name='client_id' value='{escape(row['id'], quote=True)}'>"
            "<button class='danger' type='submit'>Revocar acceso</button></form></td></tr>"
        )
    devices_html = (
        "<div class='scroll'><table><thead><tr><th>Equipo</th><th>Estado</th><th></th></tr>"
        "</thead><tbody>" + "".join(device_rows) + "</tbody></table></div>"
        if device_rows else "<div class='empty'>Aún no hay ordenadores vinculados.</div>"
    )
    clients_html = (
        "<div class='scroll'><table><thead><tr><th>Conexión MCP</th><th></th></tr></thead>"
        "<tbody>" + "".join(client_rows) + "</tbody></table></div>"
        if client_rows else "<div class='empty'>No hay asistentes MCP autorizados.</div>"
    )
    activity_rows = [
        "<tr><td class='code'>" + escape(time.strftime("%d/%m/%Y %H:%M UTC", time.gmtime(row["created"]))) +
        "</td><td>" + escape(row["tool"]) + "</td><td>" + escape(row["device"]) +
        "</td><td>" + ("Correcta" if row["status"] == "success" else "Error") + "</td></tr>"
        for row in activity
    ]
    activity_html = (
        "<div class='scroll'><table><thead><tr><th>Fecha</th><th>Acción</th>"
        "<th>Equipo</th><th>Resultado</th></tr></thead><tbody>" +
        "".join(activity_rows) + "</tbody></table></div>"
        if activity_rows else "<div class='empty'>Todavía no hay operaciones registradas.</div>"
    )
    body = (
        "<h1>Mis dispositivos</h1>"
        "<p class='lead'>Consulta tus ordenadores, revoca accesos y controla los equipos "
        "vinculados a este servidor privado.</p>"
        "<div class='kpis'>"
        f"<div class='kpi'><div class='num'>{connected}</div><div class='name'>En línea</div></div>"
        f"<div class='kpi'><div class='num'>{used}</div><div class='name'>Registrados</div></div>"
        f"<div class='kpi'><div class='num'>{seats}</div><div class='name'>Plazas utilizadas</div></div>"
        "</div><div class='panel'><div class='panel-head'><h2>Ordenadores</h2>"
        "<span class='muted small'>Vinculación desde la aplicación del equipo</span></div>" +
        devices_html +
        "</div><div class='panel'><div class='panel-head'><h2>Asistentes de IA autorizados</h2>"
        "</div>" + clients_html +
        "</div><div class='panel'><div class='panel-head'><h2>Actividad reciente</h2>"
        "<span class='muted small'>Solo metadatos · purga durante el uso</span></div>" +
        activity_html +
        "</div><div class='notice'>Para añadir un ordenador, abre Dorado Relay en ese equipo "
        "y aprueba el código de vinculación en tu navegador. No debes compartir contraseñas "
        "ni tokens con el asistente de IA. Las suscripciones y ampliaciones se gestionan "
        "manualmente durante el programa piloto.</div>"
    )
    return _page("Mis dispositivos", body, email=email, csrf=session.csrf)


@router.post("/portal/devices/revoke")
async def revoke_device(request: Request):
    from .server import revoke_paired_device
    session, redirect = _portal_or_login(request)
    if redirect:
        return redirect
    form = await request.form()
    if not csrf_ok(session, str(form.get("csrf", ""))):
        return Response("CSRF no válido", status_code=403, headers=_HEADERS)
    name = str(form.get("device_name", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        return Response("Equipo no válido", status_code=400, headers=_HEADERS)
    if not await revoke_paired_device(name):
        return Response("Equipo no encontrado", status_code=404, headers=_HEADERS)
    return RedirectResponse("/portal", status_code=303, headers=_HEADERS)


@router.post("/portal/oauth/revoke")
async def revoke_oauth(request: Request):
    from .server import revoke_oauth_client
    session, redirect = _portal_or_login(request)
    if redirect:
        return redirect
    form = await request.form()
    if not csrf_ok(session, str(form.get("csrf", ""))):
        return Response("CSRF no válido", status_code=403, headers=_HEADERS)
    client_id = str(form.get("client_id", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", client_id):
        return Response("Conexión no válida", status_code=400, headers=_HEADERS)
    if not revoke_oauth_client(client_id):
        return Response("Conexión no encontrada", status_code=404, headers=_HEADERS)
    return RedirectResponse("/portal", status_code=303, headers=_HEADERS)


@router.post("/portal/logout")
async def logout(request: Request):
    from .server import database
    session, redirect = _portal_or_login(request)
    if redirect:
        return redirect
    form = await request.form()
    if not csrf_ok(session, str(form.get("csrf", ""))):
        return Response("CSRF no válido", status_code=403, headers=_HEADERS)
    with database() as conn:
        conn.execute("DELETE FROM portal_sessions WHERE token_hash=?", (session.token_hash,))
    response = RedirectResponse("/portal/login", status_code=303, headers=_HEADERS)
    response.delete_cookie(COOKIE, path="/")
    return response


@router.get("/portal/security")
async def security_form(request: Request):
    session, redirect = _portal_or_login(request)
    if redirect:
        return redirect
    email = _owner_email() or "Cuenta"
    body = (
        "<a href='/portal'>Volver al panel</a>"
        "<section class='auth'><div class='panel'><h1>Cambiar contraseña</h1>"
        "<p class='lead'>Al cambiarla, todas las sesiones actuales se cerrarán por seguridad.</p>"
        "<form method='post' action='/portal/security'>"
        f"<input type='hidden' name='csrf' value='{session.csrf}'>"
        "<label for='current'>Contraseña actual</label>"
        "<input id='current' type='password' name='current' required autocomplete='current-password'>"
        "<label for='new'>Contraseña nueva (mínimo 12 caracteres)</label>"
        "<input id='new' type='password' name='new' required minlength='12' autocomplete='new-password'>"
        "<label for='repeat'>Repite la nueva contraseña</label>"
        "<input id='repeat' type='password' name='repeat' required minlength='12' autocomplete='new-password'>"
        "<button type='submit'>Actualizar contraseña</button></form></div></section>"
    )
    return _page("Seguridad", body, email=email, csrf=session.csrf)


@router.post("/portal/security")
async def change_password(request: Request):
    from .server import database
    session, redirect = _portal_or_login(request)
    if redirect:
        return redirect
    form = await request.form()
    if not csrf_ok(session, str(form.get("csrf", ""))):
        return Response("CSRF no válido", status_code=403, headers=_HEADERS)
    old = str(form.get("current", ""))
    new = str(form.get("new", ""))
    repeat = str(form.get("repeat", ""))
    if new != repeat:
        return Response("No coinciden las contraseñas", status_code=400, headers=_HEADERS)
    try:
        hashed = password_hash(new)
    except ValueError:
        return Response("La contraseña debe tener entre 12 y 1024 caracteres", status_code=400,
                        headers=_HEADERS)
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        owner = conn.execute("SELECT password_hash FROM portal_owner WHERE id=1").fetchone()
        if not owner or not password_matches(old, owner["password_hash"]):
            return Response("Contraseña actual incorrecta", status_code=403, headers=_HEADERS)
        conn.execute("UPDATE portal_owner SET password_hash=? WHERE id=1", (hashed,))
        conn.execute("DELETE FROM portal_sessions")
    response = RedirectResponse("/portal/login", status_code=303, headers=_HEADERS)
    response.delete_cookie(COOKIE, path="/")
    return response


def bootstrap_main() -> None:
    """Operator CLI for a single isolated relay; default is one-time invite."""
    from .server import db_path
    import argparse

    parser = argparse.ArgumentParser(description="Bootstrap one customer's private portal")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--invite", action="store_true",
                         help="Generate a one-time account activation code (default)")
    actions.add_argument("--create-owner", action="store_true",
                         help="Set owner's password locally (for private self-hosting)")
    args = parser.parse_args()
    path = db_path()
    if not path.is_file():
        raise SystemExit("Database missing. Start the relay first, then create its owner.")
    email = input("Owner email: ").strip()
    conn = sqlite3.connect(path)
    try:
        init_portal_db(conn)
        conn.commit()
        if args.create_owner:
            passwd = getpass("New owner password (12+ chars): ")
            confirm = getpass("Repeat password: ")
            if passwd != confirm:
                raise ValueError("Passwords do not match")
            create_owner(conn, email, passwd)
        else:
            code = issue_invite(conn, email)
        conn.commit()
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise SystemExit(str(exc)) from None
    finally:
        conn.close()
    if args.create_owner:
        print("Owner created. Sign in at /portal/login.")
    else:
        print("Invitation generated. Deliver the code privately to the intended owner.")
        print("Valid for 24 hours; reissuing an invite invalidates the previous code.")
        print("Activation path: /portal/activate")
        print("One-time code: " + code)


if __name__ == "__main__":
    bootstrap_main()
