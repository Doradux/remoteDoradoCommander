"""Private billing control plane for isolated Dorado Relay pilots.

Separate process/database from customer relays. Not a public checkout and
never connected to a residential MCP relay. A Stripe-signed event triggers
a fresh server-side subscription fetch; event payloads never decide seats.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager, contextmanager
import hashlib
import hmac
import json
import os
import stat
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
import uvicorn


class BillingError(Exception):
    pass


CUSTOMER_ID = re.compile(r"[a-z][a-z0-9-]{2,39}\Z")
STRIPE_CUSTOMER = re.compile(r"cus_[A-Za-z0-9]{5,100}\Z")
STRIPE_SUBSCRIPTION = re.compile(r"sub_[A-Za-z0-9]{5,100}\Z")
STRIPE_PRICE = re.compile(r"price_[A-Za-z0-9]{5,100}\Z")
ALLOWED_TYPES = {
    "customer.subscription.created", "customer.subscription.updated",
    "customer.subscription.deleted", "invoice.paid", "invoice.payment_failed",
}
ALLOWED_STATES = {"active", "trialing", "past_due", "unpaid", "canceled",
                  "incomplete", "incomplete_expired", "paused"}
PAYMENT_ACTIVE = {"active", "trialing"}
WEBHOOK_MAX_BYTES = 256_000
SIGNATURE_WINDOW = 300
RECONCILE_LOCK = asyncio.Lock()


def settings() -> dict[str, Any]:
    config = {
        "database": os.environ.get("DORADO_CONTROL_DB", "").strip(),
        "webhook_secret": os.environ.get("DORADO_STRIPE_WEBHOOK_SECRET", ""),
        "stripe_key": os.environ.get("DORADO_STRIPE_SECRET_KEY", ""),
        "admin": os.environ.get("DORADO_CONTROL_ADMIN_TOKEN", ""),
        "base_price": os.environ.get("DORADO_STRIPE_BASE_PRICE_ID", ""),
        "extra_price": os.environ.get("DORADO_STRIPE_EXTRA_PRICE_ID", ""),
        "livemode": os.environ.get("DORADO_STRIPE_LIVEMODE", "0"),
    }
    if not config["database"]:
        raise RuntimeError("DORADO_CONTROL_DB must be configured separately from relay DBs")
    if Path(config["database"]).is_dir():
        raise RuntimeError("Control plane DB must point to a file")
    if len(config["webhook_secret"]) < 24 or not config["webhook_secret"].startswith("whsec_"):
        raise RuntimeError("A Stripe webhook signing secret is required")
    if len(config["stripe_key"]) < 20 or not (
        config["stripe_key"].startswith("sk_test_") or config["stripe_key"].startswith("sk_live_")
    ):
        raise RuntimeError("A server-side Stripe secret API key is required")
    if len(config["admin"]) < 32:
        raise RuntimeError("DORADO_CONTROL_ADMIN_TOKEN must be 32+ characters")
    if not STRIPE_PRICE.fullmatch(config["base_price"]):
        raise RuntimeError("Invalid base price ID")
    if not STRIPE_PRICE.fullmatch(config["extra_price"]) or config["extra_price"] == config["base_price"]:
        raise RuntimeError("Distinct Stripe price IDs are required")
    if config["livemode"] not in ("0", "1"):
        raise RuntimeError("DORADO_STRIPE_LIVEMODE must be 0 or 1")
    if config["livemode"] == "1" and not config["stripe_key"].startswith("sk_live_"):
        raise RuntimeError("Test Stripe key cannot run in live mode")
    if config["livemode"] == "0" and not config["stripe_key"].startswith("sk_test_"):
        raise RuntimeError("Live Stripe key cannot run in test mode")
    return config


@contextmanager
def database():
    path = Path(os.environ.get("DORADO_CONTROL_DB", ""))
    if not str(path) or str(path) == ".":
        raise RuntimeError("Missing DORADO_CONTROL_DB")
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    config = settings()
    path = Path(config["database"])
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError("Billing database and directory must not be symlinks")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
        raise RuntimeError("Billing data directory must be private (0700)")
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Invalid billing database path")
    os.chmod(path, 0o600)
    with database() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            domain TEXT NOT NULL UNIQUE,
            stripe_customer_id TEXT NOT NULL UNIQUE,
            stripe_subscription_id TEXT NOT NULL UNIQUE,
            created INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS billing (
            customer_id TEXT PRIMARY KEY REFERENCES customers(id),
            status TEXT NOT NULL DEFAULT 'pending',
            desired_seats INTEGER NOT NULL DEFAULT 0,
            verified_at INTEGER NOT NULL DEFAULT 0,
            invoice_id TEXT,
            last_event_id TEXT,
            CHECK (desired_seats BETWEEN 0 AND 100)
        );
        CREATE TABLE IF NOT EXISTS webhook_events (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            received INTEGER NOT NULL,
            customer_id TEXT,
            FOREIGN KEY(customer_id) REFERENCES customers(id)
        );
        CREATE TABLE IF NOT EXISTS desktop_accounts (
            customer_id TEXT PRIMARY KEY REFERENCES customers(id),
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS desktop_sessions (
            session_hash TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL REFERENCES customers(id),
            email TEXT NOT NULL,
            device_name TEXT NOT NULL,
            expires INTEGER NOT NULL
        );
        """)


