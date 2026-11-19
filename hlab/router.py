"""Instradatore locale: parla il formato Anthropic con l'Agent SDK e inoltra a piu' fornitori, ESAURENDO LE QUOTE GRATUITE prima di pagare.

Ordine dei LIVELLI (configurabile con `tier_order`): openrouter (i :free) -> groq -> cloudflare -> ... -> paid (OpenRouter a pagamento).
  - Ogni fornitore gratuito ha la sua quota: quando segnala il limite giornaliero (429 "per day", errore 4006 di Cloudflare...) il suo
    livello si chiude fino a mezzanotte UTC e si passa al successivo, senza tentativi sprecati.
  - Un fornitore con una finestra di contesto piccola (Groq free: 8.000 token al minuto, Cloudflare: 24.000) viene SALTATO per le richieste
    piu' grandi (`max_input_tokens`): le chiamate degli agenti pesano ~18.000 token.
  - Il livello a pagamento ha un tetto di spesa giornaliero calcolato dal costo reale riportato da OpenRouter.
OpenRouter parla gia' il formato Anthropic; gli altri fornitori parlano OpenAI e passano da translate.py.
"""
from __future__ import annotations

import asyncio
import calendar
import json
import logging
import os
import re
import time
from collections import deque
from pathlib import Path

import httpx
import yaml
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from . import config
from .translate import estimate_tokens, from_openai, to_openai, to_sse

log = logging.getLogger("hlab.router")
DEFAULT_CONFIG = {
    "upstream": "https://openrouter.ai/api",
    "listen": {"host": "127.0.0.1", "port": 8099},
    "cooldown_s": 60,          # modello fuori servizio dopo un 429 senza Retry-After
    "max_wait_s": 15,          # quanto aspettare, al massimo, se tutti i modelli sono in pausa
    "timeout_s": 180,
    "limits": {"free_per_minute": 18, "free_per_day": 45, "paid_usd_per_day": 0.60},   # OpenRouter :free e livello a pagamento; 0 = nessun limite
    "providers": {},           # fornitori OpenAI-compatibili: {nome: {base_url, key_env, free, max_input_tokens, max_tokens}}
    "tier_order": ["openrouter", "groq", "cloudflare", "paid"],
    "allow_paid": True,        # False = MAI modelli a pagamento: il livello `paid` non si apre e le richieste per nome di un modello non gratuito vengono rifiutate
    "pools": {},
}
DROP_FOR_NON_ANTHROPIC = ("thinking", "context_management")
DAILY_RE = re.compile(r"per[ -]day|daily|4006|\bRPD\b|\bTPD\b", re.I)
COST_RE = re.compile(rb'"cost"\s*:\s*([0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?)')


def utc_day() -> str:
    return time.strftime("%F", time.gmtime())


def next_utc_midnight(now: float | None = None) -> float:
    t = time.gmtime(now or time.time())
    return calendar.timegm((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0)) + 86400


def is_free(model: str) -> bool:
    return model.endswith(":free") or model == "openrouter/free"


def tier_of(entry: dict, providers: dict) -> str:
    """Livello di un modello: il nome del fornitore se ha una quota gratuita, 'openrouter' per i :free, altrimenti 'paid'."""
    prov = entry.get("provider", "openrouter")
    if prov == "openrouter":
        return "openrouter" if is_free(entry["model"]) else "paid"
    return prov if providers.get(prov, {}).get("free", True) else "paid"


def label(entry: dict) -> str:
    return entry["model"] if entry.get("provider", "openrouter") == "openrouter" else f"{entry['provider']}/{entry['model']}"


def overlay_path() -> Path:
    """Modifiche ai pool fatte dalla sentinella (data/ e' l'unica cartella scrivibile dagli agenti): `models.yaml` resta il file di Lorenzo, mai toccato."""
    return config.DATA_DIR / "models.override.yaml"


