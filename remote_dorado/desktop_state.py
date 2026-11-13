"""Persist non-secret desktop choices and use the OS credential vault for tokens."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib
import json
import os
import re
import tempfile
from typing import Protocol

from .agent import DEVICE_NAME_PATTERN, validate_relay_url

_MODES = {"own", "cloud"}
_PERMISSIONS = {"inspect", "files", "full"}
_EMAIL = re.compile(r"[^\s@]{1,100}@[^\s@]{1,190}\.[^\s@]{2,63}\Z")
SERVICE_NAME = "Dorado Relay Desktop"


class CredentialUnavailable(RuntimeError):
    pass


class SecretStore(Protocol):
    def set(self, identity: str, secret: str) -> None: ...
    def get(self, identity: str) -> str | None: ...
    def delete(self, identity: str) -> None: ...


class NativeSecretStore:
    """Use Windows Credential Manager / Linux Secret Service via keyring.

    Never fall back to plaintext file or a fake/in-memory keyring backend.
    """
    def _backend(self):
        try:
            import keyring
            from keyring.backends.fail import Keyring as FailKeyring
            from keyring.backends.null import Keyring as NullKeyring
            backend = keyring.get_keyring()
            if isinstance(backend, (FailKeyring, NullKeyring)) or backend.priority <= 0:
                raise CredentialUnavailable("No secure system credential store is available")
            # Require the native backend, not unencrypted or third-party keyrings.
            if os.name == "nt":
                from keyring.backends.Windows import WinVaultKeyring
                if not isinstance(backend, WinVaultKeyring):
                    raise CredentialUnavailable("Windows Credential Manager is unavailable")
            else:
                from keyring.backends.SecretService import Keyring as SecretServiceKeyring
                if not isinstance(backend, SecretServiceKeyring):
                    raise CredentialUnavailable(
                        "Unlock the Ubuntu keyring (Secret Service / GNOME Keyring) to remember your account"
                    )
            return keyring
        except ImportError as exc:
            raise CredentialUnavailable("Install the desktop keyring package to remember connections") from exc
        except CredentialUnavailable:
            raise
        except Exception as exc:
            raise CredentialUnavailable("Could not unlock the operating-system credential vault") from exc

    def set(self, identity: str, secret: str) -> None:
        if len(secret) < 32:
            raise ValueError("Refusing to store an invalid credential")
        try:
            self._backend().set_password(SERVICE_NAME, identity, secret)
        except CredentialUnavailable:
            raise
        except Exception as exc:
            raise CredentialUnavailable("Failed to save credential in the system vault") from exc

    def get(self, identity: str) -> str | None:
        try:
            return self._backend().get_password(SERVICE_NAME, identity)
        except CredentialUnavailable:
            raise
        except Exception as exc:
            raise CredentialUnavailable("Failed to read the system credential vault") from exc

    def delete(self, identity: str) -> None:
        try:
            backend = self._backend()
            if backend.get_password(SERVICE_NAME, identity):
                backend.delete_password(SERVICE_NAME, identity)
        except CredentialUnavailable:
            raise
        except Exception as exc:
            raise CredentialUnavailable("Failed to clear stored credential") from exc


def secret_name(kind: str, origin: str, device: str, email: str = "") -> str:
    if kind not in {"own-device", "cloud-device", "cloud-session"}:
        raise ValueError("Unsupported credential kind")
    # Avoid exposing account emails/relay addresses in credential labels.
    raw = "|".join((kind, origin.lower(), device, email.lower()))
    return "dorado/" + kind + "/" + hashlib.sha256(raw.encode()).hexdigest()


def desktop_config_path() -> Path:
    chosen = os.environ.get("DORADO_DESKTOP_CONFIG")
    if chosen:
        return Path(chosen).expanduser()
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return root / "dorado-relay" / "desktop.json"


@dataclass
class DesktopPreferences:
    last_mode: str = "own"
    own_relay: str = ""
    device_name: str = ""  # Last active device, retained for older preferences.
    own_device_name: str = ""
    cloud_device_name: str = ""
    permission: str = "inspect"
    cloud_email: str = ""
    cloud_relay: str = ""
    auto_connect: bool = True

    def validate(self) -> None:
        if self.last_mode not in _MODES:
            raise ValueError("Invalid saved desktop mode")
        if self.own_relay:
            validate_relay_url(self.own_relay)
        for name in (self.device_name, self.own_device_name, self.cloud_device_name):
            if name and not DEVICE_NAME_PATTERN.fullmatch(name):
                raise ValueError("Invalid saved device name")
        if self.permission not in _PERMISSIONS:
            raise ValueError("Invalid saved permission profile")
        if self.cloud_email and not _EMAIL.fullmatch(self.cloud_email):
            raise ValueError("Invalid saved cloud account")
        if self.cloud_relay:
            validate_relay_url(self.cloud_relay)
        if not isinstance(self.auto_connect, bool):
            raise ValueError("Invalid automatic connection option")


def load_preferences(path: Path | None = None) -> DesktopPreferences:
    target = path or desktop_config_path()
    if not target.exists():
        return DesktopPreferences()
    if target.is_symlink() or not target.is_file():
        raise ValueError("Unsafe desktop preferences path")
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid desktop preferences")
    # Ignore obsolete fields, but refuse unknown secret-looking fields.
    if any(any(s in k.lower() for s in ("password", "token", "secret", "credential"))
           for k in data):
        raise ValueError("Credentials may not be stored in desktop preferences")
    saved = DesktopPreferences(**{k: data[k] for k in vars(DesktopPreferences()) if k in data})
    saved.validate()
    return saved


def save_preferences(pref: DesktopPreferences, path: Path | None = None) -> Path:
    pref.validate()
    target = path or desktop_config_path()
    if target.is_symlink():
        raise ValueError("Unsafe desktop preferences path")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        target.parent.chmod(0o700)
    fd, temp = tempfile.mkstemp(prefix=".desktop-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(asdict(pref), stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            os.chmod(temp, 0o600)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return target
