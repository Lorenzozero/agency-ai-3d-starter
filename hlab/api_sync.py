"""Riempie l'archivio dell'app con lo stato vero del sistema hlab: agenti, battito dei servizi, proposte e modifiche come "task".

Il frontend di Agentes IA 3D ha cinque colonne (pendiente, en_progreso, en_espera = "Espera Aprobación", en_revision = "Correcciones", completada). Qui la corrispondenza:
  proposta in attesa / modifica da approvare  -> en_espera     (la colonna "aspetta la tua approvazione")
  proposta accettata, non ancora applicata    -> en_progreso
  applicata, eseguita o rifiutata             -> completada
La fonte di verita' resta hlab: a ogni giro lo stato dei task si riallinea. Ogni task porta `hlab: {kind, id}` per ritrovare l'originale.
Il gesto inverso: spostare nell'app una carta da en_espera a completada = Conferma, a en_revision = Annulla (vedi attach_hooks): e' la stessa decisione dei pulsanti di Telegram.
"""
from __future__ import annotations

import asyncio
import hashlib
import platform
import threading
import shutil
import subprocess
import time
from pathlib import Path

from . import powers, registry
from .docstore import Store
from .events import Events

SYNC_EVERY_S = 20
SYSTEM_EVERY_S = 120                                                # servizi e limiti: le sonde hanno la loro cache, qui si ripubblica soltanto
CRM_EVERY_S = 300                                                 # il CRM si interroga ogni 5 minuti, non a ogni giro
SERVICES = ("hermes", "hlab-router", "hlab-worker", "hlab-notify.timer", "hlab-api")
ENTITY = {"project": "progetto", "lead": "lead", "task": "task", "user": "utente"}
FIELD = {"notes": "le note", "status": "lo stato", "deadline": "la scadenza", "payment_deadline": "la scadenza del pagamento", "is_online": "online",
         "target_url": "l'indirizzo del sito", "staging_url": "l'indirizzo di prova", "is_active": "l'accesso", "done": "il completamento",
         "due_date": "la data", "title": "il titolo"}


def _ms(t: float | None) -> dict:
    return {"__ts": int((t or time.time()) * 1000)}


# Famiglie di strumenti: (parte del nome del server MCP, id, etichetta, simbolo, colore). Il simbolo e il colore finiscono sul pavimento dell'ufficio dell'agente.
FAMILIES = [("crm", "crm", "CRM", "🗂", "#2563eb"), ("projects", "projects", "Progetti", "📁", "#0d9488"), ("pric", "pricing", "Prezzi", "💶", "#16a34a"),
            ("graph", "graph", "Grafo", "🕸", "#7c3aed"), ("ops", "ops", "Server", "🖥", "#475569"), ("prop", "proposals", "Proposte", "✍", "#db2777"),
            ("draft", "drafts", "Bozze", "📝", "#ea580c"), ("learn", "learning", "Apprendimento", "🎓", "#ca8a04"), ("pow", "powers", "GitHub e anteprime", "🐙", "#111827"),
            ("mem", "memory", "Memoria", "🧠", "#9333ea"), ("websearch", "web", "Ricerca web", "🔎", "#0284c7"), ("webfetch", "web", "Ricerca web", "🔎", "#0284c7"),
            ("read", "files", "Lettura file", "📖", "#64748b"), ("grep", "files", "Lettura file", "📖", "#64748b"), ("glob", "files", "Lettura file", "📖", "#64748b")]
GROUP_KEY = {"crm_read": "crm", "projects_read": "projects", "pricing": "pric", "graph_read": "graph", "ops_read": "ops", "proposals": "prop",
             "drafts": "draft", "learning": "learn", "powers": "pow"}
ACTIVE_S = 30                                                       # un agente e' "al lavoro" finche' l'ultimo suo evento ha meno di tanti secondi
LIVE_EVERY_S = 4


def family_of(tool: str) -> dict | None:
    """Da `mcp__crm__list_clients` o `WebSearch` alla famiglia di strumenti."""
    t = tool.lower()
    key = t.split("__")[1] if t.startswith("mcp__") and t.count("__") >= 2 else t
    for k, fid, label, glyph, color in FAMILIES:
        if key.startswith(k):
            return {"id": fid, "label": label, "glyph": glyph, "color": color}
    return None


