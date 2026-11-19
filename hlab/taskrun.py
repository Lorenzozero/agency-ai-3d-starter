"""Il motore dei task della console: un compito assegnato a un agente dalla GUI viene davvero eseguito.

Il protocollo e' quello che la GUI gia' mostra (vedi AgentResult in TaskForm.jsx):
  agentStatus  running -> done | error | needs_info | queued     (+ agentOutput / agentError / agentQuestion)
  estado       en_progreso mentre lavora, en_espera ("Attesa approvazione") a consegna fatta.
Spostare la scheda in "Correzioni" (en_revision) con un commento in `feedback` la fa rifare all'agente.
Le schede nate da hlab (proposte, scadenze, lavoro in corso) hanno il campo `hlab` e non si toccano.

Due percorsi, dal piu' economico:
  1. percorso leggero (`lean`): regole e CRM, quasi sempre 0 token. Se riconosce la richiesta (stato, scadenze, lead, cliente...) risponde subito.
  2. esecuzione completa: se la richiesta e' libera (testi, ricerche, piu' passaggi) il percorso leggero darebbe solo una risposta breve, quindi
     lavora il COO con l'agente giusto, i suoi strumenti e i controlli di sicurezza di sempre. COSTA, quindi parte solo con il tuo consenso:
     un bottone nella scheda ("Autorizza esecuzione completa", un task alla volta) oppure `attiva esecuzione completa` in chat (per tutti i task).
     Ogni esecuzione ha un tetto di spesa e c'e' un tetto giornaliero; il costo reale resta scritto nella scheda.
Se all'agente manca un'informazione indispensabile risponde "DOMANDA: ...": la scheda va in needs_info e, quando rispondi nel commento e la
rimandi in Correzioni, riparte con la tua risposta.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time

from . import chat, registry
from .docstore import Store
from .events import Events

POLL_S = 5
BEAT_S = 10                                                         # mentre lavora, l'agente "batte" ogni tanto: l'ufficio lo tiene alla scrivania
DONE_STATES = ("done", "needs_info", "error")
PREV_MAX = 6000                                                     # quanto del risultato precedente si rimanda all'agente
FULL_BUDGET_USD = float(os.environ.get("HLAB_FULL_BUDGET_USD", "0.50"))      # tetto di UNA esecuzione completa
FULL_DAILY_USD = float(os.environ.get("HLAB_FULL_DAILY_USD", "1.50"))        # tetto di tutte le esecuzioni complete di un giorno
FULL_TIMEOUT_S = 600
FULL_ON_RE = re.compile(r"^\s*attiva\s+(l.\s*)?esecuzione\s+completa\b", re.I)
FULL_OFF_RE = re.compile(r"^\s*disattiva\s+(l.\s*)?esecuzione\s+completa\b", re.I)
QUESTION_RE = re.compile(r"^\s*DOMANDA\s*:\s*(.+)", re.I | re.S)
WORK_ORDER_RE = re.compile(r"\b(scrivi|scrivere|redigi|redigere|prepara\w*|crea|creare|genera\w*|componi|stendi|progetta\w*|pianifica\w*|proponi|"
                           r"elabora\w*|traduci|riscrivi|analizza\w*|riassumi|compila\w*|realizza\w*|sviluppa\w*|organizza\w*|bozza)\b", re.I)
DELEGATE_RE = re.compile(r"```tareas\s*(\[.*?\])\s*```", re.S)
MAX_DELEGATED = 5                                                   # al massimo tanti task per delega: il COO non puo' inondare la Pipeline


def waiting(store: Store) -> dict[str, int]:
    """Cosa aspetta TE tra i task degli agenti: consensi per un'esecuzione completa, domande, consegne da giudicare, problemi."""
    out = {"consent": 0, "question": 0, "delivery": 0, "error": 0}
    for r in store.query("tasks"):
        t = r["data"]
        if t.get("hlab") or t.get("estado") == "completada":
            continue
        st = t.get("agentStatus")
        if st == "needs_info":
            out["consent" if t.get("needsFull") else "question"] += 1
        elif st == "done" and t.get("estado") == "en_espera":
            out["delivery"] += 1
        elif st == "error":
            out["error"] += 1
    return out


def waiting_line(counts: dict[str, int]) -> str:
    """Una riga leggibile, "" se non aspetta niente."""
    parts = []
    for key, one, many in (("delivery", "consegna da giudicare", "consegne da giudicare"), ("consent", "consenso da dare", "consensi da dare"),
                           ("question", "domanda a cui rispondere", "domande a cui rispondere"), ("error", "task con un problema", "task con un problema")):
        n = counts.get(key, 0)
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    return ", ".join(parts)


