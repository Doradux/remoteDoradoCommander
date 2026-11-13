"""Managed cloud token broker for a pilot: account + verified paid subscription.

No Stripe key, operator token or tenant secret goes to a desktop binary.
The central service is separate from all customer MCP relay processes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from urllib.parse import urlsplit
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .portal import password_hash, password_matches
from .http_inputs import bounded_json
from .entitlements import derive_tenant_key

router = APIRouter()
_EMAIL = re.compile(r"[^\s@]{1,100}@[^\s@]{1,190}\.[^\s@]{2,63}\Z")
_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
SESSION_TTL = 30 * 24 * 3600
FRESH_BILLING_TTL = 24 * 3600
LOGIN_ATTEMPTS: dict[str, tuple[int, float]] = {}


def _body_error(error: str, status: int = 401) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def _valid_billing(db: Any, customer_id: str) -> bool:
    row = db.execute(
        "SELECT status,desired_seats,verified_at FROM billing WHERE customer_id=?",
        (customer_id,),
    ).fetchone()
    now = int(time.time())
    return bool(
        row and row["status"] == "active"
        and row["desired_seats"] >= 1
        and 0 < row["verified_at"] <= now + 60
        and now - row["verified_at"] <= FRESH_BILLING_TTL
    )


def register_cloud_account(customer_id: str, email: str, password: str) -> None:
    """Offline operator enrollment, not an internet-accessible sign-up route."""
    from .control_plane import database
    if not _EMAIL.fullmatch(email.lower()) or not 12 <= len(password) <= 256:
        raise ValueError("Invalid cloud email or password")
    with database() as conn:
        record = conn.execute(
            "SELECT email FROM customers WHERE id=?", (customer_id,),
        ).fetchone()
        if not record or record["email"].lower() != email.lower():
            raise ValueError("Account must match registered customer's email")
        conn.execute(
            "INSERT INTO desktop_accounts(customer_id,email,password_hash) VALUES (?,?,?) "
            "ON CONFLICT(customer_id) DO UPDATE SET "
            "email=excluded.email,password_hash=excluded.password_hash",
            (customer_id, email.lower(), password_hash(password)),
        )
        conn.execute("DELETE FROM desktop_sessions WHERE customer_id=?", (customer_id,))


def _tenant_hmac(customer_id: str, data: dict[str, Any]) -> str:
    master = os.environ.get("DORADO_ENTITLEMENT_MASTER_KEY", "")
    key = derive_tenant_key(master, customer_id)
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


async def provision_tenant_device(domain: str, customer_id: str,
                                  email: str, device_name: str) -> str:
    """Only provider-side signed provisioning; never expose this signing key."""
    packet = {
        "customer_id": customer_id, "email": email, "device_name": device_name,
        "issued_at": int(time.time()), "nonce": secrets.token_urlsafe(24),
    }
    signature = _tenant_hmac(customer_id, packet)
    url = "https://" + domain + "/internal/cloud/device"
    async with httpx.AsyncClient(timeout=12, follow_redirects=False, trust_env=False) as client:
        result = await client.post(
            url, json=packet, headers={"X-Dorado-Cloud-Signature": signature},
        )
    if result.status_code != 200:
        raise RuntimeError("Customer relay refused device connection")
    data = result.json()
    if not isinstance(data, dict) or data.get("device_name") != device_name:
        raise RuntimeError("Unexpected customer relay response")
    token = data.get("agent_token")
    if not isinstance(token, str) or len(token) < 32:
        raise RuntimeError("Customer relay returned no device credential")
    return token



async def revoke_tenant_device(domain: str, customer_id: str,
                               email: str, device_name: str) -> bool:
    """Revoke the tenant device when the Cloud session is signed out."""
    packet = {
        "customer_id": customer_id, "email": email, "device_name": device_name,
        "issued_at": int(time.time()), "nonce": secrets.token_urlsafe(24),
    }
    signature = _tenant_hmac(customer_id, packet)
    async with httpx.AsyncClient(timeout=12, follow_redirects=False, trust_env=False) as client:
        response = await client.post(
            "https://" + domain + "/internal/cloud/revoke", json=packet,
            headers={"X-Dorado-Cloud-Signature": signature},
        )
    return response.status_code == 200

def _rate_limited(identifier: str) -> bool:
    now = time.monotonic()
    # Bound transient memory; the public gateway must additionally use an
    # upstream rate limiter at the reverse proxy for multi-process deployments.
    if len(LOGIN_ATTEMPTS) > 20_000:
        LOGIN_ATTEMPTS.clear()
    count, until = LOGIN_ATTEMPTS.get(identifier, (0, now + 900))
    if now > until:
        count, until = 0, now + 900
    count += 1
    LOGIN_ATTEMPTS[identifier] = (count, until)
    return count > 5


@router.post("/desktop/v1/login")
async def desktop_login(request: Request):
    from .control_plane import database
    try:
        body = await bounded_json(request, 8_192)
    except Exception as exc:
        return _body_error("invalid_request", 400)
    if not isinstance(body, dict):
        return _body_error("invalid_request", 400)
    email = body.get("email", "")
    password = body.get("password", "")
    name = body.get("device_name", "")
    if (not isinstance(email, str) or not _EMAIL.fullmatch(email.lower())
            or not isinstance(password, str) or not 1 <= len(password) <= 256
            or not isinstance(name, str) or not _NAME.fullmatch(name)):
        return _body_error("invalid_request", 400)
    email = email.lower()
    if _rate_limited(email):
        return _body_error("too_many_attempts", 429)
    with database() as db:
        record = db.execute(
            "SELECT a.customer_id,a.password_hash,c.domain "
            "FROM desktop_accounts a JOIN customers c ON c.id=a.customer_id "
            "WHERE a.email=?", (email,),
        ).fetchone()
        is_valid = bool(record and password_matches(password, record["password_hash"]))
        if not is_valid:
            return _body_error("invalid_credentials")
        if not _valid_billing(db, record["customer_id"]):
            return _body_error("inactive_subscription", 402)
        customer_id, domain = record["customer_id"], record["domain"]
    try:
        agent_token = await provision_tenant_device(domain, customer_id, email, name)
    except (RuntimeError, ValueError, httpx.HTTPError):
        return _body_error("relay_unavailable", 503)
    session = secrets.token_urlsafe(48)
    now = int(time.time())
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        if not _valid_billing(db, customer_id):
            return _body_error("inactive_subscription", 402)
        db.execute(
            "DELETE FROM desktop_sessions WHERE expires<?", (now,),
        )
        db.execute(
            "INSERT INTO desktop_sessions(session_hash,customer_id,email,device_name,expires) "
            "VALUES (?,?,?,?,?)",
            (hashlib.sha256(session.encode()).hexdigest(), customer_id, email, name,
             now + SESSION_TTL),
        )
    LOGIN_ATTEMPTS.pop(email, None)
    return JSONResponse({
        "account_email": email, "relay_url": "https://" + domain,
        "device_name": name, "plan_active": True, "agent_token": agent_token,
        "session_token": session,
    }, headers={"Cache-Control": "no-store"})


def _auth_session(request: Request):
    from .control_plane import database
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer ") or not 32 <= len(header[7:]) <= 256:
        return None
    hashed = hashlib.sha256(header[7:].encode()).hexdigest()
    with database() as db:
        row = db.execute(
            "SELECT s.email,s.customer_id,s.device_name,s.expires,c.domain "
            "FROM desktop_sessions s JOIN customers c ON c.id=s.customer_id "
            "WHERE s.session_hash=?", (hashed,),
        ).fetchone()
        if not row or row["expires"] <= int(time.time()):
            return None
        return dict(row)


@router.get("/desktop/v1/session")
async def desktop_session(request: Request):
    from .control_plane import database
    session = _auth_session(request)
    if session is None:
        return _body_error("session_expired")
    with database() as db:
        if not _valid_billing(db, session["customer_id"]):
            return _body_error("inactive_subscription", 402)
    return JSONResponse({
        "account_email": session["email"],
        "relay_url": "https://" + session["domain"],
        "device_name": session["device_name"],
        "plan_active": True,
    }, headers={"Cache-Control": "no-store"})


@router.post("/desktop/v1/logout")
async def desktop_logout(request: Request):
    from .control_plane import database
    session = _auth_session(request)
    revoked = False
    if session is not None:
        try:
            revoked = await revoke_tenant_device(
                session["domain"], session["customer_id"],
                session["email"], session["device_name"],
            )
        except (RuntimeError, ValueError, httpx.HTTPError):
            pass
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer ") and 32 <= len(header[7:]) <= 256:
        hashed = hashlib.sha256(header[7:].encode()).hexdigest()
        with database() as db:
            db.execute("DELETE FROM desktop_sessions WHERE session_hash=?", (hashed,))
    return JSONResponse({"signed_out": True, "device_revoked": revoked},
                        headers={"Cache-Control": "no-store"})