def top_tools(ev: Events, specs: dict) -> dict[str, dict]:
    """Per ogni agente lo strumento che usa di piu' (dagli eventi); se non ha ancora lavorato, il primo che gli e' stato dato."""
    counts: dict[str, dict[str, int]] = {}
    for r in ev.tool_counts():
        fam = family_of(r["tool"])
        if fam:
            d = counts.setdefault(r["agent"], {})
            d[fam["id"]] = d.get(fam["id"], 0) + r["n"]
    by_id = {fid: {"id": fid, "label": label, "glyph": glyph, "color": color} for _, fid, label, glyph, color in FAMILIES}
    out = {}
    for name, spec in specs.items():
        fam = None
        if counts.get(name):
            fam = by_id[max(counts[name], key=counts[name].get)]
        else:
            for g in list(spec.mcp) + list(spec.builtin):
                fam = family_of(GROUP_KEY.get(g, g))
                if fam:
                    break
        out[name] = fam or {"id": "chat", "label": "Conversazione", "glyph": "💬", "color": "#4700E0"}
    return out


def agents_docs(ev: Events | None = None) -> dict[str, dict]:
    out = {}
    specs = registry.load_all()
    tops = top_tools(ev, specs) if ev else {}
    stats = ev.agent_stats() if ev and hasattr(ev, "agent_stats") else {}
    tot_tokens = sum(s.get("tokensUsed", 0) for s in stats.values()) or 0
    for i, (name, spec) in enumerate(sorted(specs.items(), key=lambda kv: (kv[0] != "coo", kv[0]))):
        st = stats.get(name, {})
        out[name] = {"name": name.replace("-", " ").title().replace("Coo", "COO").replace("Crm", "CRM"), "rol": name, "role": spec.description.split(".")[0][:60],
                     "bio": spec.description, "mission": (spec.prompt or "")[:500], "photo": "", "look": {}, "active": True, "order": i,
                     "breakUntil": None, "breakType": None, **({"kind": "coordinador"} if name == "coo" else {}),
                     "specHash": hashlib.sha1(f"{spec.description}|{spec.prompt}".encode()).hexdigest()[:10],
                     "model": spec.model, "autonomy": spec.autonomy,
                     "toolsList": list(spec.mcp) + list(spec.builtin), "skillsList": list(spec.skills),
                     "tokensUsed": st.get("tokensUsed", 0), "actionsCount": st.get("actionsCount", 0),
                     "costUsd": st.get("costUsd", 0.0), "lastActiveTs": st.get("lastActiveTs", 0), "teamTotalTokens": tot_tokens,
                     **({"topTool": tops[name]} if name in tops else {})}
    return out


def live_docs(active: dict[str, dict], names: dict[str, str]) -> dict[str, dict]:
    """Chi sta lavorando adesso: una scheda "in corso" per agente, cosi' l'ufficio lo porta alla scrivania. Sparisce da sola quando l'agente si ferma."""
    day = time.strftime("%Y-%m-%d")
    out = {}
    for agent, e in active.items():
        if agent not in names:
            continue
        fam = family_of(e.get("tool") or "")
        what = fam["label"] if fam else ("una conversazione" if e.get("kind") == "chat" else "una richiesta")
        out[f"live-{agent}"] = {"tarea": f"{names[agent]} sta lavorando: {what}", "descripcion": "Attivita' in corso, letta in tempo reale dal registro degli eventi.",
                                "estado": "en_progreso", "asignado": agent, "asignadoAgente": True, "cliente": "", "prioridad": "media", "fechaVencimiento": "",
                                "fechaRequerimiento": day, "links": "", "linkTerminada": "", "feedback": "", "totalTimeSeconds": 0,
                                "createdBy": "hlab", "creadoPor": "Sistema hlab", "hlab": {"kind": "live", "id": agent}}
    return out


