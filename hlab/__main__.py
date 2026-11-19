"""CLI: python -m hlab <comando>. Comandi senza rete: agents, check, mem, approvals, approve, deny, events."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

from . import config, registry, tools
from .events import Events


def cmd_agents(a):
    specs = registry.load_all()
    print(f"{'agente':16s} {'modello':8s} aut  strumenti  skills                       memoria")
    for s in specs.values():
        mem = ("privata" if s.memory_private else "-") + f" + condivisa:{s.memory_shared}"
        print(f"{s.name:16s} {s.model:8s} {s.autonomy:^3d}  {len(tools.tool_names(s)):^9d}  {','.join(s.skills) or '-':28s} {mem}")
        if a.verbose:
            print(f"   server: {', '.join(tools.server_names(s)) or '-'}")
            for t in tools.tool_names(s):
                print(f"   - {t}  [{tools.risk_of(t)}]")
    errs = registry.validate(specs)
    print("\nregistro:", "OK" if not errs else "\n  ".join(["PROBLEMI"] + errs))
    return 1 if errs else 0


def cmd_check(a):
    ok = True
    specs = registry.load_all()
    errs = registry.validate(specs)
    print(f"[registro]  {len(specs)} agenti, {'OK' if not errs else errs}")
    ok &= not errs
    from .embed import get_embedder
    from .memory import MemoryStore
    t0 = time.time()
    emb = get_embedder()
    dim = emb.dim
    print(f"[embedding] {emb.name} dim={dim} caricato in {time.time() - t0:.1f}s")
    old = config.MEMORY_DIR
    with tempfile.TemporaryDirectory() as d:
        config.MEMORY_DIR = Path(d)
        try:
            m = MemoryStore("check")
            m.write("/clienti/prova.md", "Il cliente Prova ha pagato l'acconto del quaranta per cento.", ["prova"])
            hit = m.search("chi ha versato l'anticipo?", k=1)
            good = bool(hit) and hit[0]["path"] == "/clienti/prova.md"
            print(f"[memoria]   scrittura + ricerca ibrida: {'OK' if good else 'FALLITA'}")
            ok &= good
        finally:
            config.MEMORY_DIR = old
    from .runner import build_options
    o = build_options(specs, crm_db=_NoDb())
    print(f"[sdk]       opzioni costruite: {len(o.agents)} subagenti, server {sorted(o.mcp_servers)}")
    return 0 if ok else 1


class _NoDb:
    def query(self, *_a, **_k):
        raise RuntimeError("database non collegato in modalita' check")


def cmd_mem(a):
    from .memory import MemoryStore
    s = MemoryStore(a.store)
    if a.op == "list":
        for r in s.list(a.arg or "/"):
            print(f"{r['path']:40s} {r['head']}")
    elif a.op == "search":
        for h in s.search(a.arg, k=5):
            print(f"{h['score']:.4f} {h['path']}  {h['text'][:100]!r}")
    elif a.op == "add":
        path, _, text = a.arg.partition("=")
        print(s.write(path, text, author="cli"))
    elif a.op == "history":
        for v in s.history(a.arg):
            print(v["version"], v["op"], v["author"], time.strftime("%F %T", time.localtime(v["ts"])))
    elif a.op == "stats":
        print(json.dumps(s.stats(), indent=1))
    return 0


def cmd_approvals(a):
    for r in Events().pending():
        print(f"#{r['id']}  {r['agent']}  {r['tool']}  [{r['risk']}]  {r['args'][:120]}")
    return 0


def cmd_proposals(a):
    rows = Events().proposals(a.status)
    for r in rows:
        print(f"#{r['id']}  {r['agent']}  {r['entity']} {r['entity_id'][:8]}…  {r['field']} -> {r['new_value']!r}\n      motivo: {r['reason']}")
    print(f"{len(rows)} proposte ({a.status})")
    return 0


def _feedback(agent: str, source: str, sid, verdict: str, why: str) -> None:
    from .learning import Learning
    fid = Learning().record(agent, source, sid, verdict, why or "")
    print(f"feedback #{fid} registrato" + ("" if why else " (senza motivo: il Memory Keeper non potra' ricavarne una regola; usa --why)"))


def cmd_decide_proposal(a, accept: bool):
    ev = Events()
    p = ev.get_proposal(int(a.id))
    done = ev.decide_proposal(int(a.id), accept)
    print(f"fatto (il CRM non e' stato modificato: per applicarla `hlab apply {a.id}`)" if done and accept else "fatto" if done else "non trovata o gia' decisa")
    if done and p:
        _feedback(p["agent"], "proposal", a.id, "accepted" if accept else "rejected", a.why)
    return 0


def cmd_apply(a):
    from . import crm_write
    try:
        print(json.dumps(crm_write.apply(Events(), int(a.id)), ensure_ascii=False))
    except (ValueError, KeyError, crm_write.httpx.HTTPError) as e:
        print(f"non applicata: {type(e).__name__}: {e}")
        return 1
    return 0


def cmd_drafts(a):
    from . import commercial as co
    rows = Events().drafts(a.status)
    for d in rows:
        q = d["payload"]["quote"]
        print(f"#{d['id']}  {d['agent']}  {d['kind']}  {d['client']}  {co.money(q['one_off_cents'], d['currency'])}"
              + (f" + {co.money(q['monthly_cents'], d['currency'])}/mese" if q["monthly_cents"] else ""))
        for w in d["payload"]["warnings"]:
            print(f"      ! {w}")
    print(f"{len(rows)} bozze ({a.status})")
    return 0


def cmd_draft(a):
    from . import commercial as co
    d = Events().get_draft(int(a.id))
    if not d:
        print("bozza non trovata")
        return 1
    p, q = d["payload"], d["payload"]["quote"]
    print(f"Bozza #{d['id']} — {d['kind']} per {d['client']} — {d['currency']} — stato {d['status']}")
    for i in p["items"]:
        print(f"  {i['name']:40s} {co.money(int(i['price_cents']), d['currency'])}{' /mese' if i['monthly'] else ''}")
    for t in q["tranches"]:
        print(f"  {t['tranche']:8s} {co.money(t['total_cents'], d['currency'])} (compenso {co.money(t['net_cents'], d['currency'])}, bollo {co.money(t['stamp_duty_cents'], d['currency'])})")
    for w in p["warnings"]:
        print(f"  ! {w}")
    if d["notes"]:
        print(f"  note: {d['notes']}")
    return 0


def cmd_decide_draft(a, approve: bool):
    ev = Events()
    d = ev.get_draft(int(a.id))
    done = ev.decide_draft(int(a.id), approve)
    print("fatto (la bozza resta in locale: non e' stata inviata ne' applicata al CRM)" if done else "non trovata o gia' decisa")
    if done and d:
        _feedback(d["agent"], "draft", a.id, "accepted" if approve else "rejected", a.why)
    return 0


def cmd_feedback(a):
    for r in Events().feedback_rows(a.agent or None, processed=a.all):
        print(f"#{r['id']}  {r['agent']}  {r['verdict']:9s} {r['source']}#{r['source_id']}  {r['comment'] or '(senza commento)'}")
    return 0


def cmd_feedback_add(a):
    _feedback(a.agent, "manual", None, a.verdict, " ".join(a.text))
    return 0


def cmd_rules(a):
    for r in Events().rule_rows(None, a.status):
        print(f"#{r['id']}  [{r['status']}]  {r['agent']}: {r['rule']}   (prove: {r['evidence']})")
    return 0


def cmd_rule(a, action: str):
    from .learning import Learning
    lr = Learning()
    try:
        ok = {"approve": lambda: lr.approve(int(a.id)) == "active", "retire": lambda: lr.retire(int(a.id)), "reject": lambda: lr.reject_rule(int(a.id))}[action]()
    except ValueError as e:
        print(f"non fatto: {e}")
        return 1
    print("fatto" if ok else "regola non trovata")
    return 0


def cmd_graph(a):
    from .graph import Graph
    from .memory import MemoryStore
    g = Graph()
    if a.op == "build":
        stores = [MemoryStore(config.SHARED_STORE, readonly=True)] + [MemoryStore(n.replace("-", "_"), readonly=True) for n in registry.load_all()]
        print(g.rebuild(stores))
    elif a.op == "stats":
        print(g.stats())
    else:
        for r in g.related(a.arg, a.depth):
            print(f"{r['peso']:<5} {r['percorso']}   [{r['prova']}]")
    return 0


def cmd_jobs(a):
    from .durable import Durable
    for j in Durable().list(a.status):
        print(f"#{j['id']}  {j['kind']:10s} {j['status']:9s} tentativi {j['attempts']}/{j['max_attempts']}  {j['error'] or ''}")
    return 0


def cmd_submit(a):
    from .durable import Durable
    print("accodato: #" + str(Durable().submit(a.kind, json.loads(a.payload or "{}"), key=a.key)))
    return 0


def cmd_worker(a):
    import asyncio

    from .durable import Durable, worker_loop
    from .workflows import HANDLERS
    print("worker attivo (Ctrl+C per fermare): riprende da solo i lavori interrotti")
    try:
        asyncio.run(worker_loop(Durable(), HANDLERS))
    except KeyboardInterrupt:
        pass
    return 0


def cmd_decide(a, approve: bool):
    print("fatto" if Events().decide(int(a.id), approve) else "non trovata o gia' decisa")
    return 0


def cmd_events(a):
    for e in reversed(Events().recent(a.n)):
        print(time.strftime("%F %T", time.localtime(e["ts"])), e["kind"], e["agent"] or "", e["tool"] or "", e["detail"] or "")
    return 0


def cmd_router(a):
    from .router import serve
    serve()
    return 0


def cmd_router_status(a):
    import httpx
    from .router import load_config
    r = httpx.get(f"http://127.0.0.1:{load_config()['listen']['port']}/health", timeout=5).json()
    for pool, ms in r["pools"].items():
        print(f"pool {pool}:")
        for m in ms:
            s = r["stats"].get(m["model"], {})
            print(f"  {m['model']:50s} {m['tier']:4s} pausa {m['cooldown_s']:>4d}s  ok {s.get('ok', 0):>3d}  errori {s.get('fail', 0):>3d}  "
                  f"ultimo {s.get('last_status')}  {s.get('usd', 0):.4f} $")
    print(f"oggi: {r['today']['n']} richieste gratuite | spesa {r['spend']['usd']:.4f} $ su {r['spend']['cap']} $")
    print(f"gratuiti: {'aperti' if r['free']['open'] else 'CHIUSI - ' + r['free']['why']} | a pagamento: {'aperti' if r['paid']['open'] else 'CHIUSI - ' + r['paid']['why']}")
    return 0


def cmd_ask(a):
    import asyncio

    from .lean import CAPS, Lean, detect
    text = " ".join(a.text)
    if a.rules_only and (CAPS.get(detect(text)[0]) != 0):        # solo i percorsi senza modello: altrimenti esce 3 e risponde chi chiama
        return 3
    r = asyncio.run(Lean().ask(text))
    if a.rules_only:                                              # per Hermes: l'intento serve a decidere come presentare la risposta
        print(json.dumps({"intent": r.intent, "text": r.text}, ensure_ascii=False))
        return 0
    print(r.text)
    print(f"\n[{r.intent} | {r.tokens} token, {r.calls} chiamate al modello]")
    return 0


def cmd_api(a):
    """Server dell'app web/Flutter: archivio, tempo reale, file statici. Si lega a 127.0.0.1: per il telefono serve una rete privata o un tunnel."""
    from . import api
    api.load_token(create=True)
    print(f"API su http://{a.host}:{a.port} (token: `hlab api-token`)")
    api.serve(a.host, a.port)
    return 0


