"""Il COO ti scrive su Telegram: cosa e' cambiato (proposte, PR, anteprime), cosa va deciso, cosa scade. Frasi a regole: 0 token.

Gira da timer (ogni 15 minuti): `hlab notify`. Memoria di cosa ha gia' detto in data/notify_state.json, quindi niente doppioni.
Avvisi solo 07:30-22:00 (Europe/Rome), il riepilogo del mattino una volta al giorno dalle 08:00. Il bot e' quello di Hermes: TELEGRAM_TOKEN e
ALLOWED_CHAT_IDS arrivano da hermes.env, e scrive solo alle chat gia' autorizzate.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from . import config, powers, uptime
from .events import Events

TZ = ZoneInfo("Europe/Rome")
QUIET_FROM, QUIET_TO, DIGEST_FROM = (22, 0), (7, 30), 8
KIND = {"github_pr": "una pull request", "netlify_preview": "un'anteprima del sito", "site_publish": "la pubblicazione di un sito"}
FIELD = {"notes": "le note", "status": "lo stato", "deadline": "la scadenza", "payment_deadline": "la scadenza del pagamento",
         "is_online": "lo stato online", "target_url": "l'indirizzo del sito", "staging_url": "l'indirizzo di prova", "done": "il completamento",
         "due_date": "la data", "title": "il titolo"}


def _state_path() -> Path:
    return config.DATA_DIR / "notify_state.json"              # letto a ogni chiamata: i test cambiano DATA_DIR


def load() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(state: dict) -> None:
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(state), encoding="utf-8")


def quiet(now: datetime) -> bool:
    t = (now.hour, now.minute)
    return t >= QUIET_FROM or t < QUIET_TO


def _short(s: str, n: int = 140) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def task_lines(store, ev: Events, st: dict) -> list[str]:
    """I nuovi flussi della console: un agente chiede un consenso, fa una domanda, consegna un lavoro, incontra un problema, o il Memory Keeper propone una regola.
    Una volta sola per voce. Al primo giro si registra l'esistente in silenzio: niente valanga di avvisi per cio' che c'era gia'."""
    from .chat import agent_names
    names = agent_names()
    seen = st.get("tasks") if isinstance(st.get("tasks"), dict) else None
    first = seen is None
    seen = seen if seen is not None else {}
    lines = []

    def once(key: str, text: str) -> None:
        if key not in seen:
            seen[key] = True
            if not first:
                lines.append(text)
    for r in store.query("tasks"):
        t = r["data"]
        if t.get("hlab") or t.get("estado") == "completada" or t.get("asignado") not in names:
            continue
        who, title, tid, status = names[t["asignado"]], _short(t.get("tarea") or "", 80), r["id"], t.get("agentStatus")
        if status == "needs_info" and t.get("needsFull"):
            once(f"{tid}:consent", f"🟣 {who} chiede il consenso per eseguire «{title}» (esecuzione completa, costo massimo 0,50 $). Aprila in Pipeline e premi «Autorizza esecuzione completa».")
        elif status == "needs_info":
            once(f"{tid}:question", f"❓ {who} ha una domanda sul task «{title}»: {_short(t.get('agentQuestion') or '', 160)}")
        elif status == "done" and t.get("estado") == "en_espera":
            once(f"{tid}:done", f"📬 {who} ha consegnato «{title}»: aspetta il tuo giudizio (Completato oppure Correzioni con un commento).")
        elif status == "error":
            once(f"{tid}:error", f"⚠️ {who} ha avuto un problema con «{title}»: {_short(t.get('agentError') or '', 140)}")
    for rule in ev.rule_rows(status="proposed"):
        once(f"rule{rule['id']}", f"📚 Il Memory Keeper propone una regola per {names.get(rule['agent'], rule['agent'])}: «{_short(rule['rule'], 160)}». Approvala o respingila in Pipeline.")
    st["tasks"] = dict(list(seen.items())[-400:])                 # lo stato non cresce senza limiti
    return lines


def collect(ev: Events, state: dict, store=None) -> tuple[list, dict]:
    """Voci per cio' che e' nuovo dall'ultima volta (testo, oppure (testo, pulsanti) se serve una decisione) e nuovo stato. Non invia nulla."""
    lines, st = [], json.loads(json.dumps(state))
    if store is not None:
        lines += task_lines(store, ev, st)
    seen = st.setdefault("proposals", [])
    for p in ev.proposals("pending"):
        if p["id"] not in seen:
            what = FIELD.get(p["field"], p["field"])
            crit = "⚠️ CRITICA — " if p["entity"] == "user" else ""
            tgt = f"{p['entity']} {p['entity_id']}" if p["entity"] == "user" else f"un {p['entity']}"
            lines.append((f"📝 {crit}{p['agent']} propone di cambiare {what} di {tgt} → {_short(p['new_value'], 60) or 'vuoto'} (#{p['id']}).\n"
                          f"Motivo: {_short(p['reason'], 160)}",
                          [("✅ Conferma e applica", f"hp:ok:{p['id']}"), ("❌ Annulla", f"hp:no:{p['id']}")]))
            seen.append(p["id"])
    ch = st.setdefault("changes", {})
    for c in sorted(powers.listing(ev), key=lambda x: x["id"]):
        old = ch.get(str(c["id"]))
        if old == c["status"]:
            continue
        ch[str(c["id"])] = c["status"]
        what = KIND.get(c["kind"], c["kind"])
        link = c["result"].get("pr") or c["result"].get("anteprima") or ""
        if c["status"] == "proposed":
            lines.append((f"🛠️ {c['agent']} ha preparato {what} su {c['target']}: «{_short(c['title'], 80)}» (#{c['id']}). Aspetta il tuo ok.",
                          [("✅ Approva", f"hc:ok:{c['id']}"), ("❌ Rifiuta", f"hc:no:{c['id']}")]))
        elif c["status"] == "previewed" and old is not None:
            lines.append(f"✅ {what.capitalize()} su {c['target']} e' pronta{' ➜ ' + link if link else ''}. Merge e pubblicazione restano tuoi.")
        elif c["status"] == "previewed":
            lines.append(f"✅ {c['agent']} ha eseguito {what} su {c['target']}{' ➜ ' + link if link else ''}. Controllala: merge e pubblicazione restano tuoi.")
        elif c["status"] == "published":
            pg = c["result"].get("pages")
            if pg and pg != "built":
                lines.append(f"⏳ {c['target']}: inviato a GitHub ma Pages non ha finito (stato: {pg}). Il sito potrebbe non essere ancora aggiornato{' ➜ ' + link if link else ''}.")
            else:
                lines.append(f"🚀 Pubblicato su {c['target']}{' ➜ ' + link if link else ''}.")
    for key in ("proposals", "changes"):                       # lo stato non cresce senza limiti
        if isinstance(st[key], list):
            st[key] = st[key][-300:]
    return lines, st


