"""Chat della console con gli agenti: chi scrive nella GUI riceve una risposta vera, in italiano.

La GUI scrive in `agentChats/<agente|grupo>/mensajes` un messaggio {from: "humano", status: "pending"}. Qui lo si prende, lo si risolve con il percorso
leggero (`lean`: regole e strumenti sul CRM, quasi sempre 0 token) e si scrive la risposta {from: "agente", agentId, agentName, text}.
Nella chat di squadra (`grupo`) risponde il COO, a meno che il messaggio citi un agente con @Nome.
Un solo messaggio alla volta per chat; un errore diventa una risposta chiara (mai un silenzio): la GUI non resta con i tre puntini per sempre.
"""
from __future__ import annotations

import asyncio
import re
import time

from . import registry
from .docstore import Store
from .events import Events

POLL_S = 3
MAX_ANSWER = 3500
STALE_S = 180                                                       # un messaggio rimasto "in lavorazione" per tanto tempo si riprende (riavvio a meta')


def agent_names() -> dict[str, str]:
    """id -> nome mostrato nella GUI (lo stesso di api_sync.agents_docs)."""
    return {n: n.replace("-", " ").title().replace("Coo", "COO").replace("Crm", "CRM") for n in registry.load_all()}


MAX_HOPS = 2                                                        # nel gruppo un agente puo' citarne un altro: al massimo 2 passaggi di parola
MAX_SPEAKERS = 3                                                    # e al massimo 3 agenti per passaggio


def mentioned(text: str, names: dict[str, str], exclude=()) -> list[str]:
    """Gli agenti citati con @id, @Nome o @NomeSenzaSpazi, nell'ordine in cui compaiono, senza ripetizioni."""
    out: list[str] = []
    for m in re.finditer(r"@([\w-]+)", text):
        tag = m.group(1).lower()
        for aid, nm in names.items():
            if aid in exclude or aid in out:
                continue
            if tag in (aid.lower(), nm.lower().replace(" ", ""), nm.lower().split(" ")[0]):
                out.append(aid)
                break
    return out


def target_agent(chat_id: str, text: str, names: dict[str, str]) -> str:
    if chat_id != "grupo":
        return chat_id
    return (mentioned(text, names) or ["coo"])[0]


def pending(store: Store, chat_ids: list[str]) -> list[tuple[str, dict]]:
    """Messaggi da risolvere, i piu' vecchi per primi: (chat, documento)."""
    now = time.time() * 1000
    out = []
    for cid in chat_ids:
        for r in store.query(f"agentChats/{cid}/mensajes", where=[["from", "==", "humano"]], order=[["createdAt", "asc"]]):
            d = r["data"]
            if d.get("status") == "pending" or (d.get("status") == "working" and now - (d.get("workStart") or 0) > STALE_S * 1000):
                out.append((cid, {"id": r["id"], **d}))
    return out


HISTORY_N = 16                                                      # quanti messaggi precedenti ricorda un agente in chat


def history_for(store: Store, cid: str, current_id: str, names: dict[str, str], n: int = HISTORY_N) -> list[tuple[str, str]]:
    """Gli ultimi `n` messaggi della chat, dal piu' vecchio: [(chi, testo)]. Esclude quello a cui si sta rispondendo (e' gia' la domanda) e i messaggi tuoi ancora in attesa."""
    rows = store.query(f"agentChats/{cid}/mensajes", order=[["createdAt", "desc"]], limit=n + 6)
    out = []
    for r in reversed(rows):
        d = r["data"]
        if r["id"] == current_id or not (d.get("text") or "").strip() or (d.get("from") == "humano" and d.get("status") == "pending"):
            continue
        who = (d.get("agentName") or names.get(d.get("agentId"), "Agente")) if d.get("from") == "agente" else (d.get("authorName") or "Lorenzo")
        out.append((who, d["text"]))
    return out[-n:]


def persona_for(who: str, names: dict[str, str], group: bool = False) -> str:
    """Chi risponde: nome e specialita' dell'agente (dal suo file), cosi' parla come lui e non come un assistente qualunque."""
    spec = registry.load_all().get(who)
    role = f" ({spec.description.split('.')[0][:120]})" if spec else ""
    p = f"Sei {names.get(who, who)}, agente di Agency AI{role}. Parli con Lorenzo, il titolare."
    if group:
        others = ", ".join(nm for aid, nm in names.items() if aid != who)
        p += (f" Siete in una chat di squadra con Lorenzo e i colleghi ({others}). Se ti serve un collega citalo con @Nome, "
              "una volta sola e solo se serve davvero; altrimenti rispondi tu.")
    return p


