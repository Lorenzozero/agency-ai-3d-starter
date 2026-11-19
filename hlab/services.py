"""Mappa REALE dei servizi da cui dipende il sistema: router, fornitori di modelli, ricerca, media, server, siti, canali.

Per ogni voce: piano, limite, quanto e' stato usato, quando si azzera, stato e se il dato e' MISURATO (letto adesso dal fornitore o dal registro del router)
oppure solo DOCUMENTATO (limite dichiarato dal fornitore, data dell'ultima verifica): la vista non presenta mai una stima come se fosse una misura.
Le sonde non lanciano mai eccezioni (una voce irraggiungibile diventa «giu'»), hanno una cache per voce (i fornitori hanno limiti anche sulle sonde) e
non spendono: richieste da 1 token o elenchi di modelli. Nessuna chiave esce da qui: si leggono solo gli esiti.

Uso: `collect()` -> dict pronto per la GUI e per la sentinella; `publish(store)` lo scrive in `config/system`; `history()` -> esecuzioni e token.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from . import config

VERIFIED = "2026-11-06"
PUBLIC_URL = os.environ.get("HLAB_PUBLIC_URL", "https://agency-ai.example.com")
UNITS = ("hlab-api", "hlab-worker", "hlab-router", "hermes", "tailscaled")
TIMERS = ("hlab-notify.timer", "hlab-backup.timer", "hlab-push-vita.timer", "hlab-sentinella.timer")
TTL = {"openrouter": 300, "groq": 1800, "cloudflare": 600, "gemini": 600, "nvidia": 1800, "tavily": 900, "pixabay": 1800, "github": 300, "telegram": 600,
       "crm": 300, "public": 120, "router": 20, "server": 30, "units": 30, "backup": 600, "vita": 120, "sites": 60}
_cache: dict[str, tuple[float, list[dict]]] = {}
_hermes_env_cache: dict[str, str] | None = None


def env(name: str) -> str:
    """Variabile d'ambiente; se manca, dal file `hermes.env` (le chiavi del bot vivono li', quelle di hlab in `.env`). Mai stampata."""
    global _hermes_env_cache
    v = os.environ.get(name, "")
    if v:
        return v
    if _hermes_env_cache is None:
        _hermes_env_cache = {}
        try:
            for line in (Path.home() / "hermes.env").read_text(encoding="utf-8").splitlines():
                m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
                if m:
                    _hermes_env_cache[m.group(1)] = m.group(2).strip().strip("'\"")
        except OSError:
            pass
    return _hermes_env_cache.get(name, "")


def pct(used, limit) -> float | None:
    try:
        return round(100.0 * float(used) / float(limit), 1) if limit and used is not None else None
    except (TypeError, ValueError):
        return None


def level(p: float | None) -> str:
    """Stato dalla percentuale di quota usata: da 75% attenzione, da 95% critico."""
    return "ok" if p is None or p < 75 else "warn" if p < 95 else "down"


def item(id: str, name: str, group: str, plan: str, *, status: str = "ok", used=None, limit=None, unit: str = "", resets: str = "", detail: str = "",
         measured: bool = True, source: str = "", children: list | None = None, verified: str = VERIFIED, good_ratio: bool = False) -> dict:
    """`good_ratio`: used/limit e' la quota di cose A POSTO (servizi attivi, siti online), non di risorse consumate: 100% e' il meglio, e lo stato non si deduce dalla percentuale."""
    p = pct(used, limit)
    if status == "ok" and p is not None and not good_ratio:
        status = level(p)
    d = {"id": id, "name": name, "group": group, "plan": plan, "status": status, "used": used, "limit": limit, "unit": unit, "pct": p, "resets": resets,
         "detail": detail, "measured": measured, "source": source, "verified": verified, "checked": int(time.time()), "good_ratio": good_ratio}
    if children is not None:
        d["children"] = children
    return d


def safe(fn, id: str, name: str, group: str, plan: str, **kw) -> list[dict]:
    """Una sonda che fallisce non ferma le altre: la voce diventa «giu'» con il tipo di errore (mai il testo, che potrebbe contenere una chiave)."""
    for attempt in (1, 2):
        try:
            return fn()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as e:   # rete o fornitore lento un istante: si riprova una volta, poi NON e' un guasto
            if attempt == 1:
                time.sleep(1.5)
                continue
            r = item(id, name, group, plan, status="warn", detail=f"non ha risposto in tempo ({type(e).__name__}): riproverò al prossimo giro", measured=False, **kw)
            r["transient"] = True
            return [r]
        except Exception as e:                                                              # noqa: BLE001 - e' proprio il punto: non lanciare mai
            return [item(id, name, group, plan, status="down", detail=f"sonda non riuscita: {type(e).__name__}", measured=False, **kw)]
    return []