def cmd_api_token(a):
    """Mostra il token personale dell'app (solo a te, nel tuo terminale). --new lo rigenera: i telefoni gia' collegati dovranno rientrare."""
    from . import api
    if a.new and api.token_path().exists():
        api.token_path().unlink()
    print(api.load_token(create=True))
    return 0


def cmd_decide(a):
    """Un tocco su un pulsante di Telegram: hp|hc ok|no <id>. Stampa la frase da mostrare a Lorenzo."""
    from . import decide
    print(decide.decide(a.kind, a.verdict, a.id))
    return 0


def cmd_uptime(a):
    """Controlla adesso i siti monitorati e stampa lo stato (aggiorna lo stato, non manda avvisi)."""
    import asyncio

    from . import uptime
    alerts, st = asyncio.run(uptime.run())
    print(uptime.summary(st))
    for urgent, t in alerts:
        print(("\n! " if urgent else "\n- ") + t)
    return 0


def cmd_sentinel(a):
    from . import sentinel
    return sentinel.main([a.action] + (["--dry"] if a.dry else []))


def cmd_notify(a):
    import asyncio

    from . import notify
    print(json.dumps(asyncio.run(notify.run(dry=a.dry)), ensure_ascii=False))
    return 0


def cmd_heal(a):
    """Corregge un sito generato: scan nel browser, patch con modello, tiene solo cio' che migliora. Riscrive i file nella cartella e stampa un JSON."""
    import asyncio
    import filecmp
    import shutil
    from pathlib import Path

    from . import factory_heal
    site = Path(a.dir)
    res = asyncio.run(factory_heal.heal(site, factory_heal.llm_patcher(), max_attempts=a.attempts))
    work, changed = Path(res.pop("work_dir")), []
    for f in sorted(work.rglob("*")):
        rel = f.relative_to(work)
        if f.is_file() and "_shots" not in rel.parts and (site / rel).exists() and not filecmp.cmp(f, site / rel, shallow=False):
            shutil.copy2(f, site / rel)
            changed.append(rel.as_posix())
    shutil.rmtree(work.parent, ignore_errors=True)
    res.pop("diff", None)
    print(json.dumps({**res, "file_cambiati": changed}, ensure_ascii=False))
    return 0