async def answer_full(text: str, lean=None, history: list | None = None, persona: str | None = None) -> tuple[str, str]:
    """(risposta, tipo). Il tipo e' l'intento riconosciuto dal percorso leggero ("status", "leads"... oppure "chat" = risposta libera) o "error"."""
    from .lean import Lean

    def work():                                                      # in un thread a parte: le chiamate al CRM bloccano e non devono fermare il tempo reale
        extra = {k: v for k, v in (("history", history), ("persona", persona)) if v}
        return asyncio.run(asyncio.wait_for((lean or Lean()).ask(text, **extra), timeout=90))
    kind = "error"
    try:
        r = await asyncio.to_thread(work)
        reply, kind = (r.text or "").strip() or "Non ho trovato nulla da dire su questo.", getattr(r, "intent", "") or "status"
    except asyncio.TimeoutError:
        reply = "Ci sto mettendo troppo: riprova tra poco o scrivimi la richiesta in modo piu' breve."
    except Exception as e:                                           # CRM non raggiungibile, modelli gratuiti esauriti, ecc.
        reply = f"Non sono riuscito a rispondere ora ({type(e).__name__}). Riprova tra un minuto; se continua, controlla lo stato dei servizi."
    return reply[:MAX_ANSWER], kind


async def answer(text: str, lean=None, history: list | None = None, persona: str | None = None) -> str:
    return (await answer_full(text, lean, history, persona))[0]


async def handle(store: Store, cid: str, msg: dict, names: dict[str, str], lean=None) -> None:
    path = f"agentChats/{cid}/mensajes/{msg['id']}"
    store.update(path, {"status": "working", "workStart": int(time.time() * 1000)})
    from . import briefing
    if briefing.BRIEF_RE.match(msg.get("text", "")):                 # "briefing": la squadra si riunisce e il COO riassume (vedi briefing.py)
        await briefing.run(store, lean, chat_id=cid)
        store.update(path, {"status": "done"})
        return
    from . import taskrun
    reply = briefing.handle_learning(store, msg.get("text", ""), cid) or taskrun.handle_engine(store, msg.get("text", ""))   # apprendimento ed esecuzione completa: comandi di chat
    if reply is not None:
        store.add(f"agentChats/{cid}/mensajes", {"from": "agente", "agentId": "memory-keeper", "agentName": "Memory Keeper", "replyTo": msg["id"],
                                                 "text": reply, "createdAt": {"__op": "serverTimestamp"}})
        store.update(path, {"status": "done"})
        return
    text = msg.get("text", "")
    if cid != "grupo":
        await reply_as(store, cid, cid, text, msg["id"], names, lean)
    else:
        await group_round(store, cid, msg, names, lean)
    store.update(path, {"status": "done"})


async def reply_as(store: Store, cid: str, who: str, text: str, reply_to: str, names: dict[str, str], lean=None, group: bool = False, quiet: bool = False) -> str:
    """Un agente risponde nella chat: ricorda gli ultimi messaggi, parla come se stesso e l'ufficio lo vede al lavoro. Ritorna cio' che ha scritto ("" se tace)."""
    if (store.get(f"agents/{who}") or {}).get("active") is False:        # "Metti in riserva" dalla GUI: l'agente non risponde
        if not quiet:
            store.add(f"agentChats/{cid}/mensajes", {"from": "agente", "agentId": who, "agentName": names.get(who, who), "replyTo": reply_to,
                                                     "text": f"{names.get(who, who)} è in riserva. Riattivalo da Agenti per usarlo.", "createdAt": {"__op": "serverTimestamp"}})
        return ""
    Events().log("chat", who, "chat", detail=text[:80])               # l'ufficio vede l'agente al lavoro mentre risponde
    reply = await answer(text, lean, history_for(store, cid, "" if quiet else reply_to, names), persona_for(who, names, group))
    store.add(f"agentChats/{cid}/mensajes", {"from": "agente", "agentId": who, "agentName": names.get(who, who), "text": reply,
                                             "replyTo": reply_to, "createdAt": {"__op": "serverTimestamp"}})
    return reply


async def group_round(store: Store, cid: str, msg: dict, names: dict[str, str], lean=None) -> None:
    """Chat di squadra: rispondono gli agenti citati (il COO se nessuno); se uno di loro cita un collega, questo risponde al passaggio dopo.
    Limiti contro i giri a vuoto: 2 passaggi, 3 agenti a passaggio, e un agente parla una volta sola per messaggio."""
    spoken: set[str] = set()
    frontier = [(a, msg.get("text", "")) for a in (mentioned(msg.get("text", ""), names) or ["coo"])[:MAX_SPEAKERS]]
    for hop in range(MAX_HOPS):
        nxt: list[tuple[str, str]] = []
        for who, prompt in frontier:
            if who in spoken:
                continue
            spoken.add(who)
            reply = await reply_as(store, cid, who, prompt, msg["id"], names, lean, group=True, quiet=hop > 0)
            if reply and hop + 1 < MAX_HOPS:
                nxt += [(m, f"{names.get(who, who)} ti scrive: {reply}") for m in mentioned(reply, names, exclude=spoken | {who})]
        frontier = nxt[:MAX_SPEAKERS]
        if not frontier:
            break


async def run_once(store: Store, lean=None) -> int:
    names = agent_names()
    items = pending(store, [*names, "grupo"])
    for cid, msg in items:
        await handle(store, cid, msg, names, lean)
    return len(items)


def start_loop(store: Store) -> None:
    async def loop():
        while True:
            await asyncio.sleep(POLL_S)
            try:
                await run_once(store)
            except Exception as e:                                  # un giro fallito non ferma la chat
                print("chat:", type(e).__name__, e, flush=True)
    asyncio.get_event_loop().create_task(loop())
