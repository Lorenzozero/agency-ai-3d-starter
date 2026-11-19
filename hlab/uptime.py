"""Controllo dei siti dei clienti: online? veloce? certificato in scadenza? Solo richieste GET in lettura, 0 token.

Quali siti: i progetti del CRM segnati online con un indirizzo + quelli extra in quotas.yaml (`uptime: {urls: {nome: https://...}}`).
Un sito e' dato per GIU' dopo 2 controlli di fila falliti (non per un singolo intoppo di rete); l'avviso di ritorno online arriva una volta sola.
Gli avvisi: (urgente, testo). Lo stato sta in data/uptime_state.json.
"""
from __future__ import annotations

import asyncio
import json
import socket
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import config, powers

FAILS_TO_DOWN = 2
SSL_WARN_DAYS = 14
SLOW_MS = 6000
PROTECTED = (401, 403)                      # il server risponde ma chiede accesso (staging con password): raggiungibile, non giu'


def _state_path() -> Path:
    return config.DATA_DIR / "uptime_state.json"


def load() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(state: dict) -> None:
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(state), encoding="utf-8")


def netlify_sites(transport: httpx.BaseTransport | None = None) -> dict[str, str]:
    """I siti dell'account Netlify ({nome: url}), in sola lettura. Senza credenziale o con la rete giu' ritorna vuoto: non blocca il resto."""
    try:
        from .vault import Vault
        token = Vault().use("netlify-azienda", "elenco siti per il monitoraggio", who="uptime")
        with httpx.Client(base_url="https://api.netlify.com/api/v1", headers={"authorization": f"Bearer {token}"}, timeout=20, transport=transport) as c:
            r = c.get("/sites", params={"filter": "all", "per_page": 100})
            r.raise_for_status()
        return {s["name"]: s.get("ssl_url") or s.get("url") for s in r.json() if (s.get("ssl_url") or s.get("url"))}
    except Exception:
        return {}


def targets(db=None, quotas: dict | None = None, netlify_fn=netlify_sites) -> dict[str, str]:
    """{nome: url}: siti extra di quotas.yaml + progetti online del CRM + (solo se `uptime: {netlify: true}`) i siti Netlify dell'account.
    Netlify e' spento di default: molti siti li sono in sviluppo/staging e darebbero falsi allarmi.
    Se una sorgente non risponde si usano le altre: il controllo non si ferma per un database o un'API irraggiungibili."""
    cfg = (quotas or powers.load_quotas()).get("uptime", {})
    out = dict(cfg.get("urls") or {})
    try:                                                      # siti pubblicati dalla fabbrica: registrati da sitefactory.publish
        from .sitefactory import published_registry
        reg = published_registry()
        for n, u in (json.loads(reg.read_text(encoding="utf-8")) if reg.exists() else {}).items():
            out.setdefault(n, u)
    except Exception:
        pass
    if cfg.get("netlify", False):
        for n, u in netlify_fn().items():
            out.setdefault(n, u)
    try:
        if db is None:
            from .tools.crm_tools import PgDb
            db = PgDb()
        for r in db.query("SELECT p.name, p.target_url FROM projects p WHERE p.is_online AND p.target_url IS NOT NULL AND p.target_url <> ''"):
            url = r["target_url"].strip()
            if url.startswith(("http://", "https://")):
                out.setdefault(r["name"], url)
    except Exception:
        pass
    return out


def ssl_days(url: str) -> int | None:
    """Giorni alla scadenza del certificato (None se non https o non leggibile)."""
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        return None
    try:
        with socket.create_connection((u.hostname, u.port or 443), timeout=8) as s, ssl.create_default_context().wrap_socket(s, server_hostname=u.hostname) as t:
            exp = datetime.strptime(t.getpeercert()["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        return (exp - datetime.now(timezone.utc)).days
    except Exception:
        return None


async def check(url: str, transport: httpx.AsyncBaseTransport | None = None, ssl_fn=ssl_days) -> dict:
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, transport=transport, headers={"user-agent": "hlab-uptime/1"}) as c:
            r = await c.get(url)
        res = {"ok": r.status_code < 400 or r.status_code in PROTECTED, "status": r.status_code, "ms": int((time.monotonic() - t0) * 1000), "error": ""}
    except Exception as e:
        res = {"ok": False, "status": 0, "ms": int((time.monotonic() - t0) * 1000), "error": type(e).__name__}
    res["ssl_days"] = await asyncio.to_thread(ssl_fn, url) if res["ok"] else None
    return res


def decide(name: str, url: str, res: dict, st: dict, now: float) -> list[tuple[bool, str]]:
    """Aggiorna lo stato di un sito e ritorna gli avvisi (urgente, testo)."""
    out = []
    was_down = st.get("down", False)
    st.update(url=url, last=now, ok=res["ok"], status=res["status"], ms=res["ms"], ssl_days=res.get("ssl_days"), error=res["error"])
    if res["ok"]:
        st["fails"] = 0
        if was_down:
            st["down"] = False
            out.append((True, f"🟢 {name} è tornato online ({url}, {res['ms']} ms)."))
        if res["ms"] > SLOW_MS and not st.get("slow_told") == time.strftime("%Y-%m-%d", time.gmtime(now)):
            st["slow_told"] = time.strftime("%Y-%m-%d", time.gmtime(now))
            out.append((False, f"🐢 {name} risponde lento: {res['ms'] / 1000:.1f} secondi ({url})."))
        d = res.get("ssl_days")
        if d is not None and d <= SSL_WARN_DAYS and now - st.get("ssl_told", 0) > 6 * 86400:
            st["ssl_told"] = now
            out.append((d <= 3, f"🔐 Il certificato di {name} scade tra {d} giorni ({url}). Di solito si rinnova da solo: se non succede, serve un intervento."))
    else:
        st["fails"] = st.get("fails", 0) + 1
        if st["fails"] >= FAILS_TO_DOWN and not was_down:
            st["down"] = True
            why = f"errore {res['status']}" if res["status"] else f"non risponde ({res['error']})"
            out.append((True, f"🔴 {name} sembra giù: {why} da {st['fails']} controlli di fila ({url})."))
    return out


async def run(transport: httpx.AsyncBaseTransport | None = None, db=None, quotas: dict | None = None, ssl_fn=ssl_days,
              now: float | None = None, persist: bool = True, netlify_fn=netlify_sites) -> tuple[list[tuple[bool, str]], dict]:
    now, state = now or time.time(), load()
    sites = await asyncio.to_thread(targets, db, quotas, netlify_fn)
    results = await asyncio.gather(*(check(u, transport, ssl_fn) for u in sites.values()))
    alerts = []
    for (name, url), res in zip(sites.items(), results):
        alerts += decide(name, url, res, state.setdefault(name, {}), now)
    for gone in set(state) - set(sites):                      # un sito tolto dall'elenco non resta in stato "giu'" per sempre
        state.pop(gone)
    if persist:
        save(state)
    return alerts, state


def summary(state: dict) -> str:
    if not state:
        return "Nessun sito monitorato."
    rows = []
    for name, s in sorted(state.items(), key=lambda kv: (kv[1].get("ok", True), kv[0])):
        mark = "🟢" if s.get("ok") else ("🔴" if s.get("down") else "🟡")
        extra = f", certificato {s['ssl_days']} gg" if s.get("ssl_days") is not None else ""
        label = 'protetto (richiede accesso)' if s.get('status') in PROTECTED else 'HTTP ' + str(s['status']) if s.get('status') else s.get('error') or '?'
        rows.append(f"{mark} {name}: {label} in {s.get('ms', 0)} ms{extra}")
    return "\n".join(rows)