def apply_overlay(cfg: dict, ov: dict) -> None:
    """`add: {pool: [{model, provider?}]}` inserisce prima del primo modello di un livello successivo; `remove: {pool: [...]}` toglie. Pool o fornitori sconosciuti si ignorano."""
    rank = {t: n for n, t in enumerate(cfg["tier_order"])}
    for pool, items in ((ov or {}).get("remove") or {}).items():
        if pool in cfg["pools"]:
            gone = {(i.get("provider", "openrouter"), i["model"]) for i in items if isinstance(i, dict) and i.get("model")}
            cfg["pools"][pool] = [m for m in cfg["pools"][pool] if (m.get("provider", "openrouter"), m["model"]) not in gone]
    for pool, items in ((ov or {}).get("add") or {}).items():
        if pool not in cfg["pools"]:
            continue
        for i in items:
            if not (isinstance(i, dict) and i.get("model")) or i.get("provider", "openrouter") not in ("openrouter", *cfg["providers"]):
                continue
            entry = {"model": i["model"], **({"provider": i["provider"]} if i.get("provider") and i["provider"] != "openrouter" else {})}
            if any((m.get("provider", "openrouter"), m["model"]) == (i.get("provider", "openrouter"), i["model"]) for m in cfg["pools"][pool]):
                continue
            mine = rank.get(tier_of(entry, cfg["providers"]), 99)
            at = next((n for n, m in enumerate(cfg["pools"][pool]) if rank.get(tier_of(m, cfg["providers"]), 99) > mine), len(cfg["pools"][pool]))
            cfg["pools"][pool].insert(at, entry)


def load_config(path: Path | None = None, overlay="auto") -> dict:
    p = path or config.LAB_HOME / "models.yaml"
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if p.exists():
        user = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for k, v in user.items():
            cfg[k] = {**cfg[k], **v} if isinstance(cfg.get(k), dict) and isinstance(v, dict) and k not in ("pools", "providers") else v
    ov = overlay_path() if overlay == "auto" and p == config.LAB_HOME / "models.yaml" else (Path(overlay) if overlay not in (None, "auto") else None)
    if ov is not None and ov.exists():
        apply_overlay(cfg, yaml.safe_load(ov.read_text(encoding="utf-8")) or {})
    for name, pool in cfg["pools"].items():
        if not pool or not all(isinstance(m, dict) and m.get("model") for m in pool):
            raise ValueError(f"pool '{name}' vuoto o con voci senza 'model'")
        for m in pool:
            if m.get("provider", "openrouter") not in ("openrouter", *cfg["providers"]):
                raise ValueError(f"pool '{name}': fornitore sconosciuto '{m.get('provider')}'")
    return cfg


def _strip_cache_control(o):
    if isinstance(o, dict):
        return {k: _strip_cache_control(v) for k, v in o.items() if k != "cache_control"}
    if isinstance(o, list):
        return [_strip_cache_control(v) for v in o]
    return o


def sanitize(body: dict, model: str) -> dict:
    """I modelli non Anthropic non conoscono cache_control, thinking e simili: si tolgono per non farli fallire."""
    if model.startswith("anthropic/") or model.startswith("~anthropic/"):
        return body
    b = _strip_cache_control(body)
    for k in DROP_FOR_NON_ANTHROPIC:
        b.pop(k, None)
    return b


def anthropic_error(kind: str, msg: str, status: int, retry_after: int | None = None) -> JSONResponse:
    h = {"retry-after": str(retry_after)} if retry_after else {}
    return JSONResponse({"type": "error", "error": {"type": kind, "message": msg}}, status_code=status, headers=h)


