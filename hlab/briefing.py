"""Il briefing della squadra: gli agenti si riuniscono nella sala riunioni e il COO riassume dove siamo e dove migliorare.

Niente di inventato e niente token: la situazione e le scadenze arrivano dal CRM (percorso leggero `lean`), "come stiamo andando" dal registro
dei feedback (cosa hai accettato, respinto o corretto) e dalle regole che il Memory Keeper ha proposto e aspettano il tuo ok.
Parte da solo ogni giorno feriale alle 9:00 (ora di Roma) e quando scrivi "briefing" in chat. Il testo va nella chat di squadra; la GUI legge
`config/briefing` (inizio e fine) e porta gli agenti attivi nella sala riunioni per la durata.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import chat
from .docstore import Store
from .events import Events

TZ = ZoneInfo("Europe/Rome")
HOUR = 9
MEETING_S = 180                                                     # per quanto tempo gli agenti restano in sala riunioni
LEARN_CAP_USD = float(os.environ.get("HLAB_LEARN_BUDGET_USD", "0.30"))   # tetto di spesa di UNA elaborazione dei feedback
LEARN_TIMEOUT_S = 420
LEARN_RE = re.compile(r"^\s*(elabora|processa)\s+(i\s+)?feedback\b|^\s*impara\s+dai\s+feedback\b", re.I)
LEARN_ON_RE = re.compile(r"^\s*attiva\s+(l.\s*)?apprendimento\b", re.I)
LEARN_OFF_RE = re.compile(r"^\s*disattiva\s+(l.\s*)?apprendimento\b", re.I)
LEARN_PROMPT = ("Delega al memory-keeper l'elaborazione dei feedback non ancora elaborati, seguendo la sua skill estrai-regole: "
                "proponi regole solo dove il commento di Lorenzo spiega il motivo. Rispondi in italiano con un rapporto breve.")
_tasks: set = set()                                                 # riferimenti ai lavori in background (altrimenti Python li puo' buttare via)
BRIEF_RE = re.compile(r"^\s*(fai\s+(un\s+)?|facciamo\s+(un\s+)?|avvia\s+(un\s+)?)?briefing\b", re.I)


def tally(rows: list[dict], names: dict[str, str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        d = out.setdefault(r["agent"], {"accepted": 0, "rejected": 0, "corrected": 0})
        if r["verdict"] in d:
            d[r["verdict"]] += 1
    return out


def compose(today: str, situation: str, deadlines: str, feedback: list[dict], names: dict[str, str], rules_waiting: int, proposals_waiting: int) -> str:
    lines = [f"📋 Briefing del {today}", "", "Situazione", situation.strip() or "Nessun dato dal CRM.", "", "Scadenze", deadlines.strip() or "Nessuna.", ""]
    lines.append("Come stiamo andando")
    t = tally(feedback, names)
    if t:
        for agent, c in sorted(t.items()):
            lines.append(f"- {names.get(agent, agent)}: {c['accepted']} accettati, {c['rejected']} respinti, {c['corrected']} corretti")
    else:
        lines.append("Ancora nessun feedback: giudica i task consegnati per farci migliorare.")
    todo = [f for f in feedback if f["verdict"] in ("rejected", "corrected") and not f.get("processed")]
    if todo:
        lines += ["", "Da migliorare (tuoi commenti non ancora trasformati in regole)"]
        lines += [f"- {names.get(f['agent'], f['agent'])}: {(f.get('comment') or 'nessun commento')[:140]}" for f in todo[:3]]
        lines.append("Il Memory Keeper può proporne una regola; non diventa attiva senza il tuo ok.")
    waiting = []
    if proposals_waiting:
        waiting.append(f"{proposals_waiting} proposte da approvare")
    if rules_waiting:
        waiting.append(f"{rules_waiting} regole proposte")
    lines += ["", "In attesa di te: " + (", ".join(waiting) if waiting else "niente.")]
    return "\n".join(lines)


async def build(lean, ev: Events | None = None, now: datetime | None = None) -> str:
    ev = ev or Events()
    now = now or datetime.now(TZ)
    names = chat.agent_names()

    async def ask(q: str) -> str:
        try:
            return (await asyncio.wait_for(lean.ask(q), 60)).text
        except Exception:                                           # CRM non raggiungibile: il briefing esce lo stesso, con cio' che c'e'
            return ""
    situation, deadlines = await ask("Come va la situazione?"), await ask("Quali sono le scadenze?")
    feedback = ev.feedback_rows(processed=True, limit=500) + ev.feedback_rows(processed=False, limit=500)
    return compose(now.strftime("%d/%m/%Y"), situation, deadlines, feedback, names,
                   len(ev.rule_rows(status="proposed")), len(ev.proposals("pending")))


async def run(store: Store, lean=None, ev: Events | None = None, chat_id: str = "grupo", now: float | None = None) -> str:
    from .lean import Lean
    ev = ev or Events()
    text = await build(lean or Lean(), ev)
    from . import taskrun
    mine = taskrun.waiting_line(taskrun.waiting(store))
    if mine:
        text += "\n\nTask che aspettano te: " + mine + "."
    note = learn_note(store, ev)
    if note:
        text += "\n\n" + note
    t = (now if now is not None else time.time()) * 1000
    store.set("config/briefing", {"startedAt": int(t), "until": int(t + MEETING_S * 1000), "text": text, "day": datetime.now(TZ).strftime("%Y-%m-%d")}, merge=True)
    store.add(f"agentChats/{chat_id}/mensajes", {"from": "agente", "agentId": "coo", "agentName": "COO", "text": text[: chat.MAX_ANSWER],
                                                 "createdAt": {"__op": "serverTimestamp"}})
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    cfg = store.get("config/briefing") or {}
    if note and cfg.get("learnAuto") and cfg.get("learnDay") != today:          # una sola elaborazione al giorno, e solo se l'hai accesa tu
        store.set("config/briefing", {"learnDay": today}, merge=True)
        learn_in_background(store, chat_id, ev)
    return text


def due(last_day: str | None, now: datetime) -> bool:
    return now.weekday() < 5 and now.hour >= HOUR and now.hour < 13 and last_day != now.strftime("%Y-%m-%d")


def start_loop(store: Store) -> None:
    async def loop():
        while True:
            await asyncio.sleep(30)
            try:
                if due((store.get("config/briefing") or {}).get("day"), datetime.now(TZ)):
                    await run(store)
            except Exception as e:                                  # un briefing mancato non ferma l'API
                print("briefing:", type(e).__name__, e, flush=True)
    asyncio.get_event_loop().create_task(loop())


# ---- apprendimento: il Memory Keeper trasforma i tuoi commenti in regole proposte (le approvi tu) ----

def learnable(ev: Events) -> list[dict]:
    """Feedback respinti o corretti, non ancora elaborati, con un commento che spiega il motivo: e' da qui che si impara.
    Le approvazioni ("approvata da Lorenzo") non insegnano niente: non valgono il costo di una elaborazione."""
    return [f for f in ev.feedback_rows(processed=False, limit=200)
            if f["verdict"] in ("rejected", "corrected") and (f.get("comment") or "").strip()]


def learn_note(store: Store, ev: Events) -> str:
    n = len(learnable(ev))
    if not n:
        return ""
    auto = bool((store.get("config/briefing") or {}).get("learnAuto"))
    return (f"Apprendimento: {n} feedback con commento da elaborare. Automatico: {'acceso' if auto else 'spento'}. "
            f"Una elaborazione costa al massimo {LEARN_CAP_USD:.2f} $ (con i modelli gratuiti di solito 0). "
            + ("Parte da sola dopo il briefing." if auto else "Scrivi 'elabora i feedback' per farla ora, o 'attiva apprendimento' per farla a ogni briefing."))


async def run_learning(store: Store, ev: Events | None = None, chat_id: str = "grupo", runner_fn=None, cap: float | None = None) -> str:
    ev, cap = ev or Events(), cap or LEARN_CAP_USD
    todo = learnable(ev)
    if not todo:
        msg = "Niente da elaborare: servono feedback con un commento che spieghi il motivo."
    else:
        if runner_fn is None:
            from . import runner
            runner_fn = runner.run
        parts: list[str] = []
        try:
            out = await asyncio.wait_for(runner_fn(LEARN_PROMPT, on_text=parts.append, budget=cap), LEARN_TIMEOUT_S)
            body = ("\n".join(parts).strip() or str(out.get("result") or "Fatto.")).strip()
            msg = (f"🧠 Apprendimento: {len(todo)} feedback letti.\n{body}\n"
                   f"Costo: {float(out.get('cost_usd') or 0):.2f} $ (tetto {cap:.2f} $). Le regole proposte aspettano il tuo ok in Pipeline.")
        except asyncio.TimeoutError:
            msg = "L'elaborazione dei feedback ha impiegato troppo e si è fermata: riprova più tardi."
        except Exception as e:                                      # modelli esauriti, router spento, tetto raggiunto...
            msg = f"Non sono riuscito a elaborare i feedback ({type(e).__name__}). Riprova più tardi."
    store.add(f"agentChats/{chat_id}/mensajes", {"from": "agente", "agentId": "memory-keeper", "agentName": "Memory Keeper", "text": msg[: chat.MAX_ANSWER],
                                                 "createdAt": {"__op": "serverTimestamp"}})
    return msg


def learn_in_background(store: Store, chat_id: str = "grupo", ev: Events | None = None, runner_fn=None) -> None:
    t = asyncio.ensure_future(run_learning(store, ev, chat_id, runner_fn))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def handle_learning(store: Store, text: str, chat_id: str, ev: Events | None = None, runner_fn=None) -> str | None:
    """Comandi in chat. Ritorna la risposta, o None se il testo non c'entra."""
    if LEARN_ON_RE.match(text) or LEARN_OFF_RE.match(text):
        on = bool(LEARN_ON_RE.match(text))
        store.set("config/briefing", {"learnAuto": on}, merge=True)
        return (f"Apprendimento automatico acceso: dopo ogni briefing con feedback commentati lavoro io, al massimo {LEARN_CAP_USD:.2f} $ a volta. "
                "Scrivi 'disattiva apprendimento' per spegnerlo.") if on else "Apprendimento automatico spento. Resta 'elabora i feedback' quando lo vuoi tu."
    if LEARN_RE.match(text):
        if not learnable(ev or Events()):
            return "Niente da elaborare: servono feedback con un commento che spieghi il motivo."
        learn_in_background(store, chat_id, ev, runner_fn)
        return f"Avviato: il Memory Keeper legge i tuoi feedback (costo massimo {LEARN_CAP_USD:.2f} $). Scrivo qui il risultato."
    return None
