"""Spinge a Vita (sul Raspberry) un riepilogo in SOLA LETTURA del CRM: nomi, stati, scadenze, conteggi.

Perché «push» e non «pull»: Vita non deve avere le credenziali del database né un accesso a Oracle; è Oracle a bussare a Vita, su
`/v1/push/crm` di vita-ingest. Cosa NON parte mai: email, telefoni, partite IVA, indirizzi, note dei clienti, credenziali.
Parte (per scelta di Lorenzo, per rispondere sui conti dell'azienda): nomi, stati, scadenze, conteggi, importi per progetto e per valuta
(mai convertiti), costi di produzione per sito dal registro `ledger.jsonl`, costi degli agenti.

PROTEZIONE DEL CANALE: https (TLS del Funnel) + FIRMA di ogni invio. La chiave condivisa NON viaggia mai: si invia solo
HMAC-SHA256(chiave, «<ts>.<nome>.» + corpo) con l'orario `x-vita-ts`; il ricevitore rifiuta firme sbagliate, più vecchie di 5 minuti
o già viste (nessun replay) e scrive solo nel file del canale.

Variabili (nel `.env` di hlab, 600): HLAB_VITA_PUSH_URL (es. https://<host>/v1/push/crm), HLAB_VITA_PUSH_KEY.
Uso: `python -m hlab.push_vita [--dry]` (da timer ogni 15 minuti)."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request


LEAD_KEYS = (("nuov", "nuovi"), ("qualif", "qualificati"), ("convert", "convertiti"))
MAX_PROJECTS = 60


def _s(v, n=80) -> str:
    return "" if v is None else str(v).strip()[:n]


def build_snapshot(db) -> dict:
    """Riepilogo pronto da inviare. Solo SELECT (lo impone anche il ruolo Postgres dedicato)."""
    clients = db.query("SELECT name FROM clients ORDER BY name LIMIT 200")
    projects = db.query(
        "SELECT p.name, c.name AS client, p.status::text AS status, p.is_online, p.deadline "
        "FROM projects p JOIN clients c ON c.id = p.client_id ORDER BY p.deadline NULLS LAST, p.name LIMIT %s", (MAX_PROJECTS,))
    leads = db.query("SELECT status::text AS status, count(*) AS n FROM leads GROUP BY status")
    pending_tasks = db.query("SELECT count(*) AS n FROM tasks WHERE NOT done")
    counts = {out: 0 for _, out in LEAD_KEYS}
    for r in leads:
        st = _s(r["status"]).lower()
        for prefix, out in LEAD_KEYS:
            if st.startswith(prefix):
                counts[out] += int(r["n"])
    return {
        "clients": [{"name": _s(c["name"])} for c in clients],
        "projects": [{"name": _s(p["name"]), "client": _s(p["client"]), "status": _s(p["status"]), "online": bool(p["is_online"]),
                      "due": _s(p["deadline"], 10) or None} for p in projects],
        "leads": counts,
        "open_tasks": int(pending_tasks[0]["n"]) if pending_tasks else 0,
        "generated": int(time.time()),
    }


PAID = ("pag", "paid", "succe", "complet", "settled")
PENDING = ("in_att", "pend", "attesa")


def _cents(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def quote_totals(items) -> tuple[int, int]:
    """(una tantum, mensile) in centesimi dalle voci del preventivo [{priceCents, monthly}]."""
    once = monthly = 0
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        c = _cents(it.get("priceCents"))
        if it.get("monthly"):
            monthly += c
        else:
            once += c
    return once, monthly


def build_finance(db) -> dict:
    """Conti dell'azienda per progetto e per valuta (centesimi, mai convertiti). Solo SELECT."""
    projects = db.query("SELECT p.id::text AS id, p.name, p.currency, p.quote_items FROM projects p ORDER BY p.name LIMIT %s", (MAX_PROJECTS,))
    pays = db.query("SELECT project_id::text AS pid, status::text AS status, currency, amount_cents, paid_at FROM payments")
    try:   # il ruolo di sola lettura potrebbe non avere le fatture: i conti partono lo stesso, senza il riepilogo fatture
        invs = db.query("SELECT status::text AS status, currency, count(*) AS n, COALESCE(sum(amount_cents),0) AS cents FROM invoices GROUP BY status, currency")
    except Exception:   # noqa: BLE001
        invs = []
    paid: dict[str, int] = {}
    pending: dict[str, int] = {}
    tot_paid: dict[str, int] = {}
    tot_pending: dict[str, int] = {}
    monthly: dict[str, dict[str, int]] = {}
    for p in pays:
        st, cur, c = _s(p["status"]).lower(), _s(p["currency"], 3) or "EUR", _cents(p["amount_cents"])
        if st.startswith(PAID):
            paid[p["pid"]] = paid.get(p["pid"], 0) + c
            tot_paid[cur] = tot_paid.get(cur, 0) + c
            when = p.get("paid_at")
            if when is not None:
                key = str(when)[:7]
                monthly.setdefault(key, {})[cur] = monthly.get(key, {}).get(cur, 0) + c
        elif st.startswith(PENDING):
            pending[p["pid"]] = pending.get(p["pid"], 0) + c
            tot_pending[cur] = tot_pending.get(cur, 0) + c
    rows, tot_quote, tot_out = [], {}, {}
    for pr in projects:
        once, mon = quote_totals(pr.get("quote_items"))
        cur = _s(pr["currency"], 3) or "EUR"
        got = paid.get(pr["id"], 0)
        out = max(0, once - got)
        tot_quote[cur] = tot_quote.get(cur, 0) + once
        tot_out[cur] = tot_out.get(cur, 0) + out
        rows.append({"project": _s(pr["name"]), "currency": cur, "quote_cents": once, "monthly_cents": mon, "paid_cents": got,
                     "pending_cents": pending.get(pr["id"], 0), "outstanding_cents": out})
    months = sorted(monthly)[-6:]
    return {"projects": rows, "paid_by_currency": tot_paid, "pending_by_currency": tot_pending, "quoted_by_currency": tot_quote,
            "outstanding_by_currency": tot_out, "monthly_paid": {m: monthly[m] for m in months},
            "invoices": [{"status": _s(i["status"]), "currency": _s(i["currency"], 3), "n": _cents(i["n"]), "cents": _cents(i["cents"])} for i in invs]}