def live_sync_once(store: Store, ev: Events | None = None, now: float | None = None) -> int:
    """Allinea le schede "in corso" a cio' che gli agenti stanno facendo ora (poche righe di SQLite: gira ogni pochi secondi)."""
    ev = ev or Events()
    names = {n: n.replace("-", " ").title().replace("Coo", "COO").replace("Crm", "CRM") for n in registry.load_all()}
    docs = live_docs(ev.active_since(ACTIVE_S, now), names)
    for tid, d in docs.items():
        old = store.get(f"tasks/{tid}") or {}
        if any(old.get(k) != v for k, v in d.items()):                 # niente scritture inutili: ogni scrittura sveglia tutti gli ascoltatori
            store.set(f"tasks/{tid}", d, merge=True)
    busy = ", ".join(sorted(names[a] for a in docs_agents)) if (docs_agents := [d["asignado"] for d in docs.values()]) else ""
    if (store.get("config/agentWorker") or {}).get("busyWith", "") != busy:   # la scritta "Motore degli agenti: sta lavorando su..." dell'ufficio
        store.set("config/agentWorker", {"busyWith": busy}, merge=True)
    for r in store.query("tasks"):
        if (r["data"].get("hlab") or {}).get("kind") == "live" and r["id"] not in docs:
            store.delete(f"tasks/{r['id']}")
    return len(docs)


_svc_cache: tuple[float, dict] = (0.0, {})
SERVICES_EVERY_S = 60                                               # lo stato dei servizi cambia di rado: 1 controllo al minuto invece di 5 processi ogni 20 secondi


def service_states() -> dict[str, bool]:
    global _svc_cache
    if not shutil.which("systemctl"):
        return {}
    if time.time() - _svc_cache[0] < SERVICES_EVERY_S:
        return _svc_cache[1]
    res = {}
    for s in SERVICES:
        try:
            res[s] = subprocess.run(["systemctl", "is-active", s], capture_output=True, text=True, timeout=5).stdout.strip() == "active"
        except Exception:
            res[s] = False
    _svc_cache = (time.time(), res)
    return res


def task_docs(ev: Events) -> dict[str, dict]:
    out = {}
    day = time.strftime("%Y-%m-%d")
    base = {"cliente": "", "prioridad": "media", "fechaVencimiento": "", "fechaRequerimiento": day, "asignadoAgente": True, "links": "",
            "linkTerminada": "", "feedback": "", "totalTimeSeconds": 0, "createdBy": "hlab", "creadoPor": "Sistema hlab"}
    for status in ("pending", "accepted", "applied", "rejected"):
        for p in ev.proposals(status):
            estado = {"pending": "en_espera", "accepted": "en_progreso"}.get(status, "completada")
            crit = status == "pending" and p["entity"] == "user"
            out[f"p{p['id']}"] = {**base, "tarea": f"Cambiare {FIELD.get(p['field'], p['field'])} di {ENTITY.get(p['entity'], p['entity'])}: {str(p['new_value'])[:50] or 'vuoto'}",
                                  "descripcion": f"{p['reason']}\n\nCampo: {p['field']}\nNuovo valore: {p['new_value']}\nBersaglio: {p['entity']} {p['entity_id']}",
                                  "estado": estado, "asignado": p["agent"], "prioridad": "urgente" if crit else "media",
                                  "feedback": "Annullata" if status == "rejected" else "", "createdAt": _ms(p["ts"]), "hlab": {"kind": "proposal", "id": p["id"]}}
    for r in ev.rule_rows():                                          # le regole proposte dal Memory Keeper: si approvano spostando la scheda, come il resto
        if r["status"] not in ("proposed", "active", "rejected"):
            continue
        estado = "en_espera" if r["status"] == "proposed" else "completada"
        out[f"r{r['id']}"] = {**base, "tarea": f"Regola per {r['agent']}: {r['rule'][:70]}", "asignado": r["agent"], "estado": estado,
                              "descripcion": f"{r['rule']}\n\nProposta dal Memory Keeper dai feedback {r['evidence']}. Vale dopo la tua approvazione e si aggiunge al prompt di {r['agent']}.",
                              "feedback": "Respinta" if r["status"] == "rejected" else ("Attiva" if r["status"] == "active" else ""),
                              "createdAt": _ms(r["ts"]), "hlab": {"kind": "rule", "id": r["id"]}}
    for c in powers.listing(ev):
        estado = "en_espera" if c["status"] == "proposed" else "completada"
        link = c["result"].get("pr") or c["result"].get("anteprima") or ""
        out[f"c{c['id']}"] = {**base, "tarea": f"{'PR' if c['kind'] == 'github_pr' else 'Pubblicazione sito' if c['kind'] == 'site_publish' else 'Anteprima'} su {c['target']}: {c['title'][:60]}",
                              "descripcion": c["title"], "estado": estado, "asignado": c["agent"], "linkTerminada": link, "links": link,
                              "feedback": "Rifiutata" if c["status"] == "rejected" else "", "createdAt": _ms(c["ts"]), "hlab": {"kind": "change", "id": c["id"]}}
    return out


