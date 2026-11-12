"""Offline signed seat entitlements between billing and ONE isolated relay.

No network calls from customer devices to the billing service. The master key
stays on the operator control plane; relays only receive a derived per-customer
key. All entitlement files must be delivered through a trusted operator channel.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import time
from typing import Any

CUSTOMER_ID = re.compile(r"[a-z][a-z0-9-]{2,39}\Z")
MAX_SEATS = 100
MAX_TTL = 48 * 3600
ALLOWED = {"active", "trialing", "past_due", "unpaid", "canceled",
           "incomplete", "incomplete_expired", "paused", "pending"}


class EntitlementError(ValueError):
    pass


def derive_tenant_key(master_hex: str, customer_id: str) -> bytes:
    if not isinstance(master_hex, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", master_hex):
        raise EntitlementError("DORADO_ENTITLEMENT_MASTER_KEY must be 32 random bytes as hex")
    if not isinstance(customer_id, str) or not CUSTOMER_ID.fullmatch(customer_id):
        raise EntitlementError("Invalid customer id")
    return hmac.new(
        bytes.fromhex(master_hex),
        b"dorado-relay:tenant-entitlements:v1:" + customer_id.encode(),
        hashlib.sha256,
    ).digest()


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


def create_entitlement(
    customer_id: str, seats: int, status: str, tenant_key: bytes,
    *, issued_at: int | None = None, ttl: int = MAX_TTL,
) -> dict[str, Any]:
    if not CUSTOMER_ID.fullmatch(customer_id):
        raise EntitlementError("Invalid customer id")
    if type(seats) is not int or not 0 <= seats <= MAX_SEATS:
        raise EntitlementError("Invalid seats")
    if status not in ALLOWED or (status not in ("active", "trialing") and seats != 0):
        raise EntitlementError("Invalid billing status/seats")
    if type(ttl) is not int or not 60 <= ttl <= MAX_TTL:
        raise EntitlementError("Invalid time-to-live")
    if len(tenant_key) != 32:
        raise EntitlementError("Tenant key must be 32 bytes")
    issued_at = int(time.time()) if issued_at is None else issued_at
    payload = {
        "version": 1,
        "customer_id": customer_id,
        "seats": seats,
        "status": status,
        "issued_at": issued_at,
        "expires_at": issued_at + ttl,
    }
    return {
        "payload": payload,
        "signature": hmac.new(tenant_key, _canonical(payload), hashlib.sha256).hexdigest(),
    }


def check_entitlement(
    envelope: dict[str, Any], customer_id: str, key: bytes, *,
    now: int | None = None,
) -> int:
    """Return 0 (meaning freeze NEW pairings) when signed status denies seats."""
    now = int(time.time()) if now is None else now
    if len(key) != 32 or not isinstance(envelope, dict):
        raise EntitlementError("Invalid signed entitlement")
    payload, signature = envelope.get("payload"), envelope.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, str):
        raise EntitlementError("Invalid signed entitlement")
    expected = hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(expected, signature):
        raise EntitlementError("Invalid signed entitlement signature")
    if (payload.get("version") != 1 or payload.get("customer_id") != customer_id
            or not CUSTOMER_ID.fullmatch(customer_id)):
        raise EntitlementError("Entitlement does not match this isolated relay")
    seats, status = payload.get("seats"), payload.get("status")
    issued, expires = payload.get("issued_at"), payload.get("expires_at")
    if type(seats) is not int or not 0 <= seats <= MAX_SEATS or status not in ALLOWED:
        raise EntitlementError("Invalid seat count or subscription status")
    if status not in ("active", "trialing") and seats != 0:
        raise EntitlementError("Unpaid seats are not allowed")
    if type(issued) is not int or type(expires) is not int:
        raise EntitlementError("Invalid entitlement timestamps")
    if issued > now + 300 or expires <= now or expires - issued > MAX_TTL or expires <= issued:
        raise EntitlementError("Expired or invalid entitlement")
    return seats


def configured_entitlement_file() -> bool:
    return bool(os.environ.get("DORADO_ENTITLEMENT_FILE"))


def effective_relay_seats() -> int:
    """Fail CLOSED when enabled and missing/invalid; no automatic fallback to free."""
    location = os.environ.get("DORADO_ENTITLEMENT_FILE", "")
    if not location:
        raise EntitlementError("Signed entitlements are not configured")
    if os.environ.get("DORADO_COMMERCIAL_MODE") != "1":
        raise EntitlementError("Entitlements require commercial single-customer mode")
    customer = os.environ.get("DORADO_ENTITLEMENT_CUSTOMER", "")
    secret = os.environ.get("DORADO_ENTITLEMENT_SECRET", "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", secret):
        raise EntitlementError("Invalid relay entitlement secret")
    file = Path(location)
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 4096:
        raise EntitlementError("Missing or oversized signed entitlement")
    try:
        envelope = json.loads(file.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise EntitlementError("Unable to read signed entitlement") from exc
    return check_entitlement(envelope, customer, bytes.fromhex(secret))
