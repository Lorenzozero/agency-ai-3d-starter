"""Strumenti commerciali. `pricing`: calcoli deterministici, senza stato, condivisi. `draft_<agente>`: salva bozze di preventivo
in locale (non partono verso nessuno); Lorenzo le approva con `python -m hlab drafts`."""
from __future__ import annotations

from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

from .. import commercial as co
from ..events import Events
from ..registry import AgentSpec
from .base import S, err, js, schema

PRICING_NAMES = ["price_list", "compute_quote", "check_quote"]
DRAFT_NAMES = ["save_quote_draft", "my_drafts"]
RISK = {"price_list": "read", "compute_quote": "read", "check_quote": "read", "save_quote_draft": "reversible", "my_drafts": "read"}

ITEMS = {"type": "array", "description": "voci del preventivo: {name, price_cents (centesimi), monthly (true se canone mensile)}",
         "items": {"type": "object", "properties": {"name": S, "price_cents": {"type": "integer"}, "monthly": {"type": "boolean"}},
                   "required": ["name", "price_cents"]}}


def _clean(items: list) -> list[dict]:
    return [{"name": str(i.get("name", "")).strip(), "price_cents": i.get("price_cents"), "monthly": bool(i.get("monthly"))} for i in items or []]


def pricing_tools() -> list:
    ro = ToolAnnotations(readOnlyHint=True)

    @tool("price_list", "Listino di partenza (centesimi) e voci suggerite per tipo di progetto. Le manutenzioni sono mensili.",
          schema({}), annotations=ro)
    async def price_list(a):
        return js({t: {"etichetta": co.TYPE_LABELS[t], "prezzo_partenza_cents": co.BASE_PRICE_CENTS[t],
                       "voci_suggerite": co.SUGGESTED_PARTS[t]} for t in co.PROJECT_TYPES})

    @tool("compute_quote", "Calcola totale una tantum, canone, acconto 50%, saldo e bolli. USA SEMPRE questo, mai calcoli a mano.",
          schema({"items": ITEMS, "currency": S}, ["items"]), annotations=ro)
    async def compute_quote(a):
        items, cur = _clean(a.get("items")), (a.get("currency") or "EUR").upper()
        bad = co.check_quote("altro", cur, items)["errors"]
        if bad:
            return err("; ".join(bad))
        q = co.compute_quote(items)
        q["currency"], q["language"] = cur, co.document_language(cur)
        q["notice"] = co.FLAT_RATE_NOTICE[q["language"]]
        q["testo"] = {"una_tantum": co.money(q["one_off_cents"], cur), "canone": co.money(q["monthly_cents"], cur),
                      "tranche": [f"{t['tranche']}: {co.money(t['total_cents'], cur)} (compenso {co.money(t['net_cents'], cur)}"
                                  f"{' + bollo ' + co.money(t['stamp_duty_cents'], cur) if t['stamp_duty_cents'] else ''})" for t in q["tranches"]]}
        return js(q)

    @tool("check_quote", "Controlla un preventivo contro il listino e le regole (valuta, listino minimo, dollari). Restituisce errori e avvertenze.",
          schema({"project_type": S, "currency": S, "items": ITEMS}, ["project_type", "items"]), annotations=ro)
    async def check_quote(a):
        return js(co.check_quote((a.get("project_type") or "").lower(), (a.get("currency") or "EUR").upper(), _clean(a.get("items"))))

    return [price_list, compute_quote, check_quote]


def draft_tools(spec: AgentSpec, events: Events | None = None) -> list:
    ev = events or Events()

    @tool("save_quote_draft",
          "Salva una BOZZA di preventivo (resta in locale, non parte verso il cliente). Ricalcola e controlla da sola. "
          "client: nome del cliente o lead; project_id opzionale (uuid) se il progetto esiste.",
          schema({"client": S, "project_type": S, "currency": S, "items": ITEMS, "notes": S, "project_id": S},
                 ["client", "project_type", "items"]))
    async def save_quote_draft(a):
        pt, cur, items = (a.get("project_type") or "").lower(), (a.get("currency") or "EUR").upper(), _clean(a.get("items"))
        chk = co.check_quote(pt, cur, items)
        if chk["errors"]:
            return err("bozza non salvata: " + "; ".join(chk["errors"]))
        if not (a.get("client") or "").strip():
            return err("indica il cliente")
        q = co.compute_quote(items)
        did = ev.add_draft(spec.name, "preventivo", a["client"].strip(), (a.get("project_id") or None), cur,
                           {"project_type": pt, "items": items, "quote": q, "warnings": chk["warnings"]}, a.get("notes") or "")
        return js({"bozza": did, "stato": "in attesa di Lorenzo (non inviata)", "totale_una_tantum": co.money(q["one_off_cents"], cur),
                   "avvertenze": chk["warnings"]})

    @tool("my_drafts", "Le tue bozze di preventivo ancora in attesa di decisione.", schema({}), annotations=ToolAnnotations(readOnlyHint=True))
    async def my_drafts(a):
        return js([{"id": d["id"], "client": d["client"], "currency": d["currency"], "una_tantum_cents": d["payload"]["quote"]["one_off_cents"]}
                   for d in ev.drafts("draft", spec.name)])

    return [save_quote_draft, my_drafts]


def draft_server_name(spec: AgentSpec) -> str:
    return "draft_" + spec.name.replace("-", "_")


def build_pricing():
    from .. import scopes
    return create_sdk_mcp_server(name="pricing", version="1.0.0", tools=scopes.harden("pricing", pricing_tools()))


def build_drafts(spec: AgentSpec):
    from .. import scopes
    return create_sdk_mcp_server(name=draft_server_name(spec), version="1.0.0", tools=scopes.harden(draft_server_name(spec), draft_tools(spec)))
