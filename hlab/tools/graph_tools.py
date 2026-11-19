"""Strumenti di sola lettura sul grafo: dall'entita' (cliente, servizio, paese...) ai collegamenti con prova."""
from __future__ import annotations

from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

from .. import scopes
from ..graph import Graph
from .base import I, S, err, js, schema

NAMES = ["graph_find", "graph_related"]
RISK = {"graph_find": "read", "graph_related": "read"}


def make_tools(graph: Graph | None = None) -> list:
    g = graph or Graph()
    ro = ToolAnnotations(readOnlyHint=True)

    @tool("graph_find", "Cerca nodi del grafo per nome (cliente, progetto, regola, servizio, paese, valuta, tema).", schema({"query": S}, ["query"]), annotations=ro)
    async def graph_find(a):
        return js([{"tipo": n["kind"], "nome": n["name"]} for n in g.find(a.get("query") or "")])

    @tool("graph_related", "Collegamenti di un'entita' fino a 3 passi, con il percorso e la prova (es. da un cliente USA alle regole fiscali USA). "
          "Usalo prima di leggere file interi per capire cosa e' collegato a cosa.", schema({"entity": S, "depth": I}, ["entity"]), annotations=ro)
    async def graph_related(a):
        r = g.related(a.get("entity") or "", int(a.get("depth") or 2))
        return js(r) if r else err("entita' non trovata o senza collegamenti: ricostruisci il grafo con `hlab graph build`")

    return [graph_find, graph_related]


def build():
    return create_sdk_mcp_server(name="graph", version="1.0.0", tools=scopes.harden("graph", make_tools()))