def cmd_tokens(a):
    import sqlite3
    c = sqlite3.connect(config.EVENTS_DB)
    print(f"{'giorno':11s} {'percorso':12s} richieste  token")
    for day, tool, n, tk in c.execute("select date(ts,'unixepoch'), tool, count(*), coalesce(sum(tokens),0) from events where kind='lean' "
                                      "group by 1,2 order by 1 desc, 4 desc limit 30"):
        print(f"{day:11s} {tool:12s} {n:>9d} {tk:>6d}")
    return 0


def cmd_cred(a):
    import getpass

    from .vault import Vault
    v = Vault()
    if a.op == "list":
        for c in v.list(include_deleted=a.all):
            print(f"{c['name']:22s} {c['service']:8s} usi {c['uses']:>3d}  {c['scope'] or '-':30s}" + ("  [cancellata, recuperabile]" if c["deleted_at"] else ""))
    elif a.op == "add":
        secret = getpass.getpass(f"valore della credenziale '{a.name}' (non si vede mentre scrivi): ")
        v.add(a.name, a.service, secret.strip(), a.scope or "")
        print("inserita (cifrata)")
    elif a.op == "reveal":
        print(v.reveal(a.name))
    elif a.op == "delete":
        print("cancellata (recuperabile 30 giorni con `cred restore`)" if v.delete(a.name) else "non trovata")
    elif a.op == "restore":
        print("ripristinata" if v.restore(a.name) else "niente da ripristinare")
    elif a.op == "purge":
        print(f"eliminate definitivamente: {v.purge(a.name or None)}")
    elif a.op == "check":
        import httpx
        meta = next((x for x in v.list() if x["name"] == a.name), None)
        if not meta:
            print("credenziale non trovata")
            return 1
        tok = v.use(a.name, "verifica (sola lettura)", who="cli")
        try:
            if meta["service"] == "github":
                u = httpx.get("https://api.github.com/user", headers={"authorization": f"Bearer {tok}", "accept": "application/vnd.github+json"}, timeout=20)
                rl = httpx.get("https://api.github.com/rate_limit", headers={"authorization": f"Bearer {tok}"}, timeout=20).json()["resources"]["core"]
                print(f"GitHub: HTTP {u.status_code}" + (f", utente {u.json().get('login')}, richieste API restanti {rl['remaining']}/{rl['limit']}" if u.status_code == 200 else " (token non valido o scaduto)"))
            elif meta["service"] == "netlify":
                u = httpx.get("https://api.netlify.com/api/v1/user", headers={"authorization": f"Bearer {tok}"}, timeout=20)
                sites = httpx.get("https://api.netlify.com/api/v1/sites", params={"per_page": 100}, headers={"authorization": f"Bearer {tok}"}, timeout=20)
                print(f"Netlify: HTTP {u.status_code}" + (f", siti visibili: {', '.join(x['name'] for x in sites.json())[:300]}" if u.status_code == 200 and sites.status_code == 200 else " (token non valido o scaduto)"))
            else:
                print("nessun controllo automatico per questo servizio")
        except httpx.HTTPError as e:
            print(f"rete: {type(e).__name__}")
            return 1
    elif a.op == "log":
        for r in v.log(20):
            print(time.strftime("%F %T", time.localtime(r["ts"])), r["action"], r["name"], r["who"], r["purpose"])
    return 0