def recover(store: Store) -> int:
    """All'avvio: le schede rimaste "in corso" perche' il servizio si e' fermato a meta' tornano in coda. Un agente non lavora mentre il motore e' spento."""
    n = 0
    for r in store.query("tasks"):
        t = r["data"]
        if not t.get("hlab") and t.get("agentStatus") == "running":
            store.update(f"tasks/{r['id']}", {"agentStatus": "queued"})
            n += 1
    return n


def prompt_of(t: dict) -> str:
    p = (t.get("tarea") or "").strip()
    if t.get("descripcion"):
        p += ". " + t["descripcion"].strip()
    if t.get("agentStatus") in DONE_STATES:                          # un rifacimento: porta con se' il risultato di prima e le correzioni di chi ha riletto
        if t.get("agentOutput"):
            p += f"\nRisultato precedente (da correggere, non da rifare da zero):\n{t['agentOutput'].strip()[:PREV_MAX]}"
        if t.get("agentQuestion") and t.get("agentStatus") == "needs_info":
            p += f"\nDomanda che avevi fatto: {t['agentQuestion'].strip()[:800]}"
        if t.get("feedback"):
            p += f"\nCorrezioni richieste (o risposta alla tua domanda): {t['feedback'].strip()}"
    return p


def roster(store: Store) -> dict[str, str]:
    """Agenti a cui il COO puo' delegare: attivi, escluso lui. id -> in cosa e' bravo."""
    out = {}
    for name, spec in registry.load_all().items():
        if name != "coo" and (store.get(f"agents/{name}") or {}).get("active") is not False:
            out[name] = spec.description.split(".")[0][:110]
    return out


def delegation_rules(team: dict[str, str]) -> str:
    lines = "; ".join(f"{a} ({d})" for a, d in team.items())
    return ("\nSe il lavoro richiede piu' agenti, NON eseguirlo tu: rispondi con una riga di riepilogo e un blocco "
            "```tareas\n[{\"agente\": \"<id>\", \"titulo\": \"...\", \"descripcion\": \"...\", \"prioridad\": \"media\"}]\n```"
            f" con al massimo {MAX_DELEGATED} task, uno per l'agente piu' adatto, ognuno completo e autosufficiente. Agenti disponibili: {lines}. "
            "Se invece puoi farlo tu, consegnalo direttamente senza blocco.")


def parse_delegation(text: str) -> tuple[str, list | None]:
    """(riepilogo senza il blocco, elenco di task o None). Un blocco rotto non fa danni: si consegna il testo com'e'."""
    m = DELEGATE_RE.search(text)
    if not m:
        return text, None
    try:
        items = json.loads(m.group(1))
    except ValueError:
        return text, None
    return (text[:m.start()] + text[m.end():]).strip(), items if isinstance(items, list) else None


def create_delegated(store: Store, parent_id: str, parent: dict, items: list, team: dict[str, str]) -> tuple[list[tuple[str, str]], list[str]]:
    """Le schede per gli altri agenti: solo agenti attivi del team, niente doppioni, un massimo. Ritorna (create (agente, titolo), scartate con il motivo)."""
    open_now = {((r["data"].get("asignado") or ""), (r["data"].get("tarea") or "").strip().lower())
                for r in store.query("tasks") if r["data"].get("estado") != "completada"}
    made, skipped, day = [], [], time.strftime("%Y-%m-%d")
    for it in items:
        if not isinstance(it, dict):
            continue
        agent, title = str(it.get("agente") or "").strip(), str(it.get("titulo") or "").strip()[:200]
        if len(made) >= MAX_DELEGATED:
            skipped.append(f"{title or agent}: troppi task (massimo {MAX_DELEGATED})")
        elif agent not in team:
            skipped.append(f"{title or agent}: agente '{agent}' non disponibile")
        elif not title:
            skipped.append(f"{agent}: manca il titolo")
        elif (agent, title.lower()) in open_now:
            skipped.append(f"{title}: esiste gia' uno uguale aperto")
        else:
            store.add("tasks", {"tarea": title, "descripcion": str(it.get("descripcion") or "")[:20_000], "cliente": parent.get("cliente") or "",
                                "prioridad": it.get("prioridad") if it.get("prioridad") in ("urgente", "alta", "media", "baja") else (parent.get("prioridad") or "media"),
                                "estado": "pendiente", "asignado": agent, "asignadoAgente": True, "fechaVencimiento": "", "fechaRequerimiento": day,
                                "links": "", "linkTerminada": "", "feedback": "", "totalTimeSeconds": 0, "createdBy": "coo", "creadoPor": "COO (delega)",
                                "delegadoPor": parent_id, "createdAt": {"__op": "serverTimestamp"}})
            open_now.add((agent, title.lower()))
            made.append((agent, title))
    return made, skipped