def sync_once(store: Store, ev: Events | None = None, services: dict | None = None) -> dict:
    ev = ev or Events()
    ag = agents_docs(ev)
    live_keys = ("topTool", "tokensUsed", "actionsCount", "costUsd", "lastActiveTs", "teamTotalTokens", "toolsList", "skillsList", "model", "autonomy")
    for aid, d in ag.items():
        old = store.get(f"agents/{aid}")
        if old is None or old.get("specHash") != d["specHash"]:        # nuovo, o il file dell'agente e' cambiato: si riparte dal file
            store.set(f"agents/{aid}", {**d, "createdAt": {"__op": "serverTimestamp"}}, merge=True)
        elif any(old.get(k) != d.get(k) for k in live_keys):           # altrimenti restano le modifiche fatte dalla GUI (riserva, foto, avatar, nome) ma si aggiornano metriche e strumenti reali
            store.update(f"agents/{aid}", {k: d[k] for k in live_keys if k in d})
    svc = service_states() if services is None else services
    store.set("config/agentWorker", {"online": True, "lastSeen": {"__op": "serverTimestamp"}, "host": platform.node(), "services": svc}, merge=True)
    store.set("users/lorenzo", {"name": "Lorenzo", "email": "admin@agency-ai.local", "role": "admin", "color": "#4700E0", "photo": "",
                                "lastSeen": {"__op": "serverTimestamp"}}, merge=True)
    tasks = task_docs(ev)
    for tid, d in tasks.items():
        if not d.get("feedback"):                                      # non cancella l'esito scritto dopo una decisione dall'app
            d = {k: v for k, v in d.items() if k != "feedback"}
        store.set(f"tasks/{tid}", d, merge=True)                       # merge: tempi e note aggiunti dall'app restano
    return {"agenti": len(ag), "task": len(tasks)}


def crm_docs(clients: list[dict], deadlines: list[dict], stale: list[dict], today: str | None = None) -> tuple[list[str], dict[str, dict]]:
    """Dal CRM (sola lettura) alla GUI: l'elenco clienti e le scadenze / i progetti fermi come schede "Da fare" in sola informazione."""
    import hashlib
    day = today or time.strftime("%Y-%m-%d")
    names = sorted({c["name"] for c in clients if c.get("name")})
    base = {"asignadoAgente": True, "links": "", "linkTerminada": "", "feedback": "", "totalTimeSeconds": 0, "createdBy": "hlab", "creadoPor": "Sistema hlab",
            "fechaRequerimiento": day, "estado": "pendiente"}
    out: dict[str, dict] = {}

    def tid(*parts) -> str:
        return "d" + hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]

    for d in deadlines:
        left = (time.mktime(time.strptime(str(d.get("due"))[:10], "%Y-%m-%d")) - time.mktime(time.strptime(day, "%Y-%m-%d"))) / 86400 if d.get("due") else 99
        out[tid("dl", d.get("kind"), d.get("title"), d.get("due"))] = {
            **base, "tarea": f"Scadenza {str(d.get('due'))[:10]}: {d.get('title')}", "cliente": d.get("client") or "", "fechaVencimiento": str(d.get("due"))[:10],
            "descripcion": f"{str(d.get('kind') or '').capitalize()} in stato {d.get('status') or '?'}, cliente {d.get('client') or '-'}. Da seguire prima della scadenza.",
            "prioridad": "urgente" if left <= 3 else "alta" if left <= 7 else "media", "asignado": "project-manager", "hlab": {"kind": "deadline"}}
    for p in stale:
        out[tid("st", p.get("id") or p.get("name") or p.get("title"))] = {
            **base, "tarea": f"Progetto fermo: {p.get('name') or p.get('title') or p.get('id')}", "cliente": p.get("client") or "", "prioridad": "alta",
            "descripcion": "Nessun aggiornamento da tempo: controlla se va avanti o se va archiviato.", "asignado": "project-manager", "hlab": {"kind": "stale"}}
    return names, out