def http(timeout: float = 8.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=False)


# ---------------------------------------------------------------- registro del router (misure locali)

def router_log() -> Path:
    return config.DATA_DIR / "router.jsonl"


def day_of(ts: float) -> str:
    return time.strftime("%F", time.gmtime(ts))


def read_router_rows(days: int = 8, now: float | None = None) -> list[dict]:
    p, rows, now = router_log(), [], now or time.time()
    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            if size > 20_000_000:                                                            # file enorme: solo la parte recente
                f.seek(size - 20_000_000)
                f.readline()
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if now - r.get("ts", 0) <= days * 86400:
                    rows.append(r)
    except OSError:
        pass
    return rows


def tier_stats(rows: list[dict], day: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        if day_of(r.get("ts", 0)) != day:
            continue
        s = out.setdefault(r.get("tier", "?"), {"n": 0, "ok": 0, "fail": 0, "tokens": 0, "usd": 0.0, "last": None, "models": {}})
        s["n"] += 1
        s["ok" if r.get("ok") else "fail"] += 1
        s["tokens"] += int(r.get("est_tokens") or 0) if r.get("ok") else 0
        s["usd"] += float(r.get("cost") or 0)
        s["last"] = r.get("status")
        m = s["models"].setdefault(r.get("model", "?"), {"ok": 0, "fail": 0})
        m["ok" if r.get("ok") else "fail"] += 1
    return out


TIER_NAME = {"openrouter": "OpenRouter (modelli :free)", "gemini": "Google Gemini (AI Studio gratuito)", "nvidia": "NVIDIA NIM", "groq": "Groq", "github": "GitHub Models",
             "cloudflare": "Cloudflare Workers AI", "mistral": "Mistral", "cerebras": "Cerebras", "paid": "A pagamento (OpenRouter)"}
TIER_KEY = {"openrouter": "OPENROUTER_API_KEY", "gemini": "GEMINI_FREE_API_KEY", "nvidia": "NVIDIA_API_KEY", "groq": "GROQ_API_KEY", "github": "GITHUB_MODELS_TOKEN",
            "cloudflare": "CF_API_TOKEN"}


def probe_router() -> list[dict]:
    from . import router
    cfg = router.load_config()
    rows, today = read_router_rows(), day_of(time.time())
    stats = tier_stats(rows, today)
    health = {}
    try:
        with http(3) as c:
            health = c.get(f"http://127.0.0.1:{cfg['listen']['port']}/health").json()
    except Exception:                                                                       # noqa: BLE001
        pass
    kids = []
    allow_paid = cfg.get("allow_paid", True)
    for t in cfg["tier_order"]:
        if t == "paid" and not allow_paid:
            continue
        s = stats.get(t, {"n": 0, "ok": 0, "fail": 0, "tokens": 0, "usd": 0.0, "models": {}})
        key = TIER_KEY.get(t, "")
        have = bool(os.environ.get(key)) if key else t not in cfg["providers"]
        closed = (health.get("tiers", {}).get(t, {}) or {}).get("closed_s", 0) if health else 0
        fail_rate = s["fail"] / s["n"] if s["n"] else 0
        status, why = "ok", ""
        if not have:
            status, why = "off", "chiave assente: fornitore spento"
        elif closed > 0:
            status, why = "warn", f"chiuso per limite giornaliero ancora {closed // 60} min"
        elif s["n"] >= 10 and fail_rate > 0.6:
            status, why = "warn", f"{int(fail_rate * 100)}% di errori oggi"
        limit = cfg["limits"]["free_per_day"] if t == "openrouter" else None
        used = s["n"] if t == "openrouter" else None
        k = item(f"router-{t}", TIER_NAME.get(t, t), "router", "gratuito" if t != "paid" else "a pagamento", status=status, used=used, limit=limit,
                 unit="richieste/giorno" if limit else "", resets="mezzanotte UTC", detail=why or f"{s['n']} richieste oggi, {s['ok']} ok, {s['fail']} errori, ~{s['tokens']:,} token".replace(",", "."),
                 source="registro del router (data/router.jsonl)")
        k["today"] = {"n": s["n"], "ok": s["ok"], "fail": s["fail"], "tokens": s["tokens"], "last_status": s.get("last")}
        k["models"] = [{"model": m, **v} for m, v in sorted(s["models"].items(), key=lambda kv: -(kv[1]["ok"] + kv[1]["fail"]))[:6]]
        kids.append(k)
    spent = sum(s["usd"] for s in stats.values())
    paid_state = "ok" if allow_paid or spent == 0 else "down"
    detail = "modelli a pagamento DISATTIVATI (allow_paid: false)" if not allow_paid else "modelli a pagamento attivi con tetto giornaliero"
    if spent > 0 and not allow_paid:
        detail = f"ATTENZIONE: {spent:.4f} $ spesi oggi con i modelli a pagamento disattivati"
    guard = item("router-paid-guard", "Protezione «mai pagare»", "router", "regola", status=paid_state, used=round(spent, 4), unit="$ spesi oggi", detail=detail,
                 source="models.yaml e registro del router")
    main = item("router", "Router dei modelli", "router", "locale", status="ok" if health else "down", detail=("attivo, in ascolto su 127.0.0.1" if health else "il router non risponde"),
                children=kids + [guard], source="hlab-router")
    return [main]


# ---------------------------------------------------------------- fornitori (sonde reali)

def probe_openrouter() -> list[dict]:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return [item("openrouter", "OpenRouter (account)", "modelli", "gratuito", status="off", detail="chiave assente", measured=False)]
    with http() as c:
        h = {"Authorization": f"Bearer {key}"}
        cr = c.get("https://openrouter.ai/api/v1/credits", headers=h).json().get("data", {})
        k = c.get("https://openrouter.ai/api/v1/key", headers=h).json().get("data", {})
    total, used = float(cr.get("total_credits") or 0), float(cr.get("total_usage") or 0)
    plan = "senza crediti acquistati (gratuito)" if k.get("is_free_tier") else f"{total:g} $ di crediti acquistati"
    free_cap = "50 richieste/giorno sui modelli :free (sotto i 10 $ acquistati)" if total < 10 else "1000 richieste/giorno sui modelli :free"
    return [item("openrouter", "OpenRouter (account)", "modelli", plan, used=round(used, 3), limit=total or None, unit="$ di credito usato",
                 detail=f"{free_cap}. Credito residuo {total - used:.2f} $: non viene speso (modelli a pagamento chiusi). Controlla che l'auto-ricarica sia spenta.",
                 source="openrouter.ai/api/v1/credits")]


def probe_groq() -> list[dict]:
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return [item("groq", "Groq", "modelli", "gratuito", status="off", detail="chiave assente", measured=False)]
    with http(15) as c:
        r = c.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                   json={"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": "1"}], "max_tokens": 1})
    h = r.headers
    lim_r, rem_r = h.get("x-ratelimit-limit-requests"), h.get("x-ratelimit-remaining-requests")
    lim_t, rem_t = h.get("x-ratelimit-limit-tokens"), h.get("x-ratelimit-remaining-tokens")
    if r.status_code == 429 and not rem_r:
        return [item("groq", "Groq", "modelli", "gratuito", status="warn", detail="limite raggiunto adesso (429)", resets=h.get("retry-after", "") and f"tra {h.get('retry-after')} s")]
    if r.status_code != 200:
        return [item("groq", "Groq", "modelli", "gratuito", status="down", detail=f"risposta {r.status_code}")]
    used = int(lim_r) - int(rem_r) if lim_r and rem_r else None
    return [item("groq", "Groq", "modelli", "gratuito (Free)", used=used, limit=int(lim_r) if lim_r else None, unit="richieste/giorno",
                 resets=h.get("x-ratelimit-reset-requests", ""), detail=f"token al minuto: {rem_t or '?'} su {lim_t or '?'} (limiti per modello)", source="intestazioni x-ratelimit di Groq")]