def full_prompt(t: dict, agent: str, name: str, team: dict[str, str] | None = None) -> str:
    ctx = [f"Cliente: {t['cliente']}"] if t.get("cliente") else []
    ctx.append(f"Priorita': {t.get('prioridad') or 'media'}")
    who = "Sei tu l'agente incaricato." if agent == "coo" else f"Delega il lavoro al subagente '{agent}' ({name}): e' lui l'incaricato."
    if agent == "coo" and team and not t.get("delegadoPor"):          # i task nati da una delega non delegano ancora: niente catene
        who += delegation_rules(team)
    return (f"Esegui questo task assegnato a {name}. {who}\n{prompt_of(t)}\n{' · '.join(ctx)}\n"
            "Consegna il risultato completo, in italiano, pronto da rivedere. La tua risposta finale deve contenere SOLO il testo consegnato (o, se deleghi, il riepilogo e il blocco): "
            "niente introduzioni, commenti sul lavoro o frasi di chiusura. Non pubblicare ne' inviare nulla: la consegna la approva Lorenzo. "
            "Se ti manca un'informazione indispensabile, rispondi SOLO con 'DOMANDA: <la tua domanda>'.")


def todo(store: Store, names: dict[str, str]) -> list[tuple[str, dict]]:
    """Schede da eseguire, le piu' vecchie per prime: nuove (senza esito), rimandate in "Correzioni" o rimaste in coda."""
    out = []
    for r in store.query("tasks"):
        t = r["data"]
        if t.get("hlab") or t.get("asignado") not in names:
            continue
        st = t.get("agentStatus")
        fresh = not st and t.get("estado") in ("pendiente", "en_progreso")
        redo = t.get("estado") == "en_revision" and st in DONE_STATES
        waiting = st == "queued" and t.get("estado") in ("pendiente", "en_progreso")
        if fresh or redo or waiting:
            out.append((r["id"], t))
    return out


# ---- consenso e tetti dell'esecuzione completa ----

def spent_today(store: Store, today: str | None = None) -> float:
    cfg = store.get("config/engine") or {}
    return float(cfg.get("spentUsd") or 0) if cfg.get("day") == (today or time.strftime("%Y-%m-%d")) else 0.0


def handle_engine(store: Store, text: str) -> str | None:
    """Comandi in chat: "attiva/disattiva esecuzione completa". Ritorna la risposta, o None se il testo non c'entra."""
    if FULL_ON_RE.match(text):
        store.set("config/engine", {"fullAuto": True}, merge=True)
        return (f"Esecuzione completa automatica accesa: i task liberi partono da soli, al massimo {FULL_BUDGET_USD:.2f} $ ciascuno e {FULL_DAILY_USD:.2f} $ al giorno "
                "(con i modelli gratuiti spesso 0). Scrivi 'disattiva esecuzione completa' per tornare a chiedere ogni volta.")
    if FULL_OFF_RE.match(text):
        store.set("config/engine", {"fullAuto": False}, merge=True)
        return "Esecuzione completa automatica spenta: per i task liberi ti chiedo il consenso nella scheda."
    return None


def ask_consent(store: Store, path: str, why: str) -> str:
    left = max(FULL_DAILY_USD - spent_today(store), 0)
    store.update(path, {"agentStatus": "needs_info", "needsFull": True, "estado": "en_espera",
                        "agentQuestion": (f"{why} Un'esecuzione completa costa al massimo {FULL_BUDGET_USD:.2f} $ (con i modelli gratuiti spesso 0). "
                                          f"Oggi restano {left:.2f} $ sui {FULL_DAILY_USD:.2f} $ del tetto giornaliero. Usa 'Autorizza esecuzione completa' per avviarla.")})
    return "needs_info"


