"""Operator-only CLI for the separate Dorado billing control plane.

This CLI never sends payments, creates Stripe subscriptions, starts servers,
or changes a live relay. It stages registry rows and signed seat JSON for
a human-controlled rollout. Do not put secret keys in CLI arguments.
"""
from __future__ import annotations

import argparse
from getpass import getpass
import json
import os
from pathlib import Path
import tempfile
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from remote_dorado import control_plane as control


def atomic_private_output(path: Path, content: str, *, replace: bool = False) -> None:
    if path.is_symlink() or (path.exists() and not replace):
        raise FileExistsError("Refusing to replace an existing file without --replace")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise ValueError("Output directory must already exist and not be a symlink")
    fd, temp_name = tempfile.mkstemp(prefix=".dorado-entitlement-", dir=path.parent)
    try:
        # Only the signed JSON (NOT the secret key) is published. 0644 is
        # needed for read-only, unprivileged Docker directory mounts.
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(json.loads(content), stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True)
    register = command.add_parser("register", help="Register an already verified Stripe customer/subscription")
    register.add_argument("--customer-id", required=True)
    register.add_argument("--email", required=True)
    register.add_argument("--domain", required=True)
    register.add_argument("--stripe-customer-id", required=True)
    register.add_argument("--stripe-subscription-id", required=True)
    account = command.add_parser("set-account", help="Set private Cloud login password for a verified customer")
    account.add_argument("--customer-id", required=True)
    account.add_argument("--email", required=True)
    command.add_parser("list", help="List customer and reconciliation status (operator only)")
    export = command.add_parser("export-seats", help="Sign seats for ONE isolated relay; never deploy")
    export.add_argument("--customer-id", required=True)
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    # Requires all Stripe/DB secrets in the operator's secure environment.
    control.init_db()
    if args.command == "register":
        control.register_customer(
            args.customer_id, args.email, args.domain,
            args.stripe_customer_id, args.stripe_subscription_id,
        )
        print("Customer registered in private control-plane database. No server deployed.")
    elif args.command == "set-account":
        from remote_dorado.cloud_desktop_auth import register_cloud_account
        first = getpass("New Cloud account password (12+ characters): ")
        second = getpass("Repeat password: ")
        if first != second:
            parser.error("Passwords do not match")
        register_cloud_account(args.customer_id, args.email, first)
        print("Cloud desktop account updated; old desktop sessions revoked.")
    elif args.command == "list":
        with control.database() as conn:
            rows = conn.execute(
                "SELECT c.id,c.email,c.domain,b.status,b.desired_seats,b.verified_at "
                "FROM customers c JOIN billing b ON b.customer_id=c.id ORDER BY c.id"
            ).fetchall()
        for row in rows:
            print(f"{row['id']} | {row['email']} | {row['domain']} | "
                  f"{row['status']} | seats={row['desired_seats']} | "
                  f"verified={row['verified_at']}")
    elif args.command == "export-seats":
        document = control.signed_entitlement_for_customer(args.customer_id)
        atomic_private_output(args.output, json.dumps(document), replace=args.replace)
        print(f"Private signed seat manifest generated: {args.output}")
        print("This does NOT deploy or modify customer instances.")



if __name__ == "__main__":
    main()
