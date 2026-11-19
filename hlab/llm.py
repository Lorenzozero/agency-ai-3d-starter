"""Chiamata diretta e semplice a un modello (senza agenti), per i compiti a risposta singola come la patch del QA.
Usa lo stesso backend degli agenti: router locale (OpenRouter, pool a rotazione) oppure chiave Anthropic."""
from __future__ import annotations

import os

import httpx

from . import config


async def complete(prompt: str, system: str = "", model: str = "strong", max_tokens: int = 4000, timeout: float = 180, budget=None) -> str:
    """Se passi un `budget` (lean.Budget) i token reali della risposta vengono addebitati e il tetto e' rispettato."""
    from .router import load_config
    from .runner import backend, load_env_file
    if budget is not None:
        budget.check(len(system + prompt) // 4, max_tokens)            # si rifiuta PRIMA di spendere se non c'e' margine
        max_tokens = min(max_tokens, budget.remaining - len(system + prompt) // 4)
    load_env_file()
    kind = backend()
    body = {"max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    if system:
        body["system"] = system
    if kind == "router":
        url, headers, body["model"] = f"http://127.0.0.1:{load_config()['listen']['port']}/v1/messages", {"anthropic-version": "2023-06-01"}, f"hlab-{model}"
    else:
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"}
        body["model"] = {"strong": "claude-sonnet-5-5", "light": "claude-haiku-5-5"}.get(model, model)
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, json=body, headers=headers)
        r.raise_for_status()
    data = r.json()
    if budget is not None:
        u = data.get("usage") or {}
        budget.charge(int(u.get("input_tokens") or len(system + prompt) // 4), int(u.get("output_tokens") or max_tokens // 2))
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
