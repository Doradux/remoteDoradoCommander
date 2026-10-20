"""Embed the publicly reachable, NONSECRET relay origin into release binaries.

GitHub Actions calls this only at packaging time. Configure the repository
variable DORADO_DEFAULT_RELAY_URL; never embed private credentials.
"""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def write_default_relay() -> str:
    url = os.environ.get("DORADO_BUILD_RELAY_URL", "").strip().rstrip("/")
    if url:
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.netloc or parts.path or parts.query or
                parts.fragment or parts.username or parts.password):
            raise ValueError("DORADO_DEFAULT_RELAY_URL must be an HTTPS origin (no path or credentials)")
    target = Path(__file__).resolve().parents[1] / "remote_dorado" / "build_config.py"
    target.write_text('"""Release build configuration: public relay URL, no secrets."""\nDEFAULT_RELAY_URL = ' + json.dumps(url) + '\n')
    print("Compiled public relay origin:", url if url else "(not configured: first run will ask once)")
    return url


if __name__ == "__main__":
    write_default_relay()