def real_cost(t0: float, t1: float, nominal: float) -> float:
    """Quanto e' costato davvero. Con il router il costo vero sta nel suo registro (i modelli gratuiti costano 0); la cifra dell'SDK e' solo
    una stima, come se ogni modello fosse a pagamento. Senza registro (chiave Anthropic diretta) vale la cifra dell'SDK."""
    import json

    from . import config
    p = config.DATA_DIR / "router.jsonl"
    if not p.exists():
        return nominal
    try:
        total = 0.0
        for line in p.read_text(encoding="utf-8").splitlines()[-3000:]:
            r = json.loads(line)
            if t0 - 2 <= r.get("ts", 0) <= t1 + 2:
                total += float(r.get("cost") or 0)
        return round(total, 4)
    except Exception:
        return nominal


def record_run(store: Store, path: str, t: dict, started: float, cost: float = 0.0) -> dict:
    """Costo e tempo restano nella scheda: quanto e' costato e quanto ha lavorato l'agente."""
    return {"agentRuns": int(t.get("agentRuns") or 0) + 1, "agentLastRunAt": int(time.time() * 1000),
            "agentCostUsd": round(float(t.get("agentCostUsd") or 0) + cost, 4),
            "totalTimeSeconds": int(t.get("totalTimeSeconds") or 0) + int(time.time() - started)}


async def beating(job, who: str, ev: Events, label: str):
    """Aspetta un lavoro lungo; intanto l'agente si fa vedere in ufficio."""
    while True:
        ev.log("chat", who, "task", detail=label[:80])
        done, _ = await asyncio.wait({job}, timeout=BEAT_S)
        if done:
            return job.result()


async def full_run(store: Store, tid: str, t: dict, names: dict[str, str], runner_fn=None, started: float | None = None) -> str:
    path, who = f"tasks/{tid}", t["asignado"]
    started = started or time.time()
    left = FULL_DAILY_USD - spent_today(store)
    if left <= 0.01:                                                 # tetto giornaliero raggiunto: niente spese, la scheda lo spiega
        store.update(path, {"agentStatus": "needs_info", "needsFull": False, "estado": "en_espera",
                            "agentQuestion": f"Tetto giornaliero di {FULL_DAILY_USD:.2f} $ raggiunto: l'esecuzione completa riparte domani, o alza HLAB_FULL_DAILY_USD."})
        return "needs_info"
    cap = min(FULL_BUDGET_USD, left)
    if runner_fn is None:
        from . import runner
        runner_fn = runner.run
    store.update(path, {"agentStatus": "running", "estado": "en_progreso", "agentError": "", "agentQuestion": "", "needsFull": False})
    parts: list[str] = []
    ev = Events()
    team = roster(store)
    prompt = full_prompt(t, who, names.get(who, who), team)
    out = None
    for attempt in (1, 2):                                           # i modelli gratuiti ogni tanto sbagliano un passaggio: un secondo tentativo risolve quasi sempre
        parts.clear()
        job = asyncio.ensure_future(asyncio.wait_for(runner_fn(prompt, on_text=parts.append, budget=cap), FULL_TIMEOUT_S))
        try:
            out = await beating(job, who, ev, t.get("tarea") or "")
            break
        except asyncio.TimeoutError:
            store.update(path, {"agentStatus": "error", "estado": "en_progreso", **record_run(store, path, t, started),
                                "agentError": f"L'esecuzione ha superato i {FULL_TIMEOUT_S // 60} minuti e si è fermata. Rimandala in Correzioni per riprovare."})
            return "error"
        except Exception as e:                                       # router spento, modelli esauriti, tetto di spesa raggiunto...
            if attempt == 1:
                await asyncio.sleep(3)
                continue
            store.update(path, {"agentStatus": "error", "estado": "en_progreso", **record_run(store, path, t, started),
                                "agentError": f"Non sono riuscito a eseguire il task ({type(e).__name__}: {str(e)[:160]}). Rimandalo in Correzioni per riprovare."})
            return "error"
    cost = real_cost(started, time.time(), float(out.get("cost_usd") or 0))          # il costo vero (0 con i modelli gratuiti), non la stima dell'SDK
    day = time.strftime("%Y-%m-%d")
    store.set("config/engine", {"day": day, "spentUsd": round(spent_today(store, day) + cost, 4)}, merge=True)
    text = (str(out.get("result") or "").strip() or (parts[-1].strip() if parts else ""))[:150_000]      # il risultato finale, non le frasi di servizio dei passaggi
    stats = {**record_run(store, path, t, started, cost)}
    if who == "coo" and not t.get("delegadoPor"):                    # il COO ha diviso il lavoro: nascono le schede per gli altri agenti
        summary, items = parse_delegation(text)
        if items is not None:
            made, skipped = create_delegated(store, tid, t, items, team)
            if made:
                lines = [summary or "Ho diviso il lavoro tra gli agenti."] + [f"- {chat.agent_names().get(a, a)}: {title}" for a, title in made]
                if skipped:
                    lines += ["Non creati:"] + [f"- {s}" for s in skipped]
                lines.append("I task sono in Pipeline: ognuno parte da solo (o ti chiede il consenso se richiede un'esecuzione completa).")
                out_text = "\n".join(lines)
                store.update(path, {**stats, "agentStatus": "done", "agentOutput": out_text, "estado": "en_espera", "agentQuestion": "", "agentError": ""})
                store.add("agentChats/grupo/mensajes", {"from": "agente", "agentId": "coo", "agentName": "COO", "text": out_text[:chat.MAX_ANSWER],
                                                        "createdAt": {"__op": "serverTimestamp"}})
                return "done"
            text = "\n".join([summary, "Nessun task creato:"] + [f"- {s}" for s in skipped]).strip() or text
    q = QUESTION_RE.match(text)
    if q:
        store.update(path, {**stats, "agentStatus": "needs_info", "needsFull": False, "estado": "en_espera", "agentQuestion": q.group(1).strip()[:5000]})
        return "needs_info"
    if not text:
        store.update(path, {**stats, "agentStatus": "error", "estado": "en_progreso", "agentError": "L'agente non ha prodotto nessun risultato. Rimandalo in Correzioni per riprovare."})
        return "error"
    store.update(path, {**stats, "agentStatus": "done", "agentOutput": text, "estado": "en_espera", "agentQuestion": "", "agentError": ""})
    return "done"