def probe_cloudflare() -> list[dict]:
    tok = os.environ.get("CF_API_TOKEN", "")
    if not tok:
        return [item("cloudflare", "Cloudflare Workers AI", "modelli", "gratuito", status="off", detail="token assente", measured=False)]
    acc = os.environ.get("CF_ACCOUNT_ID", "")
    with http() as c:
        r = c.get(f"https://api.cloudflare.com/client/v4/accounts/{acc}/tokens/verify" if acc else "https://api.cloudflare.com/client/v4/user/tokens/verify",
                  headers={"Authorization": f"Bearer {tok}"})                                # un token di ACCOUNT si verifica qui, non nell'endpoint dei token utente
    ok = r.status_code == 200 and (r.json().get("result") or {}).get("status") == "active"
    return [item("cloudflare", "Cloudflare Workers AI (+ immagini FLUX)", "modelli", "gratuito: 10.000 neuroni/giorno", status="ok" if ok else "down", limit=10000, unit="neuroni/giorno",
                 resets="00:00 UTC", detail="token valido" if ok else "token non valido o scaduto", measured=False, source="developers.cloudflare.com/workers-ai/platform/pricing")]


def probe_gemini() -> list[dict]:
    key = os.environ.get("GEMINI_FREE_API_KEY", "")
    if not key:
        return [item("gemini", "Google Gemini (AI Studio)", "modelli", "gratuito", status="off", detail="GEMINI_FREE_API_KEY assente", measured=False)]
    with http() as c:
        r = c.get("https://generativelanguage.googleapis.com/v1beta/openai/models", headers={"Authorization": f"Bearer {key}"})
    ok = r.status_code == 200
    return [item("gemini", "Google Gemini (AI Studio)", "modelli", "gratuito (progetto senza fatturazione)", status="ok" if ok else "down", unit="richieste/giorno",
                 resets="mezzanotte Pacific", detail=("chiave valida. Limiti per modello dichiarati da Google (indicativamente ~250 richieste/giorno sui Flash): vedi la pagina dei limiti in AI Studio. "
                                                      "Sul piano gratuito Google puo' usare i contenuti per migliorare i prodotti." if ok else f"risposta {r.status_code}"),
                 measured=False, source="ai.google.dev/gemini-api/docs/rate-limits")]