def read_job_costs(jobs_db: str, router_log) -> list[dict]:
    """Costi REALI dei modelli a pagamento per ogni lavoro della fabbrica (`site_factory`): somma del registro del router nella finestra
    di tempo del lavoro (dalla creazione all'ultimo aggiornamento). È un'attribuzione per finestra, non per richiesta: se due lavori
    girano insieme (la coda ne esegue uno alla volta) i costi non si mescolano."""
    try:
        with _ro(jobs_db) as c:
            jobs = c.execute("SELECT payload, created, updated FROM jobs WHERE kind='site_factory'").fetchall()
        rows = []
        with open(router_log, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if float(r.get("cost") or 0) > 0:
                    rows.append((float(r.get("ts") or 0), float(r["cost"]), float(r.get("est_tokens") or 0)))
    except (sqlite3.Error, OSError):
        return []
    out = []
    for payload, created, updated in jobs:
        try:
            slug = _s(json.loads(payload).get("slug"), 60)
        except (ValueError, AttributeError, TypeError):
            continue
        if not slug or not created or not updated:
            continue
        usd = sum(c for ts, c, _ in rows if created - 5 <= ts <= updated + 5)
        if usd > 0:
            out.append({"site": slug, "kind": "agenti (modelli a pagamento)", "usd": round(usd, 4), "est": False})
    return out


def read_ledger(path, jobs_db: str | None = None, router_log=None) -> dict | None:
    """Costi di produzione per sito: dal registro `ledger.jsonl` (una riga per voce: ts, site, kind, usd, est, units) più i costi dei
    lavori della fabbrica. Per sito: `usd` REALE, `est_usd` STIMATO (prezzi di listino), unità usate, voci, ultima pubblicazione.
    None se non c'è nulla da dire."""
    sites: dict[str, dict] = {}
    recent: list[dict] = []
    seen = False

    def add(r: dict) -> None:
        site = _s(r.get("site"), 60)
        if not site:
            return
        e = sites.setdefault(site, {"site": site, "usd": 0.0, "est_usd": 0.0, "kinds": {}, "units": {}, "entries": 0, "last_ts": 0})
        usd = float(r.get("usd") or 0)
        key = "est_usd" if r.get("est") else "usd"
        e[key] = round(e[key] + usd, 4)
        k = _s(r.get("kind"), 30) or "altro"
        e["kinds"][k] = round(e["kinds"].get(k, 0.0) + usd, 4)
        for u, v in (r.get("units") or {}).items():
            if isinstance(v, (int, float)):
                e["units"][str(u)[:20]] = round(e["units"].get(str(u)[:20], 0) + v, 2)
        e["entries"] += 1
        e["last_ts"] = max(e["last_ts"], int(r.get("ts") or 0))
        if k == "deploy":
            recent.append({"site": site, "ts": int(r.get("ts") or 0), "note": _s(r.get("note"), 60)})

    try:
        with open(path, encoding="utf-8") as f:
            seen = True
            for line in f:
                try:
                    add(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    if jobs_db and router_log:
        for r in read_job_costs(jobs_db, router_log):
            seen = True
            add(r)
    if not seen:
        return None
    return {"sites": sorted(sites.values(), key=lambda x: (-(x["usd"] + x["est_usd"]), x["site"]))[:40],
            "recent": sorted(recent, key=lambda x: -x["ts"])[:5]}


def _ro(path: str):
    """Connessione SQLite in sola lettura: il produttore non scrive mai nei dati di hlab."""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)


def read_costs(log_path, now: float | None = None) -> dict | None:
    """Spesa degli agenti (dollari reali riportati da OpenRouter) di oggi e del mese, in UTC, dal registro del router."""
    now = now or time.time()
    t = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    day0 = dt.datetime(t.year, t.month, t.day, tzinfo=dt.timezone.utc).timestamp()
    mon0 = dt.datetime(t.year, t.month, 1, tzinfo=dt.timezone.utc).timestamp()
    today = month = 0.0
    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                c, ts = float(r.get("cost") or 0), float(r.get("ts") or 0)
                if ts >= mon0:
                    month += c
                    if ts >= day0:
                        today += c
    except OSError:
        return None
    return {"today_usd": round(today, 3), "month_usd": round(month, 3)}


def read_factory(jobs_db: str) -> dict | None:
    """Lavori in coda o in corso della coda durevole (tipo e stato). Nessun contenuto dei lavori: solo il tipo."""
    try:
        with _ro(jobs_db) as c:
            rows = c.execute("SELECT kind, status FROM jobs WHERE status IN ('queued','running','retry') ORDER BY id DESC LIMIT 10").fetchall()
    except sqlite3.Error:
        return None
    return {"running": [{"site": str(k)[:40], "stage": str(st)} for k, st in rows]}


def read_pending(events_db: str) -> int | None:
    """Quante decisioni aspettano Lorenzo: proposte, bozze e approvazioni in sospeso."""
    n, ok = 0, False
    for sql in ("SELECT count(*) FROM proposals WHERE status='pending'", "SELECT count(*) FROM drafts WHERE status='draft'",
                "SELECT count(*) FROM approvals WHERE status='pending'"):
        try:
            with _ro(events_db) as c:
                n, ok = n + c.execute(sql).fetchone()[0], True
        except sqlite3.Error:
            continue
    return n if ok else None


def local_extras() -> dict:
    """Fabbrica, costi e decisioni in sospeso, se i file di hlab ci sono (altrimenti il campo manca: Vita lo dice)."""
    from pathlib import Path
    data = Path(os.environ.get("HLAB_DATA") or (Path(os.environ.get("HLAB_HOME", ".")) / "data"))
    out = {}
    for key, val in (("costs", read_costs(data / "router.jsonl")), ("factory", read_factory(str(data / "jobs.db"))), ("pending", read_pending(str(data / "events.db"))), ("site_costs", read_ledger(data / "ledger.jsonl", str(data / "jobs.db"), data / "router.jsonl"))):
        if val is not None:
            out[key] = val
    return out


def sign(key: str, ts: int, name: str, body: bytes) -> str:
    """Firma dell'invio: la chiave resta qui, sul filo passa solo questo valore."""
    import hashlib
    import hmac
    return hmac.new(key.encode(), f"{ts}.{name}.".encode() + body, hashlib.sha256).hexdigest()


def send(url: str, key: str, doc: dict, timeout: int = 15) -> int:
    body = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    ts = int(time.time())
    name = url.rstrip("/").rsplit("/", 1)[-1]
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"content-type": "application/json", "x-vita-ts": str(ts), "x-vita-sig": sign(key, ts, name, body)})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


