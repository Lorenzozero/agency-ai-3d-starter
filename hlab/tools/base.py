"""Aiuti comuni agli strumenti in-process (server MCP dell'SDK)."""
from __future__ import annotations

import json
from typing import Any


def ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def js(obj: Any) -> dict[str, Any]:
    return ok(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def schema(props: dict[str, dict], required: list[str] | None = None) -> dict:
    """JSON Schema minimo: il dict semplice dell'SDK non permette parametri opzionali."""
    return {"type": "object", "properties": props, "required": required or []}


S = {"type": "string"}
I = {"type": "integer"}
N = {"type": "number"}
