"""Operator-only admin for a single isolated Dorado relay.

No credentials in shell arguments, URL query strings, logs or config files.
Never use this against a shared multi-customer relay (not supported).
"""
from __future__ import annotations

import argparse
from getpass import getpass
import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def validate_url(url: str) -> str:
    parsed = urlsplit(url.rstrip("/"))
    if parsed.scheme != "https" or not parsed.hostname or parsed.path not in ("", "/"):
        if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1") or parsed.path not in ("", "/"):
            raise ValueError("Use a trusted HTTPS relay origin (HTTP only on localhost)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Do not include credentials or query data in the relay URL")
    return url.rstrip("/")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage paired devices on one isolated Dorado relay")
    parser.add_argument("action", choices=("list", "revoke"))
    parser.add_argument("device_name", nargs="?")
    parser.add_argument("--relay", default=os.environ.get("DORADO_PUBLIC_URL", ""))
    args = parser.parse_args()
    if args.action == "revoke" and not args.device_name:
        parser.error("revoke requires a device name")
    if not args.relay:
        parser.error("Supply --relay https://your-relay.example or DORADO_PUBLIC_URL")
    url = validate_url(args.relay)
    password = getpass("Relay administrator password: ")
    if not password:
        parser.error("Empty password")
    endpoint = "/admin/devices" if args.action == "list" else "/admin/devices/revoke"
    payload = json.dumps({"device_name": args.device_name}).encode() if args.action == "revoke" else None
    request = Request(url + endpoint, data=payload, method="POST" if payload else "GET", headers={
        "Authorization": "Bearer " + password,
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    try:
        with urlopen(request, timeout=15) as response:
            print(json.dumps(json.load(response), indent=2, ensure_ascii=False))
    except HTTPError as error:
        print(f"Request failed (HTTP {error.code}). Check authentication, device name and relay.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
