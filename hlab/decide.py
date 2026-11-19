"""Decisioni prese con un tocco in chat (Telegram). Il bot controlla che il tocco arrivi dalla chat autorizzata e chiama `hlab decide`.

hp = proposta di modifica al CRM (lead, progetti, utenti): ok -> accetta e applica, no -> rifiuta.
hc = modifica di codice (PR, anteprima): ok -> livello 1 (PR o anteprima), no -> rifiuta. Merge e pubblicazione non passano MAI da qui.
Un secondo tocco sulla stessa decisione non fa nulla. Il risultato e' una frase per Lorenzo.
"""
from __future__ import annotations

import httpx

from . import crm_write, powers
from .events import Events
from .learning import Learning
from .vault import Vault

WHY_OK = "confermato da Lorenzo con il pulsante in chat"
WHY_NO = "annullato da Lorenzo con il pulsante in chat"


def decide(kind: str, verdict: str, ident: int, ev: Events | None = None, vault: Vault | None = None, transport: httpx.BaseTransport | None = None) -> str:
    ev = ev or Events()
    if kind not in ("hp", "hc") or verdict not in ("ok", "no"):
        return "Comando non valido."
    return (_proposal if kind == "hp" else _change)(verdict == "ok", int(ident), ev, vault, transport)


def _proposal(ok: bool, pid: int, ev: Events, vault, transport) -> str:
    p = ev.get_proposal(pid)
    if not p:
        return f"Proposta #{pid} non trovata."
    if p["status"] != "pending":
        return f"La proposta #{pid} era già «{p['status']}»: nessuna azione."
    ev.decide_proposal(pid, ok)
    Learning(ev).record(p["agent"], "proposal", pid, "accepted" if ok else "rejected", WHY_OK if ok else WHY_NO)
    if not ok:
        return f"❌ Proposta #{pid} annullata."
    try:
        res = crm_write.apply(ev, pid, vault=vault, transport=transport)
    except Exception as e:                                           # accettata ma non eseguita: resta recuperabile con `hlab apply`
        return f"⚠️ Proposta #{pid} confermata ma NON applicata: {type(e).__name__}: {str(e)[:160]}. Riprova con `hlab apply {pid}`."
    return f"✅ Fatto: proposta #{pid} applicata ({res['campo']}, {res['api']})."


def _change(ok: bool, cid: int, ev: Events, vault, transport) -> str:
    ch = powers.get(ev, cid)
    if not ch:
        return f"Modifica #{cid} non trovata."
    if ch["status"] != "proposed":
        return f"La modifica #{cid} era già «{ch['status']}»: nessuna azione."
    if not ok:
        powers.reject(ev, cid, WHY_NO)
        return f"❌ Modifica #{cid} rifiutata."
    try:
        res = powers.Powers(ev, vault, transport=transport).approve(cid)              # publish=False: solo livello 1
    except Exception as e:
        return f"⚠️ Modifica #{cid} NON eseguita: {type(e).__name__}: {str(e)[:160]}."
    if ch["kind"] == "site_publish":
        if res.get("pages") == "built":
            return f"🚀 Sito online: {res.get('anteprima')}"
        return (f"⏳ Modifiche inviate a GitHub, ma GitHub Pages non ha ancora finito (stato: {res.get('pages')}). Il sito non e' ancora aggiornato: {res.get('anteprima')}. "
                "Controllo e ritento con `hlab` o dal bot (/stato).")
    return f"✅ Eseguita: {res.get('pr') or res.get('anteprima') or res}. Merge e pubblicazione restano a te."
