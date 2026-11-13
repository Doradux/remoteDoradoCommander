"""HTTP contract used by the native desktop app for paid Cloud accounts.

No password is persisted. Credentials stay in the OS credential vault.
The provider must validate payment on every login and session revalidation.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import urlsplit

import httpx

from .agent import validate_relay_url
from .build_config import DEFAULT_CLOUD_API_URL


class CloudAccessError(RuntimeError):
    def __init__(self, reason: str, http_status: int = 0) -> None:
        self.reason = reason
        self.http_status = http_status
        super().__init__(reason)


def cloud_api_url() -> str:
    return (os.environ.get("DORADO_CLOUD_API_URL", "") or DEFAULT_CLOUD_API_URL).rstrip("/")


def validate_cloud_origin(value: str) -> str:
    validate_relay_url(value)
    return value.rstrip("/")


@dataclass(frozen=True)
class CloudIdentity:
    email: str
    relay_url: str
    device_name: str
    agent_token: str = ""
    session_token: str = ""


class CloudAPI:
    def __init__(self, url: str | None = None, *,
                 transport: httpx.BaseTransport | None = None) -> None:
        candidate = cloud_api_url() if url is None else url
        if not candidate:
            raise CloudAccessError("El servicio Dorado Cloud todavía no está configurado.")
        self.origin = validate_cloud_origin(candidate)
        self._transport = transport

    def _request(self, method: str, path: str, *,
                 token: str = "", json_body: dict | None = None) -> dict:
        headers = {"Cache-Control": "no-store"}
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            with httpx.Client(
                timeout=12, follow_redirects=False, trust_env=False,
                transport=self._transport,
            ) as client:
                result = client.request(
                    method, self.origin + path, json=json_body, headers=headers,
                )
        except httpx.HTTPError as exc:
            raise CloudAccessError("No se puede contactar con Dorado Cloud.") from exc
        if result.status_code in (401, 402, 403, 429, 503):
            try:
                issue = result.json().get("error", "access_denied")
            except ValueError:
                issue = "access_denied"
            reasons = {
                "invalid_credentials": "Correo o contraseña incorrectos.",
                "session_expired": "La sesión ha caducado. Vuelve a iniciar sesión.",
                "inactive_subscription": "No hay un plan de pago activo o su verificación está pendiente.",
                "too_many_attempts": "Demasiados intentos. Inténtalo más tarde.",
                "relay_unavailable": "Tu servidor Cloud no está disponible temporalmente.",
                "access_denied": "No se ha autorizado el acceso a la cuenta.",
            }
            raise CloudAccessError(reasons.get(issue, "El servidor no ha autorizado la conexión."), result.status_code)
        if result.status_code != 200:
            raise CloudAccessError("Respuesta inesperada de Dorado Cloud.", result.status_code)
        try:
            payload = result.json()
        except ValueError as exc:
            raise CloudAccessError("Respuesta inválida del servicio Cloud.") from exc
        if not isinstance(payload, dict):
            raise CloudAccessError("Respuesta inválida del servicio Cloud.")
        return payload

    @staticmethod
    def _identity(data: dict, *, expect_tokens: bool) -> CloudIdentity:
        if data.get("plan_active") is not True:
            raise CloudAccessError("Se requiere un plan de pago activo.")
        email = data.get("account_email")
        name = data.get("device_name")
        relay = data.get("relay_url")
        if not (isinstance(email, str) and "@" in email
                and isinstance(name, str) and 1 <= len(name) <= 64
                and isinstance(relay, str)):
            raise CloudAccessError("El servicio devolvió una identidad incompleta.")
        try:
            validate_relay_url(relay)
        except ValueError as exc:
            raise CloudAccessError("El servidor Cloud devolvió un relay no válido.") from exc
        agent = data.get("agent_token", "")
        session = data.get("session_token", "")
        if expect_tokens and not (
            isinstance(agent, str) and len(agent) >= 32
            and isinstance(session, str) and len(session) >= 32
        ):
            raise CloudAccessError("Faltan credenciales para mantener la sesión.")
        return CloudIdentity(email, relay, name, agent, session)

    def login(self, email: str, password: str, device_name: str) -> CloudIdentity:
        if not password or not isinstance(password, str):
            raise CloudAccessError("Introduce la contraseña de la cuenta.")
        data = self._request("POST", "/desktop/v1/login", json_body={
            "email": email.strip().lower(), "password": password,
            "device_name": device_name,
        })
        return self._identity(data, expect_tokens=True)

    def resume(self, token: str) -> CloudIdentity:
        if not isinstance(token, str) or len(token) < 32:
            raise CloudAccessError("La cuenta necesita iniciar sesión otra vez.")
        data = self._request("GET", "/desktop/v1/session", token=token)
        return self._identity(data, expect_tokens=False)

    def logout(self, token: str) -> bool:
        if token and len(token) >= 32:
            result = self._request("POST", "/desktop/v1/logout", token=token)
            return result.get("device_revoked") is True
        return False