def crm_sync_once(store: Store, lean=None) -> dict:
    """Interroga il CRM e allinea clienti e schede informative. Chiamata in un thread: il CRM e' una connessione bloccante."""
    from .lean import Lean
    lean = lean or Lean()

    async def go():
        return (await lean._call("list_clients", limit=200), await lean._call("list_deadlines", days=30), await lean._call("stale_projects", days=14))
    clients, deadlines, stale = asyncio.run(go())
    names, docs = crm_docs(clients, deadlines, stale)
    store.set("config/clients", {"list": names}, merge=True)
    for tid, d in docs.items():
        old = store.get(f"tasks/{tid}")
        if not old or not old.get("createdAt"):
            d = {**d, "createdAt": _ms(time.time())}
        store.set(f"tasks/{tid}", d, merge=True)
    for r in store.query("tasks"):                                     # le scadenze passate o i progetti ripartiti spariscono dalla pipeline
        if (r["data"].get("hlab") or {}).get("kind") in ("deadline", "stale") and r["id"] not in docs:
            store.delete(f"tasks/{r['id']}")
    return {"clienti": len(names), "schede": len(docs)}


def create_agent_file(store: Store, path: str, data: dict, agents_dir=None) -> str | None:
    """"Nuovo agente" dalla GUI: diventa un vero agente del registro (un file in agents/), in riserva finche' non lo attivi.
    Il file si controlla prima di metterlo al suo posto: uno sbagliato romperebbe il caricamento di tutti gli agenti. Ritorna il nome o None."""
    import os
    import re
    import tempfile

    import yaml

    from . import config
    d = agents_dir or config.AGENTS_DIR
    shown = (data.get("name") or "").strip()
    base = re.sub(r"[^a-z0-9]+", "-", shown.lower()).strip("-")[:28] or "agente"
    if not re.match(r"^[a-z]", base):
        base = "a-" + base
    taken = {p.stem for p in d.glob("*.md")} | set(registry.load_all(d))
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    desc = (data.get("bio") or data.get("role") or "").strip()
    if len(desc) < 20:
        desc = f"{shown}. {data.get('role', '')}. Agente creato dalla console.".strip()
    prompt = (data.get("mission") or "").strip()
    if len(prompt) < 40:
        prompt = f"Sei {shown}, {data.get('role', 'un agente della squadra')}. Rispondi sempre in italiano, in modo breve e concreto. {prompt}".strip()
    head = yaml.safe_dump({"name": name, "description": desc, "model": "sonnet", "autonomy": 1, "max_turns": 12}, allow_unicode=True, sort_keys=False)
    fd, tmp = tempfile.mkstemp(suffix=".md", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"---\n{head}---\n{prompt}\n")
        spec = registry.parse(Path(tmp))
        if spec.problems():                                          # non e' un agente valido: non entra nel registro
            os.unlink(tmp)
            return None
        os.replace(tmp, d / f"{name}.md")
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    store.delete(path)                                               # la scheda con id casuale della GUI lascia il posto a quella con il nome vero
    store.set(f"agents/{name}", {**data, "rol": name, "active": False, "specHash": hashlib.sha1(f"{spec.description}|{spec.prompt}".encode()).hexdigest()[:10]})
    return name


def attach_agent_files(store: Store) -> None:
    def hook(path: str, before: dict | None, after: dict | None) -> None:
        if before is not None or not after or not path.startswith("agents/") or "/" in path[7:] or after.get("specHash"):
            return
        threading.Thread(target=lambda: _safe(create_agent_file, store, path, after), daemon=True).start()
    store.hooks.append(hook)


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception as e:
        print("agent_file:", type(e).__name__, e, flush=True)