def probe_nvidia() -> list[dict]:
    key = os.environ.get("NVIDIA_API_KEY", "")
    if not key:
        return [item("nvidia", "NVIDIA NIM", "modelli", "gratuito", status="off", detail="chiave assente", measured=False)]
    with http(20) as c:
        r = c.post("https://integrate.api.nvidia.com/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                   json={"model": "nvidia/nemotron-3.5-bolt-e4b", "messages": [{"role": "user", "content": "1"}], "max_tokens": 1})
    ok = r.status_code == 200
    return [item("nvidia", "NVIDIA NIM", "modelli", "gratuito (Developer Program)", status="ok" if ok else "down", unit="richieste/minuto", limit=40,
                 detail=("chiave valida, inferenza ok" if ok else f"risposta {r.status_code} (403 = account da abilitare)"), measured=False, source="build.nvidia.com")]


def probe_tavily() -> list[dict]:
    key = env("TAVILY_API_KEY")
    if not key:
        return [item("tavily", "Tavily (ricerca web)", "ricerca", "gratuito", status="off", detail="chiave assente", measured=False)]
    with http(20) as c:
        r = c.get("https://api.tavily.com/usage", headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        return [item("tavily", "Tavily (ricerca web)", "ricerca", "gratuito", status="down", detail=f"risposta {r.status_code}")]
    d = r.json()
    acc = d.get("account") or {}
    used = acc.get("plan_usage", (d.get("key") or {}).get("usage"))
    limit = acc.get("plan_limit") or (d.get("key") or {}).get("limit") or 1000
    return [item("tavily", "Tavily (ricerca web)", "ricerca", f"{acc.get('current_plan') or 'Free'}: {limit} crediti/mese", used=used, limit=limit, unit="crediti/mese", resets="inizio mese",
                 detail="il bot si ferma da solo a 800 ricerche al mese (SEARCH_MAX_MONTH)", source="api.tavily.com/usage")]


def probe_pixabay() -> list[dict]:
    key = env("PIXABAY_API_KEY") or os.environ.get("PIXABAY_API_KEY", "")
    if not key:
        return [item("pixabay", "Pixabay (video e foto stock)", "media", "gratuito", status="off", detail="chiave assente", measured=False)]
    with http(20) as c:
        r = c.get("https://pixabay.com/api/", params={"key": key, "q": "coffee", "per_page": 3})
    h = r.headers
    lim, rem = h.get("x-ratelimit-limit"), h.get("x-ratelimit-remaining")
    used = int(lim) - int(rem) if lim and rem else None
    return [item("pixabay", "Pixabay (video e foto stock)", "media", "gratuito: 100 richieste/60 s", status="ok" if r.status_code == 200 else "down", used=used,
                 limit=int(lim) if lim else 100, unit="richieste/minuto", resets=f"tra {h.get('x-ratelimit-reset')} s" if h.get("x-ratelimit-reset") else "",
                 detail="risposte in cache 24 ore (obbligo dell'API)", source="intestazioni x-ratelimit di Pixabay")]


def probe_github() -> list[dict]:
    tok = env("GH_TOKEN")
    if not tok:
        return [item("github", "GitHub (PR e Pages)", "distribuzione", "gratuito", status="off", detail="token assente", measured=False)]
    with http() as c:
        r = c.get("https://api.github.com/rate_limit", headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
    if r.status_code != 200:
        return [item("github", "GitHub (PR e Pages)", "distribuzione", "gratuito", status="down", detail=f"risposta {r.status_code}: token da verificare")]
    core = (r.json().get("resources") or {}).get("core") or {}
    return [item("github", "GitHub (PR e Pages)", "distribuzione", "gratuito: 5.000 richieste/ora", used=(core.get("limit") or 0) - (core.get("remaining") or 0), limit=core.get("limit"),
                 unit="richieste/ora", resets=time.strftime("%H:%M UTC", time.gmtime(core.get("reset", 0))) if core.get("reset") else "", source="api.github.com/rate_limit")]


def probe_telegram() -> list[dict]:
    tok = env("TELEGRAM_TOKEN")
    if not tok:
        return [item("telegram", "Telegram (bot Hermes)", "canali", "gratuito", status="off", detail="token assente", measured=False)]
    with http() as c:
        r = c.get(f"https://api.telegram.org/bot{tok}/getMe")
    ok = r.status_code == 200 and r.json().get("ok")
    return [item("telegram", "Telegram (bot Hermes)", "canali", "gratuito", status="ok" if ok else "down", detail="bot raggiungibile" if ok else f"risposta {r.status_code}",
                 source="api.telegram.org")]


def probe_crm() -> list[dict]:
    from .tools.crm_tools import PgDb
    t = time.time()
    PgDb().query("SELECT 1 AS ok")
    return [item("crm", "CRM (Postgres, sola lettura)", "dati", "esterno", detail=f"risponde in {int((time.time() - t) * 1000)} ms", source="ruolo Postgres di sola lettura")]


# ---------------------------------------------------------------- server e infrastruttura

def run(cmd: list[str], timeout: float = 6) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def probe_server() -> list[dict]:
    out = []
    try:
        load1 = float(Path("/proc/loadavg").read_text().split()[0])
        cpus = os.cpu_count() or 1
        mem = {k: int(v.split()[0]) for k, v in (l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines() if ":" in l)}
        used_mb = (mem["MemTotal"] - mem["MemAvailable"]) // 1024
        total_mb = mem["MemTotal"] // 1024
        du = shutil.disk_usage("/")
        up_days = float(Path("/proc/uptime").read_text().split()[0]) / 86400
        out += [item("server-cpu", "Server Oracle: processore", "infrastruttura", f"{cpus} CPU", used=round(load1, 2), limit=cpus, unit="carico medio (1 min)", detail=f"acceso da {up_days:.1f} giorni", source="/proc/loadavg"),
                item("server-ram", "Server Oracle: memoria", "infrastruttura", f"{total_mb // 1024} GB", used=used_mb, limit=total_mb, unit="MB", source="/proc/meminfo"),
                item("server-disk", "Server Oracle: disco", "infrastruttura", f"{du.total // 2**30} GB", used=du.used // 2**30, limit=du.total // 2**30, unit="GB", source="statvfs /")]
    except Exception as e:                                                                  # noqa: BLE001
        out.append(item("server", "Server Oracle", "infrastruttura", "", status="down", detail=f"lettura non riuscita: {type(e).__name__}", measured=False))
    return out


def probe_units() -> list[dict]:
    names = list(UNITS) + list(TIMERS)
    states = dict(zip(names, run(["systemctl", "is-active", *names]).split()))
    kids = [item(f"unit-{n}", n, "infrastruttura", "systemd", status="ok" if states.get(n) == "active" else ("off" if n.endswith(".timer") and states.get(n) == "inactive" else "down"),
                 detail=states.get(n, "sconosciuto")) for n in names]
    failed = [l.split()[1] if l.startswith("●") else l.split()[0] for l in run(["systemctl", "--failed", "--no-legend", "--plain"]).splitlines() if l.strip()]
    bad = [k for k in kids if k["status"] == "down"]
    return [item("units", "Servizi e timer del server", "infrastruttura", "systemd", status="down" if bad else "ok", used=len(kids) - len(bad), limit=len(kids), unit="attivi", good_ratio=True,
                 detail=("fermi: " + ", ".join(k["name"] for k in bad)) if bad else "tutti attivi" + (f"; unita' fallite: {', '.join(failed[:4])}" if failed else ""),
                 children=kids, source="systemctl")]


def probe_public() -> list[dict]:
    t = time.time()
    with http(10) as c:
        r = c.get(f"{PUBLIC_URL}/api/health")
    ok = r.status_code == 200
    return [item("public", "Indirizzo fisso della console (Tailscale Funnel)", "infrastruttura", "gratuito", status="ok" if ok else "down", detail=f"{PUBLIC_URL} risponde in {int((time.time() - t) * 1000)} ms" if ok else f"{PUBLIC_URL}: risposta {r.status_code}",
                 source="richiesta HTTPS dal server")]


def probe_backup() -> list[dict]:
    root = Path.home() / "hermes_backups"
    days = sorted(p.name for p in root.glob("20??-??-??")) if root.exists() else []
    if not days:
        return [item("backup", "Backup notturno", "infrastruttura", "locale + copia sul Pi", status="down", detail="nessuna copia trovata")]
    last = max(root.joinpath(days[-1]).stat().st_mtime, 0)
    age_h = (time.time() - last) / 3600
    return [item("backup", "Backup notturno (+ copia sul Raspberry)", "infrastruttura", f"{len(days)} giorni conservati", status="ok" if age_h < 26 else "warn", used=round(age_h, 1), limit=26, unit="ore dall'ultima copia",
                 detail=f"ultima copia {days[-1]}; la copia fuori sede parte dopo ogni backup (sola scrittura sul Pi)", source="~/hermes_backups")]


def probe_vita() -> list[dict]:
    out = run(["systemctl", "show", "hlab-push-vita.service", "-p", "Result", "-p", "ExecMainExitTimestamp"])
    kv = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    ok = kv.get("Result") == "success"
    return [item("vita", "Invio dei dati a Vita (Raspberry)", "canali", "gratuito", status="ok" if ok else "warn", detail=f"ultimo esito: {kv.get('Result', '?')} ({kv.get('ExecMainExitTimestamp', '')[:25]})", source="systemd")]


def probe_sites() -> list[dict]:
    from . import uptime
    st = uptime.load()
    kids = [item(f"site-{n}", n, "siti", "pubblicato", status="ok" if s.get("ok") and not s.get("down") else "down", used=s.get("ms"), unit="ms",
                 detail=f"HTTP {s.get('status')}, certificato {s.get('ssl_days')} giorni" if s.get("ssl_days") is not None else f"HTTP {s.get('status')}") for n, s in sorted(st.items())]
    if not kids:
        return [item("sites", "Siti dei clienti", "siti", "", status="info", detail="nessun sito monitorato", measured=False)]
    bad = [k for k in kids if k["status"] == "down"]
    return [item("sites", "Siti monitorati", "siti", "controllo ogni 15 minuti", status="down" if bad else "ok", used=len(kids) - len(bad), limit=len(kids), unit="online", good_ratio=True, children=kids, source="monitor di uptime")]


# ---------------------------------------------------------------- voci documentate, non misurabili da qui
DOCUMENTED = [
    item("oracle", "Oracle Cloud (server)", "infrastruttura", "Always Free? da verificare", status="info", detail="Il piano non e' leggibile dal server: controlla Billing nella console OCI che non ci siano addebiti.", measured=False,
         source="cloud.oracle.com"),
    item("pagespeed", "Google PageSpeed (audit)", "ricerca", "gratuito: ~25.000 richieste/giorno", status="info", detail="chiave presente; la quota non e' interrogabile senza consumarla", measured=False,
         source="developers.google.com/speed/docs/insights/v5/get-started"),
    item("gemini-bot", "Gemini immagini del bot (progetto prepagato)", "media", "crediti esauriti (402)", status="info", detail="Il bot ripiega su Cloudflare FLUX gratis. Tetto GEMINI_MAX_USD_MONTH=3. Non e' usata dal router.", measured=False,
         source="hermes.env"),
    item("hf", "Hugging Face ZeroGPU", "media", "gratuito: ~5 min GPU/giorno", status="info", detail="Non collegato: solo uso manuale per video/3D.", measured=False, source="huggingface.co/pricing"),
]

PROBES = {"openrouter": (probe_openrouter, "OpenRouter (account)", "modelli", "gratuito"), "groq": (probe_groq, "Groq", "modelli", "gratuito"),
          "cloudflare": (probe_cloudflare, "Cloudflare", "modelli", "gratuito"), "gemini": (probe_gemini, "Gemini", "modelli", "gratuito"),
          "nvidia": (probe_nvidia, "NVIDIA NIM", "modelli", "gratuito"), "tavily": (probe_tavily, "Tavily", "ricerca", "gratuito"),
          "pixabay": (probe_pixabay, "Pixabay", "media", "gratuito"), "github": (probe_github, "GitHub", "distribuzione", "gratuito"),
          "telegram": (probe_telegram, "Telegram", "canali", "gratuito"), "crm": (probe_crm, "CRM", "dati", "esterno"),
          "router": (probe_router, "Router", "router", "locale"), "server": (probe_server, "Server", "infrastruttura", ""),
          "units": (probe_units, "Servizi", "infrastruttura", "systemd"), "public": (probe_public, "Indirizzo fisso", "infrastruttura", ""),
          "backup": (probe_backup, "Backup", "infrastruttura", ""), "vita": (probe_vita, "Vita", "canali", ""), "sites": (probe_sites, "Siti", "siti", "")}
GROUP_ORDER = ["router", "modelli", "ricerca", "media", "dati", "distribuzione", "canali", "siti", "infrastruttura"]
GROUP_LABEL = {"router": "Router dei modelli", "modelli": "Fornitori di modelli (piani gratuiti)", "ricerca": "Ricerca e audit", "media": "Immagini, video e media", "dati": "Dati",
               "distribuzione": "Pubblicazione", "canali": "Canali e collegamenti", "siti": "Siti dei clienti", "infrastruttura": "Server e infrastruttura"}


def run_probe(pid: str, force: bool = False) -> list[dict]:
    ttl_ok = _cache.get(pid) and time.time() - _cache[pid][0] < TTL.get(pid, 300)
    if ttl_ok and not force:
        return _cache[pid][1]
    fn, name, group, plan = PROBES[pid]
    res = safe(fn, pid, name, group, plan)
    _cache[pid] = (time.time(), res)
    return res


def collect(force: bool = False) -> dict:
    """Stato completo, raggruppato. Le sonde girano in parallelo; ciascuna ha la sua cache. Scrive anche `data/system_status.json` per gli altri processi."""
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda p: run_probe(p, force), PROBES))
    items = [i for r in results for i in r] + [dict(d, checked=int(time.time())) for d in DOCUMENTED]
    flat = []
    for i in items:
        flat.append(i)
        flat += i.get("children", [])
    counts = {s: sum(1 for i in flat if i["status"] == s) for s in ("ok", "warn", "down", "off", "info")}
    groups = [{"id": g, "label": GROUP_LABEL[g], "items": [i for i in items if i["group"] == g]} for g in GROUP_ORDER]
    status = {"updated": int(time.time()), "counts": counts, "overall": "down" if counts["down"] else "warn" if counts["warn"] else "ok", "groups": [g for g in groups if g["items"]]}
    try:
        p = config.DATA_DIR / "system_status.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass
    return status


def load_status(max_age_s: float = 300) -> dict | None:
    """Ultimo stato scritto da `collect` (anche da un altro processo); None se manca o e' troppo vecchio."""
    try:
        d = json.loads((config.DATA_DIR / "system_status.json").read_text(encoding="utf-8"))
        return d if time.time() - d.get("updated", 0) <= max_age_s else None
    except (OSError, ValueError):
        return None


def flat_items(status: dict) -> list[dict]:
    out = []
    for g in status.get("groups", []):
        for i in g["items"]:
            out.append(i)
            out += i.get("children", [])
    return out


# ---------------------------------------------------------------- cronologia: esecuzioni e token
def history(now: float | None = None) -> dict:
    now = now or time.time()
    rows = read_router_rows(8, now)
    days = {}
    for r in rows:
        d = days.setdefault(day_of(r.get("ts", 0)), {"day": day_of(r.get("ts", 0)), "n": 0, "ok": 0, "fail": 0, "tokens": 0, "usd": 0.0, "tiers": {}})
        d["n"] += 1
        d["ok" if r.get("ok") else "fail"] += 1
        d["tokens"] += int(r.get("est_tokens") or 0) if r.get("ok") else 0
        d["usd"] = round(d["usd"] + float(r.get("cost") or 0), 5)
        d["tiers"][r.get("tier", "?")] = d["tiers"].get(r.get("tier", "?"), 0) + 1
    out = {"updated": int(now), "days": sorted(days.values(), key=lambda x: x["day"])[-7:], "jobs": [], "events": [], "lean": []}
    try:
        from .durable import Durable
        out["jobs"] = [{"id": j["id"], "kind": j["kind"], "status": j["status"], "attempts": j.get("attempts"), "ts": int(j.get("updated") or j.get("created") or 0)}
                       for j in Durable().list(limit=15)]
    except Exception:                                                                       # noqa: BLE001
        pass
    try:
        from .events import Events
        with Events()._conn() as c:
            out["events"] = [{"ts": int(ts), "kind": k, "agent": a, "tool": t.split("__")[-1] if t else "", "ok": None if ok is None else bool(ok), "detail": (d or "")[:120]}
                             for ts, k, a, t, ok, d in c.execute("select ts, kind, agent, tool, ok, detail from events where kind not in ('tool_allow','tool_deny','lean') order by ts desc limit 40")]
            out["lean"] = [{"day": day, "path": tool, "n": n, "tokens": tk} for day, tool, n, tk in
                           c.execute("select date(ts,'unixepoch'), tool, count(*), coalesce(sum(tokens),0) from events where kind='lean' group by 1,2 order by 1 desc limit 20")]
    except Exception:                                                                       # noqa: BLE001
        pass
    return out


def publish(store) -> dict:
    """Raccoglie e scrive in `config/system` e `config/systemHistory` (la pagina Sistema della console ascolta questi due documenti)."""
    st = collect()
    store.set("config/system", {**st, "updatedAt": {"__op": "serverTimestamp"}})
    store.set("config/systemHistory", {**history(), "updatedAt": {"__op": "serverTimestamp"}})
    return st["counts"]
