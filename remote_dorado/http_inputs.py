"""Bounded, deterministic HTTP request decoders for public auth routes.

Reject oversized bodies and duplicate form fields before using credentials,
tokens or CSRF values. HTML forms and OAuth token requests use URL encoding.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl

from fastapi import Request, HTTPException


async def limited_bytes(request: Request, maximum: int) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared and declared.isdecimal() and int(declared) > maximum:
        raise OverflowError("request_too_large")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > maximum:
            raise OverflowError("request_too_large")
        content.extend(chunk)
    return bytes(content)


async def limited_json(request: Request, maximum: int = 16_384) -> Any:
    body = await limited_bytes(request, maximum)
    return json.loads(body)


async def limited_form(request: Request, maximum: int = 16_384) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise ValueError("expected_urlencoded_form")
    body = await limited_bytes(request, maximum)
    try:
        text = body.decode("utf-8", errors="strict")
        pairs = parse_qsl(
            text, keep_blank_values=True, encoding="utf-8",
            errors="strict", max_num_fields=24,
        )
    except (UnicodeError, ValueError) as exc:
        raise ValueError("invalid_form_encoding") from exc
    fields: dict[str, str] = {}
    for key, value in pairs:
        if not key or key in fields or len(key) > 80:
            raise ValueError("duplicate_or_invalid_field")
        fields[key] = value
    return fields


async def bounded_json(request: Request, maximum: int = 8_192) -> Any:
    try:
        return await limited_json(request, maximum)
    except OverflowError as exc:
        raise HTTPException(status_code=413, detail="request_too_large") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid_json") from exc


async def bounded_form(request: Request, maximum: int = 16_384) -> dict[str, str]:
    try:
        return await limited_form(request, maximum)
    except OverflowError as exc:
        raise HTTPException(status_code=413, detail="request_too_large") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid_form") from exc
