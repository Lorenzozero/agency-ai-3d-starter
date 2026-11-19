"""Regole applicate dal codice (hook PreToolUse), non dai prompt: un'email ostile non le puo' aggirare.

1. Un agente usa solo gli strumenti del suo elenco, anche se il modello ne conosce altri.
2. Mai cancellazioni vere: qualunque strumento il cui nome suggerisce delete/drop/truncate e' negato.
3. Autonomia: 0 solo lettura; 1 le azioni reversibili aspettano l'ok; 2 le reversibili passano da sole.
   Le azioni verso l'esterno chiedono SEMPRE l'ok (livello 0: negate).
4. L'ok vale per una sola esecuzione identica (stessi argomenti) ed e' registrato.
"""
from __future__ import annotations

import re

from . import scopes, tools
from .events import Events
from .registry import AgentSpec

HARD_DENY = re.compile(r"(delete|drop|truncate|destroy|purge|wipe)", re.I)


def decide(spec: AgentSpec | None, tool: str, args: dict, events: Events, *, request: bool = True) -> tuple[str, str]:
    """-> (allow|deny, motivo). Con 'deny' per approvazione il motivo contiene l'id da approvare."""
    who = spec.name if spec else "?"
    if spec is None:
        return "deny", "agente sconosciuto"
    risk = tools.risk_of(tool)
    if risk == "delegate":
        return "allow", ""
    if tool not in tools.tool_names(spec):
        return "deny", f"lo strumento {tool} non e' tra quelli concessi a {who}"
    if HARD_DENY.search(tool.split("__")[-1]):
        return "deny", "le cancellazioni vere sono vietate: usa la cancellazione logica"
    if risk == "read":
        return "allow", ""
    if spec.autonomy == 0:
        return "deny", f"{who} e' in sola osservazione (autonomia 0)"
    if risk == "reversible" and spec.autonomy >= 2:
        return "allow", ""
    if events.consume_approval(who, tool, args):
        return "allow", "approvato"
    if not request:
        return "deny", "serve approvazione"
    aid = events.request_approval(who, tool, args, risk)
    return "deny", f"serve l'approvazione di Lorenzo (richiesta #{aid}): riferiscilo nel tuo rapporto e prosegui con il resto"


def pre_tool_hook(specs: dict[str, AgentSpec], events: Events, main: str = "coo"):
    async def hook(input_data, tool_use_id, context):
        if input_data.get("hook_event_name") != "PreToolUse":
            return {}
        agent = input_data.get("agent_type") or main      # vuoto = sessione principale (COO)
        tool, args = input_data.get("tool_name", ""), input_data.get("tool_input") or {}
        verdict, why = decide(specs.get(agent), tool, args, events)
        events.log("tool_allow" if verdict == "allow" else "tool_deny", agent, tool, verdict == "allow", why)
        if verdict == "allow":
            if tool.startswith("mcp__"):     # gettone di capacita' legato ad agente, strumento e argomenti esatti (scopes.py)
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                               "updatedInput": {**args, "_cap": scopes.mint(agent, tool, args)}}}
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": why}}
    return hook


def post_tool_hook(events: Events, main: str = "coo"):
    async def hook(input_data, tool_use_id, context):
        events.log("tool_done", input_data.get("agent_type") or main, input_data.get("tool_name", ""), True)
        return {}
    return hook
