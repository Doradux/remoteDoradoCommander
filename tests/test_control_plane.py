"""Offline tests for the isolated billing control-plane, with signed Stripe fixtures."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from remote_dorado import control_plane as cp


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_CONTROL_DB", str(tmp_path / "control.sqlite3"))
    monkeypatch.setenv("DORADO_STRIPE_WEBHOOK_SECRET", "whsec_" + "W" * 32)
    monkeypatch.setenv("DORADO_STRIPE_SECRET_KEY", "sk_test_" + "K" * 32)
    monkeypatch.setenv("DORADO_CONTROL_ADMIN_TOKEN", "A" * 48)
    monkeypatch.setenv("DORADO_STRIPE_BASE_PRICE_ID", "price_" + "B" * 24)
    monkeypatch.setenv("DORADO_STRIPE_EXTRA_PRICE_ID", "price_" + "X" * 24)
    monkeypatch.setenv("DORADO_STRIPE_LIVEMODE", "0")
    with TestClient(cp.app) as api:
        cp.register_customer(
            "alice", "Alice@Example.com", "alice.relay.example.com",
            "cus_" + "A" * 15, "sub_" + "S" * 15,
        )
        cp.register_customer(
            "bob", "bob@example.com", "bob.relay.example.com",
            "cus_" + "B" * 15, "sub_" + "T" * 15,
        )
        yield api


def fixture_subscription(*, status="active", extras=0, customer=None, sub=None,
                         invoice_status="paid", pending_update=None, unknown_price=False):
    prices = [{"price": {"id": "price_" + "B" * 24}, "quantity": 1}]
    if extras:
        prices.append({"price": {"id": "price_" + ("Z" if unknown_price else "X") * 24},
                       "quantity": extras})
    return {
        "id": sub or "sub_" + "S" * 15,
        "customer": customer or "cus_" + "A" * 15,
        "livemode": False,
        "status": status,
        "items": {"data": prices, "has_more": False},
        "latest_invoice": {
            "id": "in_" + "I" * 15, "status": invoice_status,
            "lines": {
                "data": (
                    [{"price": {"id": "price_" + "X" * 24}, "quantity": extras}]
                    if extras else []
                ),
                "has_more": False,
            },
        },
        "pending_update": pending_update,
    }


def event(event_id, event_type="customer.subscription.updated", *,
          subscription=None, customer=None, livemode=False):
    subscription = subscription or "sub_" + "S" * 15
    if event_type.startswith("invoice."):
        obj = {"id": "in_" + "I" * 15, "subscription": subscription,
               "customer": customer or "cus_" + "A" * 15}
    else:
        obj = {"id": subscription, "customer": customer or "cus_" + "A" * 15,
               "status": "past_due", "items": {"data": []}}
    return {
        "id": "evt_" + event_id, "type": event_type, "livemode": livemode,
        "created": int(time.time()),
        "data": {"object": obj},
    }


def signed(client, data, *, secret="whsec_" + "W" * 32, timestamp=None,
           change_signature=False):
    raw = json.dumps(data, separators=(",", ":")).encode()
    timestamp = int(time.time()) if timestamp is None else timestamp
    sig = hmac.new(secret.encode(), str(timestamp).encode() + b"." + raw,
                   hashlib.sha256).hexdigest()
    if change_signature:
        sig = "0" * 64
    return client.post(
        "/stripe/webhook",
        content=raw,
        headers={"stripe-signature": f"t={timestamp},v1={sig}",
                 "content-type": "application/json"},
    )


def test_signed_event_reconciles_fresh_stripe_not_untrusted_snapshot(client, monkeypatch):
    fetched = []
    async def fetch(subscription_id, key):
        fetched.append((subscription_id, key))
        return fixture_subscription(extras=3)
    monkeypatch.setattr(cp, "fetch_subscription", fetch)
    result = signed(client, event("one00001"))
    assert result.status_code == 200, result.text
    assert result.json()["result"] == "reconciled"
    assert fetched == [("sub_" + "S" * 15, "sk_test_" + "K" * 32)]
    authorized = client.get("/admin/customers", headers={"Authorization": "Bearer " + "A" * 48})
    assert authorized.status_code == 200
    people = {item["id"]: item for item in authorized.json()["customers"]}
    assert people["alice"]["desired_seats"] == 5
    assert people["alice"]["status"] == "active"
    assert people["bob"]["desired_seats"] == 0
    assert people["bob"]["status"] == "pending"
    assert "W" * 32 not in authorized.text
    assert client.get("/admin/customers").status_code == 403
    assert client.get("/admin/customers", headers={"Authorization": "Bearer " + "A" * 47}).status_code == 403

    # Replays must never grant seats or trigger another call to Stripe.
    assert signed(client, event("one00001")).json()["result"] == "duplicate"
    assert len(fetched) == 1
    with cp.database() as conn:
        assert conn.execute("SELECT count(*) FROM webhook_events").fetchone()[0] == 1


def test_out_of_order_snapshot_ignored_live_stripe_state_used(client, monkeypatch):
    status = {"extras": 5}
    async def fetch(sub_id, key):
        return fixture_subscription(extras=status["extras"])
    monkeypatch.setattr(cp, "fetch_subscription", fetch)
    # Events arrive in reverse chronological order, but state is fetched live.
    current = event("newer001")
    current["created"] = 200
    obsolete = event("older001")
    obsolete["created"] = 100
    assert signed(client, current).status_code == 200
    assert signed(client, obsolete).status_code == 200
    with cp.database() as conn:
        assert conn.execute("SELECT desired_seats FROM billing WHERE customer_id='alice'").fetchone()[0] == 7


def test_failed_payment_freezes_new_seat_entitlements(client, monkeypatch):
    async def fetch(sub_id, key):
        return fixture_subscription(status="past_due", extras=4, invoice_status="open")
    monkeypatch.setattr(cp, "fetch_subscription", fetch)
    result = signed(client, event("failed001", event_type="invoice.payment_failed"))
    assert result.status_code == 200
    with cp.database() as conn:
        assert conn.execute("SELECT desired_seats FROM billing WHERE customer_id='alice'").fetchone()[0] == 0
    # This still does not kill existing client machines; that policy is separate.


def test_invoice_paid_event_updates_quota_only_from_live_subscription(client, monkeypatch):
    async def fetch(sub_id, key):
        return fixture_subscription(extras=2)
    monkeypatch.setattr(cp, "fetch_subscription", fetch)
    result = signed(client, event("paid0001", event_type="invoice.paid"))
    assert result.status_code == 200
    with cp.database() as conn:
        assert conn.execute("SELECT desired_seats FROM billing WHERE customer_id='alice'").fetchone()[0] == 4


@pytest.mark.parametrize("changed", [
    {"unknown_price": True},
    {"invoice_status": "open"},
    {"pending_update": {"subscription_items": []}},
    {"extras": 99},
])
def test_bad_or_unpaid_subscription_cannot_increase_quota(client, monkeypatch, changed):
    async def fetch(sub_id, key):
        return fixture_subscription(extras=3, **changed)
    monkeypatch.setattr(cp, "fetch_subscription", fetch)
    result = signed(client, event("bad000001"))
    assert result.status_code == 503
    assert result.json()["error"] == "reconciliation_required"
    with cp.database() as conn:
        assert conn.execute("SELECT desired_seats FROM billing WHERE customer_id='alice'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM webhook_events").fetchone()[0] == 0


def test_reject_mismatched_stripe_customer(client, monkeypatch):
    async def fetch(sub_id, key):
        return fixture_subscription(customer="cus_" + "B" * 15)
    monkeypatch.setattr(cp, "fetch_subscription", fetch)
    result = signed(client, event("wrong001"))
    assert result.status_code == 503
    with cp.database() as conn:
        assert conn.execute("SELECT count(*) FROM webhook_events").fetchone()[0] == 0


def test_unmapped_customer_does_not_create_account(client, monkeypatch):
    async def no_fetch(sub_id, key):
        pytest.fail("Unknown subscription must never reach Stripe reconciliation")
    monkeypatch.setattr(cp, "fetch_subscription", no_fetch)
    result = signed(client, event("orphan01", subscription="sub_" + "Q" * 15))
    assert result.status_code == 200
    assert result.json()["result"] == "unmapped"
    with cp.database() as conn:
        assert conn.execute("SELECT count(*) FROM customers").fetchone()[0] == 2


def test_signature_timestamp_replay_and_mode_guards(client, monkeypatch):
    async def no_fetch(sub_id, key):
        pytest.fail("Invalid webhook must never fetch Stripe")
    monkeypatch.setattr(cp, "fetch_subscription", no_fetch)
    data = event("auth0001")
    assert signed(client, data, change_signature=True).status_code == 400
    assert signed(client, data, timestamp=int(time.time()) - 500).status_code == 400
    assert signed(client, data, timestamp=int(time.time()) + 500).status_code == 400
    assert signed(client, event("mode0001", livemode=True)).status_code == 503
    assert signed(client, data, secret="whsec_" + "Z" * 32).status_code == 400
    assert client.post("/stripe/webhook", content=b"x" * (cp.WEBHOOK_MAX_BYTES + 1),
                       headers={"stripe-signature": "irrelevant"}).status_code == 413


def test_invalid_registry_or_duplicate_subscription_rejected(client):
    with pytest.raises(ValueError):
        cp.register_customer("../secret", "test@example.com", "test.example.com",
                             "cus_" + "N" * 15, "sub_" + "N" * 15)
    with pytest.raises(Exception):
        cp.register_customer("another", "test@example.com", "another.example.com",
                             "cus_" + "A" * 15, "sub_" + "U" * 15)
    with cp.database() as conn:
        assert conn.execute("SELECT count(*) FROM customers").fetchone()[0] == 2


def test_signed_export_requires_recent_verified_customer_and_never_exposes_master(
    client, monkeypatch, tmp_path,
):
    from remote_dorado.entitlements import check_entitlement, derive_tenant_key
    from scripts.control_plane_admin import atomic_private_output
    monkeypatch.setenv("DORADO_ENTITLEMENT_MASTER_KEY", "4f" * 32)
    with pytest.raises(cp.BillingError, match="fresh_stripe"):
        cp.signed_entitlement_for_customer("alice")
    async def paid_fetch(sub_id, key):
        return fixture_subscription(extras=2)
    monkeypatch.setattr(cp, "fetch_subscription", paid_fetch)
    assert signed(client, event("export001")).status_code == 200
    envelope = cp.signed_entitlement_for_customer("alice")
    assert envelope["payload"]["seats"] == 4
    assert envelope["payload"]["status"] == "active"
    tenant_key = derive_tenant_key("4f" * 32, "alice")
    assert check_entitlement(envelope, "alice", tenant_key) == 4

    path = tmp_path / "entitlement.json"
    atomic_private_output(path, json.dumps(envelope))
    assert path.stat().st_mode & 0o777 == 0o644  # nonsecret, read by unprivileged Docker user
    assert "4f" * 32 not in path.read_text()
    with pytest.raises(FileExistsError):
        atomic_private_output(path, json.dumps(envelope))
    atomic_private_output(path, json.dumps(envelope), replace=True)
    assert check_entitlement(json.loads(path.read_text()), "alice", tenant_key) == 4

    with cp.database() as conn:
        conn.execute("UPDATE billing SET verified_at=? WHERE customer_id='alice'",
                     (int(time.time()) - 36 * 3600,))
    with pytest.raises(cp.BillingError, match="fresh_stripe"):
        cp.signed_entitlement_for_customer("alice")
    with pytest.raises(cp.BillingError, match="unknown_customer"):
        cp.signed_entitlement_for_customer("missing")


def test_canceled_subscription_with_removed_items_freezes_seats(client, monkeypatch):
    async def canceled_fetch(sub_id, key):
        fresh = fixture_subscription(status="canceled", invoice_status="open")
        fresh["items"] = {"data": [], "has_more": False}
        return fresh
    monkeypatch.setattr(cp, "fetch_subscription", canceled_fetch)
    assert signed(client, event("cancel001", event_type="customer.subscription.deleted")).status_code == 200
    with cp.database() as conn:
        row = conn.execute("SELECT desired_seats,status FROM billing WHERE customer_id='alice'").fetchone()
    assert row["desired_seats"] == 0
    assert row["status"] == "canceled"


def test_private_control_plane_db_permissions(client):
    from pathlib import Path
    path = Path(cp.settings()["database"])
    assert path.stat().st_mode & 0o077 == 0
    assert path.parent.stat().st_mode & 0o077 == 0



def test_extra_seats_require_proof_on_latest_paid_invoice(client, monkeypatch):
    async def no_proof(sub_id, key):
        fresh = fixture_subscription(extras=3)
        del fresh["latest_invoice"]["lines"]
        return fresh
    monkeypatch.setattr(cp, "fetch_subscription", no_proof)
    rejected = signed(client, event("noproof001"))
    assert rejected.status_code == 503
    with cp.database() as conn:
        assert conn.execute("SELECT desired_seats FROM billing WHERE customer_id='alice'").fetchone()[0] == 0


def test_modern_stripe_invoice_line_pricing_can_prove_extras(client, monkeypatch):
    async def modern_paid(sub_id, key):
        fresh = fixture_subscription(extras=2)
        fresh["latest_invoice"]["lines"]["data"] = [
            {"pricing": {"price_details": {"price": "price_" + "X" * 24}}, "quantity": 2}
        ]
        return fresh
    monkeypatch.setattr(cp, "fetch_subscription", modern_paid)
    assert signed(client, event("modern001")).status_code == 200
    with cp.database() as conn:
        assert conn.execute("SELECT desired_seats FROM billing WHERE customer_id='alice'").fetchone()[0] == 4