def chats() -> list[int]:
    return [int(x) for x in os.environ.get("ALLOWED_CHAT_IDS", "").split(",") if x.strip()]


def _deliver(items: list, transport: httpx.BaseTransport | None = None) -> int:
    """Le notizie semplici in un solo messaggio; ogni decisione da prendere in un messaggio suo, coi pulsanti."""
    n, plain = 0, [i for i in items if isinstance(i, str)]
    if plain:
        n += send("\n\n".join(plain), transport)
    for i in items:
        if not isinstance(i, str):
            n += send(i[0], transport, i[1])
    return n


def send(text: str, transport: httpx.BaseTransport | None = None, buttons: list | None = None) -> int:
    """Invia alle chat autorizzate di Hermes. Ritorna quanti messaggi sono partiti."""
    token, n = os.environ.get("TELEGRAM_TOKEN", ""), 0
    if not token:
        raise RuntimeError("manca TELEGRAM_TOKEN (EnvironmentFile=hermes.env)")
    with httpx.Client(transport=transport, timeout=20) as c:
        for chat in chats():
            msg = {"chat_id": chat, "text": text[:3900], "disable_web_page_preview": True}
            if buttons:                                                              # tastiera inline: il tocco arriva al bot come callback, solo dalla chat autorizzata
                msg["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in buttons]]}
            r = c.post(f"https://api.telegram.org/bot{token}/sendMessage", json=msg)
            n += r.status_code == 200
    return n


LEVELS = (70, 90)


def plan_alerts(state: dict, plan_fn=powers.netlify_plan) -> list[tuple[bool, str]]:
    """Crediti Netlify: un avviso a 70% e uno a 90% per periodo di fatturazione (urgente da 90). Senza dati dal piano non dice nulla."""
    plan = plan_fn(max_age=3600)
    if not plan or not plan.get("crediti_inclusi"):
        return []
    pct = 100 * (plan.get("crediti_usati") or 0) / plan["crediti_inclusi"]
    told = state.setdefault("netlify_plan", {})
    if told.get("periodo") != plan.get("fine_periodo"):                  # nuovo periodo: si riparte
        told.clear()
        told["periodo"] = plan.get("fine_periodo")
        told["livelli"] = []
    out = []
    for lv in LEVELS:
        if pct >= lv and lv not in told["livelli"]:
            told["livelli"].append(lv)
            fine = (plan.get("fine_periodo") or "")[:10]
            out.append((lv >= 90, f"📊 Netlify: usati {plan.get('crediti_usati')} crediti su {plan['crediti_inclusi']} ({pct:.0f}%) del mese"
                                  f"{' (fino al ' + fine + ')' if fine else ''}. Sopra la soglia di prudenza le anteprime automatiche si fermano: le build si bloccano a 100%."))
    return out


async def digest_text() -> str:
    """Riepilogo del mattino: stato e scadenze dal CRM, senza modello."""
    from .lean import Lean
    lean, parts = Lean(), []
    for q in ("stato", "scadenze 7 giorni"):
        try:
            parts.append((await lean.ask(q)).text.strip())
        except Exception as e:                               # CRM non raggiungibile: il riepilogo salta, non gli avvisi
            parts.append(f"({q}: non disponibile — {type(e).__name__})")
    try:                                                     # cosa aspetta te tra i task degli agenti
        from . import taskrun
        from .docstore import Store
        line = taskrun.waiting_line(taskrun.waiting(Store()))
        if line:
            parts.append("👤 Aspettano te: " + line + ".")
    except Exception:
        pass
    plan = await asyncio.to_thread(powers.netlify_plan, None, None, 3600)
    if plan and plan.get("crediti_inclusi"):
        parts.append(f"📊 Netlify ({plan.get('piano')}): {plan.get('crediti_usati')}/{plan['crediti_inclusi']} crediti usati questo mese.")
    return "☀️ Buongiorno. Ecco il punto:\n\n" + "\n\n".join(parts)


AUDIT_MAX_SITES = 6


def audit_week_alerts(state: dict, results: dict[str, dict]) -> list[str]:
    """Confronta i risultati della settimana con quelli della precedente e ritorna SOLO gli avvisi che meritano un messaggio:
    voto di sicurezza D/F, SEO sotto 60, oppure un peggioramento rispetto alla settimana scorsa. Aggiorna lo storico in `state['audit_hist']`."""
    hist = state.setdefault("audit_hist", {})
    out = []
    for site, r in results.items():
        sic, seo_p = (r.get("riepilogo") or {}).get("sicurezza"), (r.get("riepilogo") or {}).get("seo")
        if sic is None and seo_p is None:
            continue                                              # sito non raggiungibile: lo dice gia' il monitoraggio dei siti
        prev = hist.get(site) or {}
        why = []
        if sic in ("D", "F"):
            why.append(f"sicurezza {sic}")
        elif sic and prev.get("sic") and "ABCDF".index(sic) > "ABCDF".index(prev["sic"]):
            why.append(f"sicurezza peggiorata ({prev['sic']} → {sic})")
        if isinstance(seo_p, int) and (seo_p < 60 or (isinstance(prev.get("seo"), int) and prev["seo"] - seo_p >= 15)):
            why.append(f"SEO {seo_p}" + (f" (era {prev['seo']})" if isinstance(prev.get("seo"), int) else ""))
        if why:
            first = next(iter(r.get("priorita") or []), None)
            out.append(f"🔎 Controllo settimanale · {site}: " + ", ".join(why) + (f". Prima cosa: {first['messaggio'][:140]}" if first else "") + ".")
        hist[site] = {"sic": sic, "seo": seo_p, "settimana": state.get("audit_week")}
    return out


async def weekly_audit(state: dict, now: datetime, targets_fn=None, full_fn=None) -> list[str]:
    """Ogni lunedi (dopo l'ora del riepilogo, mai di notte): sicurezza, SEO e prestazioni dei siti registrati, a campione e in sola lettura.
    Silenzio se va tutto bene: scrive solo se serve."""
    week = now.strftime("%G-W%V")
    if now.weekday() != 0 or state.get("audit_week") == week:
        return []
    from . import audit
    targets = (targets_fn or audit.audit_targets)()
    results = {}
    for name, url in list(targets.items())[:AUDIT_MAX_SITES]:
        try:
            results[name] = await (full_fn or audit.full)(url)
        except Exception:
            continue
    state["audit_week"] = week
    return audit_week_alerts(state, results)



async def run(ev: Events | None = None, now: datetime | None = None, transport: httpx.BaseTransport | None = None, digest=digest_text, dry: bool = False,
              uptime_run=uptime.run, plan_fn=powers.netlify_plan, store=None) -> dict:
    ev, now = ev or Events(), now or datetime.now(TZ)
    if store is None:
        from .docstore import Store
        store = Store()
    state = load()
    sent, held = 0, 0
    if "changes" not in state:                               # primo avvio: il passato si registra in silenzio, si parla solo del nuovo
        state = collect(ev, {}, store)[1]
        if not dry:
            save(state)
        return {"inviati": 0, "in_attesa_orario": 0, "ora": now.strftime("%H:%M"), "primo_avvio": True}
    if not quiet(now):
        lines, new = collect(ev, state, store)
        if lines:
            if dry:
                held = len(lines)
            else:
                sent += _deliver(lines, transport)
                state = new
        elif not dry:
            state = new                                      # niente da dire, ma lo stato si aggiorna (primo giro dei task: registrati in silenzio)
    else:
        held = len(collect(ev, state, store)[0])
    try:                                                     # siti giu': avviso subito anche di notte; il resto (lento, certificato) solo di giorno
        alerts, _ = await uptime_run(persist=not dry)
    except Exception:                                        # il monitoraggio non deve mai bloccare gli altri avvisi
        alerts = []
    if not quiet(now):                                       # crediti Netlify: avvisi a 70% e 90% del mese; di notte non si segna come detto cio' che non si manda
        try:
            alerts += plan_alerts(json.loads(json.dumps(state)) if dry else state, plan_fn)
        except Exception:
            pass
    now_ok = [t for urgent, t in alerts if urgent or not quiet(now)]
    if now_ok:
        if dry:
            held += len(now_ok)
        else:
            sent += send("\n\n".join(now_ok), transport)
    if now.hour >= DIGEST_FROM and not quiet(now):             # controllo settimanale dei siti (lunedi): un errore qui non blocca il resto
        try:
            wa = await weekly_audit(state if not dry else json.loads(json.dumps(state)), now)
            if wa and not dry:
                sent += send("\n\n".join(wa), transport)
        except Exception:
            pass
    today = now.date().isoformat()
    if now.hour >= DIGEST_FROM and not quiet(now) and state.get("digest") != today:
        if not dry:
            sent += send(await digest(), transport)
            state["digest"] = today
    if not dry:
        save(state)
    return {"inviati": sent, "in_attesa_orario": held, "ora": now.strftime("%H:%M")}
