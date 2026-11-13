"""One-time HMAC authorization from cloud account broker to isolated relay."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any


def cloud_request_signature(tenant_key: bytes, packet: dict[str, Any]) -> str:
    if not isinstance(tenant_key, bytes) or len(tenant_key) != 32:
        raise ValueError("Expected a 32-byte per-tenant signing key")
    data = json.dumps(
        packet, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    return hmac.new(tenant_key, data, hashlib.sha256).hexdigest()


def verify_cloud_request(tenant_key: bytes, packet: Any,
                         signature: str, customer_id: str,
                         *, now: int | None = None) -> tuple[str, str]:
    if not isinstance(packet, dict) or set(packet) != {
        "customer_id", "email", "device_name", "issued_at", "nonce",
    }:
        raise ValueError("Invalid provider request")
    if packet["customer_id"] != customer_id:
        raise ValueError("Cloud request targets another customer")
    if (type(packet["issued_at"]) is not int
            or abs((int(time.time()) if now is None else now) - packet["issued_at"]) > 60):
        raise ValueError("Expired provider request")
    if not isinstance(packet["nonce"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{24,64}", packet["nonce"]):
        raise ValueError("Invalid request nonce")
    name = packet["device_name"]
    email = packet["email"]
    if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)
            or not isinstance(email, str)
            or not re.fullmatch(r"[^\s@]{1,100}@[^\s@]{1,190}\.[^\s@]{2,63}", email)):
        raise ValueError("Invalid account/device")
    if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", signature):
        raise ValueError("Missing cloud signature")
    if not hmac.compare_digest(
        cloud_request_signature(tenant_key, packet), signature.lower(),
    ):
        raise ValueError("Untrusted cloud provisioning request")
    return name, email.lower()
