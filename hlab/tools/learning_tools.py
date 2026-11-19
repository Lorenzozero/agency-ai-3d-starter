"""Strumenti del Memory Keeper per il ciclo di apprendimento. Propone regole; attivarle spetta a Lorenzo."""
from __future__ import annotations

from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

from ..events import Events
from ..learning import Learning
from ..registry import AgentSpec
from .base import I, S, err, js, schema

NAMES = ["feedback_batch", "propose_rule", "rules_for", "mark_processed"]
RISK = {"feedback_batch": "read", "rules_for": "read", "propose_rule": "reversible", "mark_processed": "reversible"}


def server_name(spec: AgentSpec) -> str:
    return "learn_" + spec.name.replace("-", "_")


def make_tools(spec: AgentSpec, events: Events | None = None, learning: Learning | None = None) -> list:
    ev = events or Events()
    lr = learning or Learning(ev)
    ro = ToolAnnotations(readOnlyHint=True)

    @tool("feedback_batch", "Feedback non ancora elaborati (rifiuti, correzioni, approvazioni con commento), opzionalmente di un solo agente.",
          schema({"agent": S, "limit": I}), annotations=ro)
    async def feedback_batch(a):
        rows = ev.feedback_rows((a.get("agent") or None), limit=min(int(a.get("limit") or 30), 60))
        return js([{"id": r["id"], "agente": r["agent"], "fonte": f"{r['source']}#{r['source_id']}", "verdetto": r["verdict"],
                    "commento": r["comment"]} for r in rows])

    @tool("propose_rule",
          "Proponi UNA regola comportamentale per un agente, nata da feedback reali (evidence = id dei feedback). Formula in italiano, imperativa, "
          "generale (senza dati di un cliente), verificabile, massimo due frasi. Non attiva nulla: la approva Lorenzo.",
          schema({"agent": S, "rule": S, "evidence": {"type": "array", "items": I}}, ["agent", "rule", "evidence"]))
    async def propose_rule(a):
        try:
            r = lr.propose((a.get("agent") or "").strip(), a.get("rule") or "", list(a.get("evidence") or []))
        except ValueError as e:
            return err(str(e))
        return js({"regola": r["id"], "stato": "proposta, in attesa dell'approvazione di Lorenzo"})

    @tool("rules_for", "Regole di un agente (attive e proposte), per non duplicarle.", schema({"agent": S}, ["agent"]), annotations=ro)
    async def rules_for(a):
        return js([{"id": r["id"], "stato": r["status"], "regola": r["rule"]} for r in ev.rule_rows(a["agent"]) if r["status"] in ("active", "proposed")])

    @tool("mark_processed", "Segna come elaborati i feedback letti (anche quelli da cui non nasce nessuna regola).",
          schema({"ids": {"type": "array", "items": I}}, ["ids"]))
    async def mark_processed(a):
        return js({"segnati": ev.mark_feedback([int(i) for i in a.get("ids") or []])})

    return [feedback_batch, propose_rule, rules_for, mark_processed]


def build(spec: AgentSpec):
    from .. import scopes
    return create_sdk_mcp_server(name=server_name(spec), version="1.0.0", tools=scopes.harden(server_name(spec), make_tools(spec)))