def register_customer(
    customer_id: str, email: str, domain: str,
    stripe_customer_id: str, stripe_subscription_id: str,
) -> None:
    """Only call from an authenticated offline operator action."""
    if not CUSTOMER_ID.fullmatch(customer_id):
        raise ValueError("Invalid customer id")
    if not re.fullmatch(r"[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}", email):
        raise ValueError("Invalid email")
    if not re.fullmatch(r"(?:[a-z0-9][a-z0-9-]{0,62}\.)+[a-z]{2,63}", domain):
        raise ValueError("Invalid domain")
    if not STRIPE_CUSTOMER.fullmatch(stripe_customer_id):
        raise ValueError("Invalid Stripe customer ID")
    if not STRIPE_SUBSCRIPTION.fullmatch(stripe_subscription_id):
        raise ValueError("Invalid Stripe subscription ID")
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO customers VALUES (?,?,?,?,?,?)",
            (customer_id, email.lower(), domain.lower(),
             stripe_customer_id, stripe_subscription_id, int(time.time())),
        )
        conn.execute(
            "INSERT INTO billing (customer_id) VALUES (?)", (customer_id,),
        )


def verify_stripe_signature(payload: bytes, header: str, secret: str, now: int | None = None) -> None:
    """Strict Stripe v1 HMAC check over the unmodified request bytes.

    Reject old/future timestamps and require a valid v1 HMAC. Signature
    verification is not deduplication: webhook_events enforces that separately.
    """
    if len(payload) > WEBHOOK_MAX_BYTES:
        raise BillingError("oversized")
    if not header or len(header) > 4096:
        raise BillingError("invalid_signature")
    parts = []
    for part in header.split(","):
        if "=" not in part:
            continue
        key, value = part.strip().split("=", 1)
        parts.append((key, value))
    timestamps = [value for key, value in parts if key == "t"]
    signatures = [value for key, value in parts if key == "v1"]
    if len(timestamps) != 1 or not signatures or not timestamps[0].isdigit():
        raise BillingError("invalid_signature")
    timestamp = int(timestamps[0])
    if abs((int(time.time()) if now is None else now) - timestamp) > SIGNATURE_WINDOW:
        raise BillingError("expired_signature")
    expected = hmac.new(
        secret.encode("utf-8"),
        str(timestamp).encode("ascii") + b"." + payload,
        hashlib.sha256,
    ).hexdigest()
    if not any(len(sig) == 64 and hmac.compare_digest(expected, sig) for sig in signatures):
        raise BillingError("invalid_signature")


async def limited_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > WEBHOOK_MAX_BYTES:
            raise BillingError("oversized")
        body.extend(chunk)
    return bytes(body)


async def fetch_subscription(subscription_id: str, api_key: str) -> dict[str, Any]:
    """Fetch live Stripe state, never provision from an event snapshot."""
    if not STRIPE_SUBSCRIPTION.fullmatch(subscription_id):
        raise BillingError("invalid_subscription")
    path = quote(subscription_id, safe="")
    async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
        response = await client.get(
            "https://api.stripe.com/v1/subscriptions/" + path,
            params={"expand[]": "latest_invoice"},
            headers={"Authorization": "Bearer " + api_key},
        )
        response.raise_for_status()
        result = response.json()
    if not isinstance(result, dict):
        raise BillingError("unexpected_stripe_response")
    return result


