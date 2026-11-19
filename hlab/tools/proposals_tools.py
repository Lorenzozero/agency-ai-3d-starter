"""Proposte di modifica al CRM. L'agente PROPONE (con il motivo); non tocca il CRM. Lorenzo accetta o rifiuta
con `python -m hlab proposals` / `accept <id>`. Applicarle davvero e' un passo successivo, con strumento di scrittura dedicato."""
from __future__ import annotations

import re

from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

from ..events import Events
from ..registry import AgentSpec
from .base import S, err, js, ok, schema

NAMES = ["propose_change", "my_proposals"]
RISK = {"propose_change": "reversible", "my_proposals": "read"}   # reversible: scrive solo nel registro locale delle proposte
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
ALLOWED = {                                  # cosa si puo' proporre: tutto il resto e' fuori portata
    "project": {"status", "deadline", "payment_deadline", "is_online", "target_url", "staging_url"},
    "task": {"done", "due_date", "title"},
    "lead": {"status", "notes"},
    "user": {"is_active"},                   # attivare/disattivare un accesso: critico, parte solo dopo la conferma di Lorenzo in chat (entity_id: email o uuid)
}


def make_tools(spec: AgentSpec, events: Events | None = None) -> list:
    ev = events or Events()

    @tool("propose_change",
          "Proponi una modifica al CRM (non la applica). entity: project|task|lead|user; field tra quelli ammessi; indica SEMPRE il motivo. "
          "Es. entity=project, field=deadline, new_value=2026-12-01, reason='cliente ha chiesto lo spostamento'.",
          schema({"entity": S, "entity_id": S, "field": S, "new_value": S, "reason": S},
                 ["entity", "entity_id", "field", "new_value", "reason"]))
    async def propose_change(a):
        ent, fld = (a.get("entity") or "").strip().lower(), (a.get("field") or "").strip()
        if ent not in ALLOWED or fld not in ALLOWED[ent]:
            return err(f"non proponibile. Ammessi: " + "; ".join(f"{e}: {', '.join(sorted(f))}" for e, f in ALLOWED.items()))
        ident = (a.get("entity_id") or "").strip()
        if not (UUID_RE.match(ident) or (ent == "user" and EMAIL_RE.match(ident))):
            return err("entity_id deve essere un uuid (per gli utenti anche un'email)")
        if ent == "user" and str(a.get("new_value", "")).strip().lower() not in ("true", "false"):
            return err("per un utente new_value e' true o false")
        if len((a.get("reason") or "").strip()) < 10:
            return err("serve un motivo chiaro (almeno una frase)")
        pid = ev.add_proposal(spec.name, ent, a["entity_id"].strip(), fld, str(a.get("new_value", "")), a["reason"].strip())
        return ok(f"proposta #{pid} registrata: Lorenzo la vedra' e decidera'. Il CRM non e' stato modificato.")

    @tool("my_proposals", "Le tue proposte ancora in attesa di decisione.", schema({}), annotations=ToolAnnotations(readOnlyHint=True))
    async def my_proposals(a):
        return js([p for p in ev.proposals("pending") if p["agent"] == spec.name])

    return [propose_change, my_proposals]


def server_name(spec: AgentSpec) -> str:
    return "prop_" + spec.name.replace("-", "_")      # uno per agente: l'autore della proposta e' sempre chi la fa


def build(spec: AgentSpec):
    from .. import scopes
    return create_sdk_mcp_server(name=server_name(spec), version="1.0.0", tools=scopes.harden(server_name(spec), make_tools(spec)))