class State:
    def __init__(self, cfg: dict, api_key: str, transport=None, log_path: Path | None = None, cfg_path: Path | None = None):
        self.cfg, self.key = cfg, api_key
        self.transport, self.cfg_path = transport, cfg_path
        self._cfg_mtime = self._mtimes()
        self._cfg_check = 0.0
        self.cool: dict[str, float] = {}          # etichetta modello -> istante di rientro
        self.rr: dict[tuple, int] = {}            # (pool, livello) -> prossimo indice di partenza
        self.minute: deque[float] = deque()
        self.day = {"date": utc_day(), "n": 0}
        self.spend = {"date": utc_day(), "usd": 0.0}
        self.closed: dict[str, float] = {}        # livello -> fino a quando e' chiuso (limite giornaliero)
        self.stats: dict[str, dict] = {}
        self.client = httpx.AsyncClient(base_url=cfg["upstream"], timeout=cfg["timeout_s"], transport=transport)
        self.keys, self.clients = {}, {}
        self._build_providers()
        self.log_path = log_path
        self._load_today()

    def _build_providers(self) -> None:
        for name, p in self.cfg["providers"].items():       # solo i fornitori con una chiave presente
            k = os.environ.get(p.get("key_env", ""), "")
            if k and name not in self.clients:
                base = re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), p["base_url"])
                self.keys[name], self.clients[name] = k, httpx.AsyncClient(base_url=base, timeout=self.cfg["timeout_s"], transport=self.transport)

    def _mtimes(self) -> float:
        out = 0.0
        for q in (self.cfg_path, overlay_path() if self.cfg_path and self.cfg_path == config.LAB_HOME / "models.yaml" else None):
            try:
                out = max(out, q.stat().st_mtime) if q else out
            except OSError:
                pass
        return out

    def maybe_reload(self, every_s: float = 10.0) -> bool:
        """Ricarica `models.yaml` se e' cambiato (la sentinella aggiorna i pool dopo i test): niente riavvio, stato e contatori restano.
        Un file rotto non sostituisce mai la configurazione in uso."""
        if not self.cfg_path or time.time() - self._cfg_check < every_s:
            return False
        self._cfg_check = time.time()
        try:
            m = self._mtimes()
            if m == self._cfg_mtime:
                return False
            new = load_config(self.cfg_path, overlay=overlay_path() if self.cfg_path == config.LAB_HOME / "models.yaml" else None)
        except (OSError, ValueError, yaml.YAMLError) as e:
            log.warning("models.yaml non ricaricato: %s", type(e).__name__)
            self._cfg_mtime = self._mtimes()
            return False
        self._cfg_mtime, self.cfg = m, new
        self._build_providers()
        log.warning("models.yaml ricaricato a caldo")
        return True

    @property
    def free_closed_until(self) -> float:
        return self.closed.get("openrouter", 0.0)

    @free_closed_until.setter
    def free_closed_until(self, v: float) -> None:
        self.closed["openrouter"] = v

    def _load_today(self) -> None:
        """Dopo un riavvio si ricostruiscono richieste, spesa e chiusure di oggi (UTC) dal registro: i tetti non si azzerano."""
        if not self.log_path or not self.log_path.exists():
            return
        today = utc_day()
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if time.strftime("%F", time.gmtime(r.get("ts", 0))) != today:
                continue
            if r.get("ok") and is_free(r.get("model", "")):
                self.day["n"] += 1
            self.spend["usd"] += float(r.get("cost") or 0)
            if r.get("status") == 429 and r.get("daily"):
                self.closed[r.get("tier") or "openrouter"] = max(self.closed.get(r.get("tier") or "openrouter", 0), next_utc_midnight())

    def _roll(self) -> None:
        d = utc_day()
        if self.day["date"] != d:
            self.day = {"date": d, "n": 0}
        if self.spend["date"] != d:
            self.spend = {"date": d, "usd": 0.0}
            self.closed = {}

    def pool_name(self, model: str) -> str | None:
        n = (model or "").split("[")[0]
        return n[5:] if n.startswith("hlab-") and n[5:] in self.cfg["pools"] else None

    def free_state(self) -> tuple[bool, str, int]:
        """Livello OpenRouter gratuito: (aperto, motivo, secondi). Chiuso per limite giornaliero di OpenRouter o per i tetti locali."""
        self._roll()
        now, lim = time.time(), self.cfg["limits"]
        if self.closed.get("openrouter", 0) > now:
            return False, "limite giornaliero dei modelli gratuiti raggiunto (OpenRouter)", int(self.closed["openrouter"] - now)
        while self.minute and now - self.minute[0] > 60:
            self.minute.popleft()
        if lim["free_per_day"] and self.day["n"] >= lim["free_per_day"]:
            return False, f"tetto giornaliero locale raggiunto ({lim['free_per_day']} richieste ai modelli gratuiti)", int(next_utc_midnight() - now)
        if lim["free_per_minute"] and len(self.minute) >= lim["free_per_minute"]:
            return False, "tetto al minuto raggiunto", max(1, int(60 - (now - self.minute[0])))
        return True, "", 0

    def paid_state(self) -> tuple[bool, str, int]:
        self._roll()
        if not self.cfg.get("allow_paid", True):
            return False, "modelli a pagamento disattivati (allow_paid: false)", 3600
        cap = self.cfg["limits"].get("paid_usd_per_day") or 0
        if cap and self.spend["usd"] >= cap:
            return False, f"tetto di spesa giornaliero raggiunto ({self.spend['usd']:.3f} $ su {cap} $)", int(next_utc_midnight() - time.time())
        return True, "", 0

    def entries(self, pool: str) -> list[dict]:
        out = []
        for m in self.cfg["pools"][pool]:
            prov = m.get("provider", "openrouter")
            if prov == "openrouter" or prov in self.keys:
                out.append({**m, "provider": prov, "tier": tier_of(m, self.cfg["providers"]), "label": label({**m, "provider": prov})})
        return out

    def order(self, pool: str, free_ok: bool = True, paid_ok: bool = True) -> list[dict]:
        """Livelli nell'ordine di `tier_order`, ognuno a rotazione; i modelli in pausa vanno in fondo al proprio livello."""
        self._roll()
        now, ents = time.time(), self.entries(pool)
        out: list[dict] = []
        for tier in self.cfg["tier_order"]:
            if tier == "paid" and not paid_ok or tier == "openrouter" and not free_ok or self.closed.get(tier, 0) > now:
                continue
            models = [e for e in ents if e["tier"] == tier]
            if not models:
                continue
            start = self.rr.get((pool, tier), 0) % len(models)
            self.rr[(pool, tier)] = start + 1                  # rotazione: a ogni richiesta si parte dal modello successivo
            rot = models[start:] + models[:start]
            ready = [e for e in rot if self.cool.get(e["label"], 0) <= now]
            out += ready or sorted(rot, key=lambda e: self.cool.get(e["label"], 0))
        return out

    def soonest(self, ents) -> float:
        return max(0.0, min((self.cool.get(e["label"], 0) for e in ents), default=0) - time.time())

    def bench(self, e: dict, ok: bool, status: int, ms: int, cost: float = 0.0, daily: bool = False, est: int = 0):
        s = self.stats.setdefault(e["label"], {"ok": 0, "fail": 0, "last_status": None, "ms_total": 0, "usd": 0.0})
        s["ok" if ok else "fail"] += 1
        s["last_status"], s["ms_total"], s["usd"] = status, s["ms_total"] + ms, round(s["usd"] + cost, 6)
        self.spend["usd"] += cost
        if self.log_path:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": round(time.time()), "model": e["model"] if e["provider"] == "openrouter" else e["label"], "tier": e["tier"],
                                    "ok": ok, "status": status, "ms": ms, "cost": cost, "daily": daily, "est_tokens": est}) + "\n")

    def count(self):
        self.minute.append(time.time())
        self.day["n"] += 1


