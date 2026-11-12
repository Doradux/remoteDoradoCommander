"""Single-owner, self-service portal for ONE isolated Dorado relay.

Pilot scope: not a shared SaaS. The account is created by an operator using
the local bootstrap command; public signup is intentionally disabled.
"""
from __future__ import annotations

from dataclasses import dataclass
from getpass import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from .ui import ui_response, HEADERS as UI_HEADERS

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


_HEADERS = UI_HEADERS

def _owner_email() -> str | None:
    from .server import database
    with database() as conn:
        row = conn.execute("SELECT email FROM portal_owner WHERE id=1").fetchone()
        return row["email"] if row else None


def _login_page(next_path: str, error: str = "") -> HTMLResponse:
    return ui_response("login", next=safe_next(next_path), error=bool(error))


def _activation_page(error: bool = False) -> HTMLResponse:
    return ui_response("activate", error=bool(error))


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
    try:
        maximum = max_devices()
        billing_unavailable = False
    except RuntimeError:
        maximum = None
        billing_unavailable = True
    return ui_response(
        "dashboard",
        email=email,
        csrf=session.csrf,
        devices=[
            {"name": row["name"], "created": row["created"],
             "online": row["name"] in hub.devices}
            for row in devices
        ],
        clients=[
            {"id": row["id"], "redirectUris": json.loads(row["redirect_uris"])}
            for row in clients
        ],
        activity=[
            {"when": time.strftime("%d/%m/%Y %H:%M UTC", time.gmtime(row["created"])),
             "tool": row["tool"], "device": row["device"], "status": row["status"]}
            for row in activity
        ],
        maxDevices=(
            None if maximum == 0 and not os.environ.get("DORADO_ENTITLEMENT_FILE")
            else maximum
        ),
        billingUnavailable=billing_unavailable,
        billingSigned=bool(os.environ.get("DORADO_ENTITLEMENT_FILE")),
    )


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
    return ui_response(
        "security", email=_owner_email() or "Cuenta", csrf=session.csrf
    )


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