def cmd_changes(a):
    from . import powers
    for c in powers.listing(Events(), a.status):
        extra = c["result"].get("pr") or c["result"].get("anteprima") or ""
        print(f"#{c['id']}  {c['status']:10s} {c['kind']:16s} {c['target']:28s} {c['title'][:50]}  {extra}")
    return 0


def cmd_change(a):
    from . import powers
    ch = powers.get(Events(), int(a.id))
    if not ch:
        print("non trovata")
        return 1
    print(f"#{ch['id']} {ch['kind']} su {ch['target']} — {ch['title']} — proposta da {ch['agent']} — stato {ch['status']}")
    for path, content in ch["payload"]["files"].items():
        print(f"  {path}  ({len(str(content))} caratteri)")
    print("quote:", json.dumps(powers.Powers().quota_report(), ensure_ascii=False))
    if ch["result"]:
        print("risultato:", ch["result"])
    return 0


def cmd_approve_change(a):
    from . import powers
    try:
        print(powers.Powers().approve(int(a.id), publish=a.publish))
    except (powers.QuotaError, ValueError, KeyError, powers.httpx.HTTPError) as e:
        print(f"non eseguita: {type(e).__name__}: {e}")
        return 1
    return 0


def cmd_reject_change(a):
    from . import powers
    ok = powers.reject(Events(), int(a.id), a.why)
    print("rifiutata" + ("" if a.why else " (senza motivo: non ne nascera' una regola; usa --why)") if ok else "non trovata o gia' decisa")
    return 0