async def work_on(store: Store, tid: str, t: dict, names: dict[str, str], lean=None, runner_fn=None) -> str:
    path, who = f"tasks/{tid}", t["asignado"]
    if (store.get(f"agents/{who}") or {}).get("active") is False:
        store.update(path, {"agentStatus": "queued", "estado": "pendiente"})      # in riserva: resta in coda finche' non lo riattivi
        return "queued"
    started = time.time()
    if t.get("fullRunOk"):                                           # consenso gia' dato dalla scheda: si salta il percorso leggero
        return await full_run(store, tid, t, names, runner_fn, started)
    store.update(path, {"agentStatus": "running", "estado": "en_progreso", "agentError": "", "agentQuestion": "", "needsFull": False})
    ev = Events()
    if WORK_ORDER_RE.search(f"{t.get('tarea') or ''} {t.get('descripcion') or ''}"):
        text, kind = "", "chat"                                      # un ordine di lavoro ("scrivi", "prepara"...): le regole del percorso leggero lo scambierebbero per una domanda
    else:
        job = asyncio.ensure_future(chat.answer_full(prompt_of(t), lean))
        text, kind = await beating(job, who, ev, t.get("tarea") or "")
    if kind == "chat":                                               # richiesta libera: il percorso leggero darebbe solo una risposta breve
        if (store.get("config/engine") or {}).get("fullAuto"):
            return await full_run(store, tid, t, names, runner_fn, started)
        return ask_consent(store, path, "Questo task è una richiesta libera: la risposta rapida sarebbe troppo corta.")
    if kind == "error" or text.startswith(("Non sono riuscito", "Ci sto mettendo troppo")):
        store.update(path, {"agentStatus": "error", "agentError": text, "estado": "en_progreso", **record_run(store, path, t, started)})
        return "error"
    store.update(path, {"agentStatus": "done", "agentOutput": text, "estado": "en_espera", "agentQuestion": "", "agentError": "", **record_run(store, path, t, started)})
    return "done"


async def run_once(store: Store, lean=None, runner_fn=None) -> int:
    names = chat.agent_names()
    items = todo(store, names)
    for tid, t in items:
        await work_on(store, tid, t, names, lean, runner_fn)
    return len(items)


def start_loop(store: Store) -> None:
    async def loop():
        try:
            recover(store)
        except Exception as e:
            print("taskrun recover:", type(e).__name__, e, flush=True)
        while True:
            await asyncio.sleep(POLL_S)
            try:
                await run_once(store)
            except Exception as e:                                  # un giro fallito non ferma il motore
                print("taskrun:", type(e).__name__, e, flush=True)
    asyncio.get_event_loop().create_task(loop())