def main(argv: list[str] | None = None, db=None) -> int:
    dry = "--dry" in (argv if argv is not None else sys.argv[1:])
    if db is None:
        from .tools.crm_tools import PgDb      # import tardivo: porta con sé l'SDK degli agenti, che nei test non serve
        db = PgDb()
    doc = build_snapshot(db)
    try:
        doc["finance"] = build_finance(db)       # se le tabelle dei pagamenti mancano, il riepilogo parte comunque senza conti
    except Exception as e:   # noqa: BLE001
        print(f"conti non disponibili: {type(e).__name__}", file=sys.stderr)
    doc.update(local_extras())
    if dry:
        print(json.dumps(doc, ensure_ascii=False, indent=1))
        return 0
    url, key = os.environ.get("HLAB_VITA_PUSH_URL", ""), os.environ.get("HLAB_VITA_PUSH_KEY", "")
    if not url.startswith("https://") or not key:
        print("manca HLAB_VITA_PUSH_URL (https) o HLAB_VITA_PUSH_KEY: nulla inviato", file=sys.stderr)
        return 2
    try:
        send(url, key, doc)
    except urllib.error.HTTPError as e:      # 403 = chiave errata, 429 = troppo presto: il messaggio non contiene mai la chiave
        print(f"Vita ha risposto {e.code}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as e:
        print(f"Vita non raggiungibile: {type(e).__name__}", file=sys.stderr)
        return 1
    print("riepilogo inviato a Vita")
    return 0


if __name__ == "__main__":
    sys.exit(main())