def make_app(cfg: dict | None = None, api_key: str = "", transport=None, log_path: Path | None = None) -> Starlette:
    st = State(cfg or load_config(), api_key, transport, log_path, cfg_path=(config.LAB_HOME / "models.yaml") if cfg is None else None)

    def or_headers(request: Request) -> dict:
        h = {"authorization": f"Bearer {st.key}", "content-type": "application/json", "x-title": "hermes-lab"}
        if request.headers.get("anthropic-version"):
            h["anthropic-version"] = request.headers["anthropic-version"]   # anthropic-beta non si inoltra: i modelli gratuiti non lo capiscono
        return h

    async def messages(request: Request):
        body = await request.json()
        st.maybe_reload()
        wanted = body.get("model", "")
        pool = st.pool_name(wanted)
        est = estimate_tokens(body)
        if pool:
            free_ok, free_why, free_wait = st.free_state()
            paid_ok, paid_why, paid_wait = st.paid_state()
            candidates = st.order(pool, free_ok, paid_ok)
            if not candidates:
                why, wait = (paid_why, paid_wait) if not paid_ok and st.cfg.get("allow_paid", True) else (free_why, free_wait)
                return anthropic_error("rate_limit_error", why, 429, min(wait, 3600))
        else:
            if not st.cfg.get("allow_paid", True) and not is_free(wanted):
                return anthropic_error("permission_error", f"il modello '{wanted}' non e' gratuito e i modelli a pagamento sono disattivati", 403)
            candidates = [{"model": wanted, "provider": "openrouter", "tier": "paid", "label": wanted}]
        deadline = time.time() + st.cfg["max_wait_s"]
        tried = 0
        while True:
            for e in candidates:
                prov_cfg = st.cfg["providers"].get(e["provider"], {})
                if st.closed.get(e["tier"], 0) > time.time():
                    continue                                       # il livello si e' chiuso durante questa richiesta
                if prov_cfg.get("max_input_tokens") and est > prov_cfg["max_input_tokens"]:
                    continue                                       # richiesta troppo grande per questo fornitore: non e' un tentativo
                tried += 1
                t0 = time.time()
                try:
                    if e["provider"] == "openrouter":
                        req = st.client.build_request("POST", "/v1/messages", json=sanitize({**body, "model": e["model"]}, e["model"]), headers=or_headers(request))
                        resp = await st.client.send(req, stream=bool(body.get("stream")))
                    else:
                        resp = await st.clients[e["provider"]].post("/chat/completions", json=to_openai(body, e["model"], prov_cfg.get("max_tokens"), prov_cfg.get("tool_call_extra")),
                                                                    headers={"authorization": f"Bearer {st.keys[e['provider']]}"})
                except (httpx.TimeoutException, httpx.TransportError) as ex:
                    st.cool[e["label"]] = time.time() + 30
                    st.bench(e, False, 0, int((time.time() - t0) * 1000), est=est)
                    log.warning("%s: rete (%s)", e["label"], type(ex).__name__)
                    continue
                ms = int((time.time() - t0) * 1000)
                if resp.status_code == 200:
                    if e["tier"] == "openrouter":
                        st.count()
                    out_h = {"x-hlab-model": e["label"], "x-hlab-attempts": str(tried), "x-hlab-tier": e["tier"]}
                    if e["provider"] != "openrouter":              # OpenAI-compatibile: si traduce la risposta intera
                        try:
                            msg = from_openai(resp.json(), e["model"])
                        except ValueError:
                            st.cool[e["label"]] = time.time() + 120
                            st.bench(e, False, 502, ms, est=est)
                            continue
                        st.bench(e, True, 200, ms, est=est)
                        if body.get("stream"):
                            return Response(to_sse(msg), 200, {**out_h, "content-type": "text/event-stream"})
                        return JSONResponse(msg, headers=out_h)
                    if body.get("stream"):
                        async def gen(r=resp, ee=e, started=t0):
                            tail = b""
                            try:
                                if r.is_stream_consumed:        # risposta gia' letta per intero (trasporti di prova o risposte piccole)
                                    tail = r.content
                                    yield tail
                                else:
                                    async for chunk in r.aiter_raw():
                                        tail = (tail + chunk)[-4096:]
                                        yield chunk
                            finally:
                                await r.aclose()
                                c = COST_RE.findall(tail)
                                st.bench(ee, True, 200, int((time.time() - started) * 1000), float(c[-1]) if c else 0.0, est=est)
                        return StreamingResponse(gen(), media_type=resp.headers.get("content-type", "text/event-stream"), headers=out_h)
                    c = COST_RE.findall(resp.content)
                    st.bench(e, True, 200, ms, float(c[-1]) if c else 0.0, est=est)
                    return Response(resp.content, 200, {**out_h, "content-type": "application/json"})
                if body.get("stream") and e["provider"] == "openrouter":
                    await resp.aread()
                text = resp.text[:400]
                daily = (resp.status_code == 429 and bool(DAILY_RE.search(text))) or ("4006" in text and e["provider"] == "cloudflare")
                st.bench(e, False, resp.status_code, ms, daily=daily, est=est)
                if daily and e["tier"] != "paid":
                    st.closed[e["tier"]] = next_utc_midnight()      # limite giornaliero: si chiude tutto il livello fino a mezzanotte UTC
                elif resp.status_code == 429:
                    st.cool[e["label"]] = time.time() + float(resp.headers.get("retry-after") or st.cfg["cooldown_s"])
                elif resp.status_code >= 500 or resp.status_code in (402, 404):
                    st.cool[e["label"]] = time.time() + (3600 if resp.status_code in (402, 404) else 30)
                elif resp.status_code == 413:
                    st.cool[e["label"]] = time.time() + 600         # richiesta troppo grande per quel modello
                elif resp.status_code in (400, 401, 403, 422) and not pool:
                    return Response(resp.content, resp.status_code, {"content-type": "application/json"})
                else:
                    st.cool[e["label"]] = time.time() + 120      # richiesta rifiutata da quel modello: si prova il prossimo
                log.warning("%s -> %s %s", e["label"], resp.status_code, text[:200])
            live = [e for e in candidates if st.closed.get(e["tier"], 0) <= time.time()]
            wait = st.soonest(live)
            if not pool or not live or time.time() + wait > deadline:
                break
            await asyncio.sleep(wait + 0.2)
            candidates = st.order(pool, st.free_state()[0], st.paid_state()[0])
        too_big = [e["provider"] for e in candidates if st.cfg["providers"].get(e["provider"], {}).get("max_input_tokens", 10 ** 9) < est]
        hint = f" (richiesta da ~{est} token: saltati {sorted(set(too_big))} per la soglia di contesto)" if too_big else ""
        return anthropic_error("overloaded_error", f"nessun modello disponibile nel pool ({tried} tentativi){hint}: riprova tra poco", 529,
                               int(st.soonest(candidates)) + 1 if pool and candidates else 5)

    async def count_tokens(request: Request):
        raw = json.dumps(await request.json(), ensure_ascii=False)
        return JSONResponse({"input_tokens": max(1, len(raw) // 4)})

    async def health(request: Request):
        now = time.time()
        free_ok, free_why, _ = st.free_state()
        paid_ok, paid_why, _ = st.paid_state()
        return JSONResponse({"ok": True, "pools": {p: [{"model": e["label"], "tier": e["tier"], "cooldown_s": max(0, round(st.cool.get(e["label"], 0) - now))}
                                                         for e in st.entries(p)] for p in st.cfg["pools"]},
                             "today": st.day, "spend": {**st.spend, "cap": st.cfg["limits"].get("paid_usd_per_day")},
                             "free": {"open": free_ok, "why": free_why}, "paid": {"open": paid_ok, "why": paid_why},
                             "tiers": {t: {"closed_s": max(0, round(st.closed.get(t, 0) - now))} for t in st.cfg["tier_order"]},
                             "providers": sorted(st.keys), "stats": st.stats})

    app = Starlette(routes=[Route("/v1/messages", messages, methods=["POST"]),
                            Route("/v1/messages/count_tokens", count_tokens, methods=["POST"]),
                            Route("/health", health)])
    app.state.hl = st
    return app


def serve():  # python -m hlab router
    import uvicorn

    from .runner import load_env_file
    load_env_file()
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise SystemExit("manca OPENROUTER_API_KEY in ~/hermes_lab/.env")
    cfg = load_config()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    uvicorn.run(make_app(cfg, key, log_path=config.DATA_DIR / "router.jsonl"), host=cfg["listen"]["host"], port=cfg["listen"]["port"],
                log_level="warning")