def desired_entitlement(subscription: dict[str, Any], config: dict[str, Any]) -> tuple[str, int, str | None]:
    """Two seats base + priced extras, only from a paid Stripe state.

    No unrecognized prices or partially paid upgrades can increase seats.
    If Stripe is still applying pending updates, retain previous entitlement.
    """
    status = subscription.get("status")
    if status not in ALLOWED_STATES:
        raise BillingError("unknown_subscription_status")
    # Nonpaying/cancelled subscriptions cannot add any seats, even if Stripe
    # omits their former subscription-item list after cancellation.
    if status not in PAYMENT_ACTIVE:
        invoice = subscription.get("latest_invoice")
        invoice_id = invoice.get("id") if isinstance(invoice, dict) else None
        return status, 0, invoice_id
    items = subscription.get("items")
    if not isinstance(items, dict) or not isinstance(items.get("data"), list):
        raise BillingError("incomplete_items")
    if items.get("has_more"):
        raise BillingError("incomplete_items")
    if subscription.get("pending_update") is not None:
        raise BillingError("pending_payment_update")
    seen_base, extra = 0, 0
    for item in items["data"]:
        if not isinstance(item, dict) or not isinstance(item.get("price"), dict):
            raise BillingError("invalid_subscription_item")
        price = item["price"].get("id")
        quantity = item.get("quantity")
        if type(quantity) is not int or quantity < 0:
            raise BillingError("invalid_item_quantity")
        if price == config["base_price"]:
            if quantity != 1:
                raise BillingError("invalid_base_quantity")
            seen_base += 1
        elif price == config["extra_price"]:
            extra += quantity
        else:
            raise BillingError("unexpected_stripe_price")
    if seen_base != 1 or extra > 98:
        raise BillingError("invalid_plan_or_seat_count")
    invoice = subscription.get("latest_invoice")
    if status == "active":
        # Fail closed on unpaid or missing invoices, including pending prorations.
        if not isinstance(invoice, dict) or invoice.get("status") != "paid":
            raise BillingError("latest_invoice_not_paid")
    if status == "active" and extra:
        # An older paid base invoice is NOT evidence that a new add-on quantity
        # was paid. For pilot safety, fail closed unless the latest paid invoice
        # actually includes this add-on price and at least its current quantity.
        # Some prorations create partial lines; those need manual reconciliation.
        lines = invoice.get("lines")
        if not isinstance(lines, dict) or lines.get("has_more") or not isinstance(lines.get("data"), list):
            raise BillingError("extra_device_invoice_proof_missing")
        covered = False
        for line in lines["data"]:
            if not isinstance(line, dict):
                continue
            legacy = line.get("price")
            modern = line.get("pricing")
            price = legacy.get("id") if isinstance(legacy, dict) else None
            if not price and isinstance(modern, dict):
                details = modern.get("price_details")
                if isinstance(details, dict):
                    price = details.get("price")
            quantity = line.get("quantity")
            if (price == config["extra_price"] and type(quantity) is int
                    and quantity >= extra):
                covered = True
                break
        if not covered:
            raise BillingError("extra_device_invoice_proof_missing")
    invoice_id = invoice.get("id") if isinstance(invoice, dict) else None
    seats = 2 + extra if status == "active" else 2 if status == "trialing" else 0
    return status, seats, invoice_id


def signed_entitlement_for_customer(customer_id: str) -> dict[str, Any]:
    """Operator-only export after RECENT verified Stripe reconciliation.

    The relay gets only a per-customer derived key, never the master key or
    Stripe credentials. No service or quota change occurs until operator
    safely delivers the resulting file to the correct isolated relay.
    """
    from .entitlements import derive_tenant_key, create_entitlement
    if not CUSTOMER_ID.fullmatch(customer_id):
        raise BillingError("invalid_customer")
    master = os.environ.get("DORADO_ENTITLEMENT_MASTER_KEY", "")
    try:
        key = derive_tenant_key(master, customer_id)
    except ValueError as exc:
        raise BillingError("invalid_entitlement_master_key") from exc
    with database() as conn:
        record = conn.execute(
            "SELECT b.status, b.desired_seats, b.verified_at "
            "FROM customers c JOIN billing b ON c.id=b.customer_id WHERE c.id=?",
            (customer_id,),
        ).fetchone()
    if not record:
        raise BillingError("unknown_customer")
    now = int(time.time())
    if (record["verified_at"] <= 0 or record["verified_at"] > now + 300
            or now - record["verified_at"] > 24 * 3600):
        raise BillingError("subscription_needs_fresh_stripe_reconciliation")
    try:
        return create_entitlement(
            customer_id, record["desired_seats"], record["status"], key,
        )
    except ValueError as exc:
        raise BillingError("invalid_stored_entitlement") from exc


def event_subscription_id(event: dict[str, Any]) -> str | None:
    event_type = event.get("type")
    data = event.get("data")
    obj = data.get("object") if isinstance(data, dict) else None
    if not isinstance(obj, dict):
        raise BillingError("missing_event_object")
    if isinstance(event_type, str) and event_type.startswith("customer.subscription."):
        identifier = obj.get("id")
    elif isinstance(event_type, str) and event_type.startswith("invoice."):
        identifier = obj.get("subscription")
        if not identifier:
            parent = obj.get("parent")
            details = parent.get("subscription_details") if isinstance(parent, dict) else None
            identifier = details.get("subscription") if isinstance(details, dict) else None
    else:
        return None
    if identifier is None:
        return None
    if not isinstance(identifier, str) or not STRIPE_SUBSCRIPTION.fullmatch(identifier):
        raise BillingError("invalid_event_subscription")
    return identifier