def attach_hooks(store: Store, decide_fn=None, resync=None) -> None:
    """Spostare in app una carta da "Espera Aprobación" a "Completada" la conferma, a "Correcciones" la annulla: stessa decisione dei pulsanti di Telegram.
    Parte in un thread (la conferma puo' chiamare il CRM) e poi riallinea. Un secondo spostamento non rifa nulla: `decide` ignora cio' che e' gia' deciso."""
    from . import decide as _decide
    run = decide_fn or _decide.decide

    def work(path: str, kind: str, verdict: str, ident: int) -> None:
        try:
            msg = _decide_rule(verdict == "ok", ident) if kind == "hr" else run(kind, verdict, ident)
        except Exception as e:
            msg = f"⚠️ Non eseguita: {type(e).__name__}: {str(e)[:160]}"
        store.update(path, {"feedback": msg})
        (resync or (lambda: sync_once(store)))()

    def hook(path: str, before: dict | None, after: dict | None) -> None:
        if not (path.startswith("tasks/") and before and after and after.get("hlab")):
            return
        if before.get("estado") != "en_espera" or after.get("estado") == "en_espera":
            return
        verdict = {"completada": "ok", "en_revision": "no"}.get(after.get("estado"))
        h = after["hlab"]
        if verdict and h.get("kind") in ("proposal", "change", "rule"):
            code = {"proposal": "hp", "change": "hc", "rule": "hr"}[h["kind"]]
            threading.Thread(target=work, args=(path, code, verdict, int(h["id"])), daemon=True).start()
    store.hooks.append(hook)


def _decide_rule(approve: bool, rid: int) -> str:
    """Approvare o respingere una regola proposta dal Memory Keeper: da attiva entra nel prompt di quell'agente (vedi learning.py)."""
    from .learning import Learning
    lr = Learning()
    try:
        if approve:
            lr.approve(rid)
            return "✅ Regola attivata: da ora l'agente la segue."
        lr.reject_rule(rid)
        return "Regola respinta."
    except ValueError as e:
        return f"⚠️ Non eseguita: {e}"


def attach_feedback_hooks(store: Store, learning=None) -> None:
    """Il tuo giudizio su un task consegnato da un agente diventa feedback: accettato (Completato) o rimandato (Correzioni, col tuo commento).
    E' il materiale da cui il Memory Keeper ricava le regole: senza questo, dalla GUI non si impara nulla."""
    names = None

    def hook(path: str, before: dict | None, after: dict | None) -> None:
        nonlocal names
        if not (path.startswith("tasks/") and before and after) or after.get("hlab") or after.get("agentStatus") != "done":
            return
        if before.get("estado") != "en_espera" or after.get("estado") not in ("completada", "en_revision"):
            return
        names = names or set(registry.load_all())
        if after.get("asignado") not in names:
            return
        from .learning import Learning
        verdict = "accepted" if after["estado"] == "completada" else "corrected"
        try:
            (learning or Learning()).record(after["asignado"], "task", path.split("/", 1)[1], verdict, after.get("feedback") or "")
        except Exception as e:
            print("feedback:", type(e).__name__, e, flush=True)
    store.hooks.append(hook)


def start(store: Store):
    """Funzione di avvio per Starlette: la GUI è di sola telemetria/lettura; gli ordini agli agenti arrivano esclusivamente da Telegram."""
    async def on_start():
        store.bind_loop(asyncio.get_running_loop())
        sync_once(store)

        async def crm_loop():
            while True:
                try:
                    await asyncio.to_thread(crm_sync_once, store)
                except Exception as e:                                 # CRM irraggiungibile: la GUI tiene i dati di prima
                    print("crm_sync:", type(e).__name__, e, flush=True)
                await asyncio.sleep(CRM_EVERY_S)
        asyncio.get_event_loop().create_task(crm_loop())

        async def system_loop():                                       # pagina Sistema: servizi, piani, limiti, esecuzioni e token (hlab.services)
            from . import services
            while True:
                try:
                    await asyncio.to_thread(services.publish, store)
                except Exception as e:                                 # una sonda rotta non ferma l'API: la pagina tiene i dati di prima
                    print("system_sync:", type(e).__name__, e, flush=True)
                await asyncio.sleep(SYSTEM_EVERY_S)
        asyncio.get_event_loop().create_task(system_loop())

        async def loop():
            while True:
                await asyncio.sleep(SYNC_EVERY_S)
                try:
                    sync_once(store)
                except Exception as e:                                     # un giro fallito non ferma l'API
                    print("api_sync:", type(e).__name__, e, flush=True)
        asyncio.get_event_loop().create_task(loop())

        async def live_loop():                                         # l'ufficio segue il lavoro vero degli agenti con pochi secondi di ritardo
            while True:
                await asyncio.sleep(LIVE_EVERY_S)
                try:
                    live_sync_once(store)
                except Exception as e:
                    print("live_sync:", type(e).__name__, e, flush=True)
        asyncio.get_event_loop().create_task(live_loop())
    return on_start
