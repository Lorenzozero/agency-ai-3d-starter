"""Strumenti dell'agente Ops/Uptime: stato dei siti dei clienti, in sola lettura. Un controllo al volo e' ammesso solo su siti gia' monitorati."""
from __future__ import annotations

from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

from .. import scopes, uptime
from .base import S, err, js, schema

NAMES = ["site_health", "check_site"]
RISK = {"site_health": "read", "check_site": "read"}          # check_site fa solo una GET su un indirizzo gia' noto: niente scritture, niente URL a scelta


def make_tools() -> list:
    ro = ToolAnnotations(readOnlyHint=True)

    @tool("site_health", "Stato dell'ultimo controllo di tutti i siti monitorati: online o giu', tempi di risposta, giorni al rinnovo del certificato.",
          schema({}), annotations=ro)
    async def site_health(a):
        st = uptime.load()
        return js({"riepilogo": uptime.summary(st), "siti": {n: {k: s.get(k) for k in ("ok", "down", "status", "ms", "ssl_days", "error")} for n, s in st.items()}})

    @tool("check_site", "Controlla adesso un sito GIA' monitorato (nome come in site_health): ritorna esito, tempo e certificato. Non accetta indirizzi nuovi.",
          schema({"name": S}, ["name"]), annotations=ro)
    async def check_site(a):
        st = uptime.load()
        name = (a.get("name") or "").strip()
        known = next((n for n in st if n.lower() == name.lower()), None)
        if not known:
            return err(f"'{name}' non e' monitorato. Siti noti: {', '.join(sorted(st)) or 'nessuno'}")
        return js({"sito": known, **await uptime.check(st[known]["url"])})

    return [site_health, check_site]


def build():
    return create_sdk_mcp_server(name="ops", version="1.0.0", tools=scopes.harden("ops", make_tools()))