def cmd_quota(a):
    from . import powers
    powers.netlify_plan(max_age=0)                                      # rilegge il piano vero da Netlify (se raggiungibile) prima del rapporto
    print(json.dumps(powers.Powers().quota_report(), ensure_ascii=False, indent=1))
    return 0


def cmd_run(a):
    from .runner import run
    r = asyncio.run(run(a.prompt, budget=a.budget))
    print(f"\n[{r.get('subtype')}] costo ${r.get('cost_usd') or 0:.4f}, {r.get('turns')} turni")
    return 0 if r.get("subtype") == "success" else 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="hlab")
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("agents"); s.add_argument("-v", "--verbose", action="store_true"); s.set_defaults(f=cmd_agents)
    sp.add_parser("check").set_defaults(f=cmd_check)
    s = sp.add_parser("mem"); s.add_argument("store"); s.add_argument("op", choices=["list", "search", "add", "history", "stats"])
    s.add_argument("arg", nargs="?", default=""); s.set_defaults(f=cmd_mem)
    sp.add_parser("approvals").set_defaults(f=cmd_approvals)
    s = sp.add_parser("approve"); s.add_argument("id"); s.set_defaults(f=lambda a: cmd_decide(a, True))
    s = sp.add_parser("deny"); s.add_argument("id"); s.set_defaults(f=lambda a: cmd_decide(a, False))
    s = sp.add_parser("proposals"); s.add_argument("status", nargs="?", default="pending"); s.set_defaults(f=cmd_proposals)
    s = sp.add_parser("apply"); s.add_argument("id"); s.set_defaults(f=cmd_apply)
    s = sp.add_parser("accept"); s.add_argument("id"); s.add_argument("--why", default=""); s.set_defaults(f=lambda a: cmd_decide_proposal(a, True))
    s = sp.add_parser("reject"); s.add_argument("id"); s.add_argument("--why", default=""); s.set_defaults(f=lambda a: cmd_decide_proposal(a, False))
    s = sp.add_parser("drafts"); s.add_argument("status", nargs="?", default="draft"); s.set_defaults(f=cmd_drafts)
    s = sp.add_parser("draft"); s.add_argument("id"); s.set_defaults(f=cmd_draft)
    s = sp.add_parser("approve-draft"); s.add_argument("id"); s.add_argument("--why", default=""); s.set_defaults(f=lambda a: cmd_decide_draft(a, True))
    s = sp.add_parser("reject-draft"); s.add_argument("id"); s.add_argument("--why", default=""); s.set_defaults(f=lambda a: cmd_decide_draft(a, False))
    s = sp.add_parser("events"); s.add_argument("n", nargs="?", type=int, default=20); s.set_defaults(f=cmd_events)
    s = sp.add_parser("feedback"); s.add_argument("agent", nargs="?"); s.add_argument("--all", action="store_true"); s.set_defaults(f=cmd_feedback)
    s = sp.add_parser("feedback-add"); s.add_argument("agent"); s.add_argument("verdict", choices=["rejected", "accepted", "corrected"])
    s.add_argument("text", nargs="+"); s.set_defaults(f=cmd_feedback_add)
    s = sp.add_parser("rules"); s.add_argument("status", nargs="?"); s.set_defaults(f=cmd_rules)
    for act in ("approve", "retire", "reject"):
        s = sp.add_parser("rule-" + act); s.add_argument("id"); s.set_defaults(f=(lambda act: lambda a: cmd_rule(a, act))(act))
    s = sp.add_parser("graph"); s.add_argument("op", choices=["build", "related", "stats"]); s.add_argument("arg", nargs="?", default="")
    s.add_argument("--depth", type=int, default=2); s.set_defaults(f=cmd_graph)
    s = sp.add_parser("jobs"); s.add_argument("status", nargs="?"); s.set_defaults(f=cmd_jobs)
    s = sp.add_parser("submit"); s.add_argument("kind"); s.add_argument("payload", nargs="?", default="{}"); s.add_argument("--key"); s.set_defaults(f=cmd_submit)
    sp.add_parser("worker").set_defaults(f=cmd_worker)
    s = sp.add_parser("cred"); s.add_argument("op", choices=["list", "add", "reveal", "delete", "restore", "purge", "log", "check"]); s.add_argument("name", nargs="?")
    s.add_argument("service", nargs="?", default="other"); s.add_argument("--scope", default=""); s.add_argument("--all", action="store_true"); s.set_defaults(f=cmd_cred)
    s = sp.add_parser("changes"); s.add_argument("status", nargs="?"); s.set_defaults(f=cmd_changes)
    s = sp.add_parser("change"); s.add_argument("id"); s.set_defaults(f=cmd_change)
    s = sp.add_parser("approve-change"); s.add_argument("id"); s.add_argument("--publish", action="store_true"); s.set_defaults(f=cmd_approve_change)
    s = sp.add_parser("reject-change"); s.add_argument("id"); s.add_argument("--why", default=""); s.set_defaults(f=cmd_reject_change)
    sp.add_parser("quota").set_defaults(f=cmd_quota)
    s = sp.add_parser("ask"); s.add_argument("text", nargs="+"); s.add_argument("--rules-only", action="store_true"); s.set_defaults(f=cmd_ask)
    sp.add_parser("uptime").set_defaults(f=cmd_uptime)
    s = sp.add_parser("api"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8770); s.set_defaults(f=cmd_api)
    s = sp.add_parser("api-token"); s.add_argument("--new", action="store_true"); s.set_defaults(f=cmd_api_token)
    s = sp.add_parser("decide"); s.add_argument("kind", choices=["hp", "hc"]); s.add_argument("verdict", choices=["ok", "no"]); s.add_argument("id", type=int); s.set_defaults(f=cmd_decide)
    s = sp.add_parser("sentinel"); s.add_argument("action", nargs="?", default="cycle", choices=["cycle", "review", "rollback", "status", "candidates"]); s.add_argument("--dry", action="store_true"); s.set_defaults(f=cmd_sentinel)
    s = sp.add_parser("notify"); s.add_argument("--dry", action="store_true"); s.set_defaults(f=cmd_notify)
    s = sp.add_parser("heal"); s.add_argument("dir"); s.add_argument("--attempts", type=int, default=2); s.set_defaults(f=cmd_heal)
    sp.add_parser("tokens").set_defaults(f=cmd_tokens)
    sp.add_parser("router").set_defaults(f=cmd_router)
    sp.add_parser("router-status").set_defaults(f=cmd_router_status)
    s = sp.add_parser("run"); s.add_argument("prompt"); s.add_argument("--budget", type=float, default=None); s.set_defaults(f=cmd_run)
    a = p.parse_args(argv)
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
