"""Isolamento rigido: gettoni di capacita' per OGNI singola chiamata di strumento.

Il hook (policy.py) decide se l'agente puo' usare lo strumento e, se si', emette un gettone firmato (HMAC) legato ad agente, strumento e
argomenti esatti, valido pochi secondi. Il gestore dello strumento rifiuta qualunque chiamata senza gettone valido, o con un gettone
emesso per un altro agente, strumento o argomenti, o il cui agente non possiede la capacita' richiesta (scope).
Cosi' anche un bug nel prompt, nel routing o in una futura scorciatoia non basta a leggere dati di un altro ruolo: serve il gettone.
Il segreto di firma nasce a ogni avvio del processo e non esce mai da esso.
"""
from __future__ import annotations

import base64
import dataclasses
import functools
import hashlib
import hmac
import json
import os
import time
from copy import deepcopy

from . import config

_SECRET = os.urandom(32)
TTL_S = 60
GROUP_SCOPE = {"crm_read": "crm", "projects_read": "projects", "pricing": "pricing", "graph_read": "graph", "ops_read": "ops", "web_audit": "audit", "site_design": "sites", "site_build": "sites", "site_copy": "sites", "site_art": "sites", "system_read": "system", "system_improve": "sysops"}
SHARED_SERVERS = {"crm", "projects", "pricing", "graph", "ops", "audit", "sites", "system", "sysops"}
PER_AGENT = (("mem_", "memory"), ("draft_", "draft"), ("prop_", "prop"), ("learn_", "learn"), ("pow_", "pow"))
TOOL_SCOPE = {"money_summary": "finance"}      # incassi: capacita' separata dal resto del CRM


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


def args_hash(args: dict) -> str:
    clean = {k: v for k, v in (args or {}).items() if k != "_cap"}
    return hashlib.sha256(json.dumps(clean, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:20]


def scope_of(full_name: str) -> str | None:
    """Capacita' richiesta da uno strumento MCP (nome completo mcp__server__strumento)."""
    parts = full_name.split("__")
    if len(parts) != 3 or parts[0] != "mcp":
        return None
    _, server, tool = parts
    if tool in TOOL_SCOPE:
        return TOOL_SCOPE[tool]
    if tool == "shared_search":
        return "shared:read"
    if tool == "shared_write":
        return "shared:write"
    if server in SHARED_SERVERS:
        return server
    for prefix, kind in PER_AGENT:
        if server.startswith(prefix):
            return f"{kind}:{server[len(prefix):]}"
    return None


def agent_scopes(spec) -> set[str]:
    s = {GROUP_SCOPE[g] for g in spec.mcp if g in GROUP_SCOPE}
    if "crm_read" in spec.mcp:
        s.add("finance")
    me = spec.name.replace("-", "_")
    if "drafts" in spec.mcp:
        s.add(f"draft:{me}")
    if "proposals" in spec.mcp:
        s.add(f"prop:{me}")
    if "learning" in spec.mcp:
        s.add(f"learn:{me}")
    if "powers" in spec.mcp:
        s.add(f"pow:{me}")
    if spec.memory_private:
        s.add(f"memory:{me}")
    if spec.memory_shared in ("read", "write"):
        s.add("shared:read")
    if spec.memory_shared == "write":
        s.add("shared:write")
    return s


def mint(agent: str, tool: str, args: dict, ttl: int = TTL_S) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"a": agent, "t": tool, "h": args_hash(args), "e": time.time() + ttl}).encode()).decode()
    return body + "." + hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()[:32]


@functools.lru_cache(maxsize=1)
def _specs() -> dict:
    from . import registry
    return registry.load_all()


def verify(token: str | None, full_name: str, args: dict, specs: dict | None = None) -> tuple[bool, str]:
    if not token or "." not in token:
        return False, "manca il gettone di capacita'"
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()[:32]):
        return False, "gettone non valido"
    try:
        p = json.loads(base64.urlsafe_b64decode(body.encode()))
    except Exception:  # noqa: BLE001
        return False, "gettone illeggibile"
    if p["e"] < time.time():
        return False, "gettone scaduto"
    if p["t"] != full_name:
        return False, "gettone emesso per un altro strumento"
    if p["h"] != args_hash(args):
        return False, "gettone emesso per argomenti diversi"
    spec = (specs or _specs()).get(p["a"])
    need = scope_of(full_name)
    if spec is None or need is None or need not in agent_scopes(spec):
        return False, f"l'agente {p['a']} non possiede la capacita' '{need}'"
    return True, ""


def _wrap(tool, full_name: str):
    inner = tool.handler

    async def guarded(args):
        clean = {k: v for k, v in (args or {}).items() if k != "_cap"}
        if config.CAPS_MODE != "off":
            ok, why = verify((args or {}).get("_cap"), full_name, clean)
            if not ok:
                if config.CAPS_MODE == "enforce":
                    return _err(f"accesso negato a {full_name}: {why}")
                from .events import Events           # warn: si esegue, ma resta traccia per capire se i gettoni arrivano davvero
                Events().log("cap_missing", "", full_name, False, why)
        return await inner(clean)

    # `_cap` NON compare nello schema mostrato al modello (alla prova reale un modello ha provato a compilarlo): lo inietta il hook
    # con updatedInput e il gestore lo legge dagli argomenti (gli schemi non vietano proprieta' extra).
    return dataclasses.replace(tool, handler=guarded)


def harden(server: str, tools: list) -> list:
    """Avvolge ogni strumento di un server: senza gettone valido non esegue."""
    return [_wrap(t, f"mcp__{server}__{t.name}") for t in tools]