async def process_event(event: dict[str, Any], config: dict[str, Any]) -> str:
    identifier = event.get("id")
    if not isinstance(identifier, str) or not re.fullmatch(r"evt_[A-Za-z0-9]{5,100}", identifier):
        raise BillingError("invalid_event_id")
    event_type = event.get("type")
    if not isinstance(event_type, str):
        raise BillingError("invalid_event_type")
    if type(event.get("livemode")) is not bool or event["livemode"] != (config["livemode"] == "1"):
        raise BillingError("wrong_stripe_mode")
    if event_type not in ALLOWED_TYPES:
        return "ignored"
    subscription_id = event_subscription_id(event)
    if not subscription_id:
        return "ignored"

    # Single-process pilot: serialize webhooks and refresh the CURRENT Stripe
    # subscription, avoiding changes based on stale unordered event snapshots.
    async with RECONCILE_LOCK:
        with database() as conn:
            already = conn.execute("SELECT 1 FROM webhook_events WHERE id=?", (identifier,)).fetchone()
            mapped = conn.execute(
                "SELECT * FROM customers WHERE stripe_subscription_id=?",
                (subscription_id,),
            ).fetchone()
        if already:
            return "duplicate"
        if not mapped:
            # Unknown subscriptions cannot grant any seats or create customers.
            return "unmapped"
        fresh = await fetch_subscription(subscription_id, config["stripe_key"])
        if (fresh.get("id") != subscription_id or
                fresh.get("customer") != mapped["stripe_customer_id"] or
                fresh.get("livemode") != (config["livemode"] == "1")):
            raise BillingError("stripe_account_mismatch")
        status, seats, invoice_id = desired_entitlement(fresh, config)
        with database() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM webhook_events WHERE id=?", (identifier,)).fetchone():
                return "duplicate"
            conn.execute(
                "UPDATE billing SET status=?, desired_seats=?, verified_at=?, invoice_id=?, last_event_id=? "
                "WHERE customer_id=?",
                (status, seats, int(time.time()), invoice_id, identifier, mapped["id"]),
            )
            # Stripe retries only briefly; the signature window prevents
            # a captured ancient delivery from being accepted again.
            conn.execute("DELETE FROM webhook_events WHERE received<?",
                         (int(time.time()) - 90 * 86400,))
            conn.execute(
                "INSERT INTO webhook_events VALUES (?,?,?,?)",
                (identifier, event_type, int(time.time()), mapped["id"]),
            )
    return "reconciled"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Dorado Billing Control Plane (private pilot)",
    lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None,
)
from .cloud_desktop_auth import router as cloud_desktop_router
app.include_router(cloud_desktop_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    try:
        config = settings()
        raw = await limited_body(request)
        verify_stripe_signature(
            raw, request.headers.get("stripe-signature", ""),
            config["webhook_secret"],
        )
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise BillingError("invalid_event")
        result = await process_event(event, config)
    except BillingError as exc:
        if str(exc) == "oversized":
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        if str(exc) in ("invalid_signature", "expired_signature"):
            return JSONResponse({"error": "invalid_signature"}, status_code=400)
        # Alert operator and let Stripe retry unresolved reconciliation.
        return JSONResponse({"error": "reconciliation_required"}, status_code=503)
    except (httpx.HTTPError, json.JSONDecodeError, TypeError, ValueError):
        return JSONResponse({"error": "reconciliation_required"}, status_code=503)
    return {"result": result}


def admin_authenticated(request: Request) -> bool:
    candidate = request.headers.get("authorization", "")
    return candidate.startswith("Bearer ") and secrets.compare_digest(
        candidate[7:], os.environ.get("DORADO_CONTROL_ADMIN_TOKEN", "")
    )


@app.get("/admin/customers")
async def admin_customers(request: Request):
    if not admin_authenticated(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    with database() as conn:
        rows = conn.execute(
            "SELECT c.id, c.email, c.domain, b.status, b.desired_seats, "
            "b.verified_at, b.invoice_id FROM customers c JOIN billing b "
            "ON c.id=b.customer_id ORDER BY c.id"
        ).fetchall()
    return JSONResponse({"customers": [dict(row) for row in rows]},
                        headers={"Cache-Control": "no-store"})


def main() -> None:
    uvicorn.run(
        "remote_dorado.control_plane:app",
        host=os.environ.get("DORADO_CONTROL_BIND_HOST", "127.0.0.1"),
        port=int(os.environ.get("DORADO_CONTROL_PORT", "8790")),
        proxy_headers=False,
        workers=1,
    )


if __name__ == "__main__":
    main()
