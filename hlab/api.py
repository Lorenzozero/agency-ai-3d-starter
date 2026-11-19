"""API del frontend (app web / app Flutter): archivio di documenti + flusso in tempo reale + file statici dell'app. Solo per te: token personale.

Si lega a 127.0.0.1 di default. Per usarla dal telefono NON si apre su internet: si raggiunge da una rete privata (Tailscale) o da un tunnel SSH.
Il token sta in data/api_token (permessi 600); si genera con `hlab api-token`. Ogni richiesta (tranne /api/health e /api/login) lo vuole come Bearer.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from . import config
from .docstore import Store

USER = {"uid": "lorenzo", "email": "admin@agency-ai.local", "displayName": "Lorenzo"}
KEEPALIVE_S = 20
MAX_BODY, MAX_LOGIN_BODY = 2_000_000, 4_096   # byte: oltre, la richiesta e' rifiutata prima di leggerla (un corpo enorme non deve riempire la memoria)
# Cosa puo' fare la pagina: solo il proprio codice, i font di Google e immagini; collegamenti solo verso se stessa (blocca anche l'invio email di emailjs, mai usato).
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; "
       "img-src 'self' data: blob: https:; media-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
FAIL_MAX, FAIL_WINDOW_S = 10, 600          # dopo 10 token sbagliati in 10 minuti, quell'indirizzo e' bloccato per il resto della finestra


def token_path() -> Path:
    return config.DATA_DIR / "api_token"


def load_token(create: bool = False) -> str | None:
    p = token_path()
    if p.exists():
        return p.read_text().strip()
    if not create:
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    p.write_text(tok)
    p.chmod(0o600)
    return tok


class SecurityHeaders:
    """Intestazioni di sicurezza su ogni risposta (ASGI puro: non interferisce con il flusso in tempo reale)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        api_path = scope.get("path", "").startswith("/api/")

        async def snd(m):
            if m["type"] == "http.response.start":
                have = {k.lower() for k, _ in m.get("headers", [])}
                extra = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"), (b"x-frame-options", b"DENY"),
                         (b"strict-transport-security", b"max-age=15552000"), (b"content-security-policy", CSP.encode()),
                         (b"permissions-policy", b"camera=(), microphone=(), geolocation=()")]
                if api_path and b"cache-control" not in have:
                    extra.append((b"cache-control", b"no-store"))              # i dati dell'archivio non restano in cache del browser o di un proxy
                m["headers"] = list(m.get("headers", [])) + extra
            await send(m)
        await self.app(scope, receive, snd)


def make_app(store: Store | None = None, token: str | None = None, dist: Path | None = None, on_start=None) -> Starlette:
    st = store or Store()
    expected = token or load_token()
    dist = dist if dist is not None else Path(os.environ.get("HLAB_APP_DIST") or config.LAB_HOME / "app" / "dist")

    fails: dict[str, list[float]] = {}

    def client_ip(req: Request) -> str:
        """L'API ascolta solo su 127.0.0.1: chi arriva da internet passa per un proxy locale (Cloudflare, Tailscale Funnel) e la connessione sembra sempre locale.
        L'indirizzo vero sta in cf-connecting-ip (Cloudflare) o nel primo valore di x-forwarded-for (Tailscale e altri proxy). Senza questo, 10 tentativi
        sbagliati di UN estraneo bloccherebbero tutti, anche te."""
        fwd = (req.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        cf = req.headers.get("cf-connecting-ip") if os.environ.get("HLAB_TRUST_CF_IP") == "1" else None      # un header che chiunque puo' inventare: si crede solo dietro Cloudflare
        return cf or fwd or (req.client.host if req.client else "?")

    def locked(ip: str) -> bool:
        now = time.time()
        recent = [t for t in fails.get(ip, []) if now - t < FAIL_WINDOW_S]
        if recent:
            fails[ip] = recent
        else:
            fails.pop(ip, None)                                               # la tabella non cresce all'infinito con indirizzi che non tornano
        return len(recent) >= FAIL_MAX

    def fail(ip: str) -> None:
        fails.setdefault(ip, []).append(time.time())
        if len(fails[ip]) == FAIL_MAX:
            print(f"api: indirizzo bloccato per {FAIL_WINDOW_S // 60} minuti dopo {FAIL_MAX} token errati: {ip}", flush=True)     # resta nel journal: si vede con journalctl

    def too_big(req: Request, limit: int) -> bool:
        try:
            return int(req.headers.get("content-length") or 0) > limit
        except ValueError:
            return True

    def authed(req: Request) -> bool:
        h = req.headers.get("authorization", "")
        return bool(expected) and h.startswith("Bearer ") and hmac.compare_digest(h[7:], expected)

    def guard(fn):
        async def wrapped(req: Request):
            ip = client_ip(req)
            if locked(ip):
                return JSONResponse({"error": "troppi tentativi: riprova piu' tardi"}, status_code=429)
            if not authed(req):
                fail(ip)
                return JSONResponse({"error": "non autorizzato"}, status_code=401)
            if too_big(req, MAX_BODY):
                return JSONResponse({"error": "richiesta troppo grande"}, status_code=413)
            try:
                body = await req.json() if req.method == "POST" else {}
                if not isinstance(body, dict):
                    raise ValueError("il corpo deve essere un oggetto JSON")
                return await fn(req, body)
            except (KeyError, ValueError, TypeError) as e:
                return JSONResponse({"error": str(e)}, status_code=400)
        return wrapped

    async def health(req):
        return JSONResponse({"ok": True})

    async def login(req):
        ip = client_ip(req)
        if locked(ip):
            return JSONResponse({"error": "troppi tentativi: riprova piu' tardi"}, status_code=429)
        if too_big(req, MAX_LOGIN_BODY):
            return JSONResponse({"error": "richiesta troppo grande"}, status_code=413)
        try:
            body = await req.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):                                         # un corpo malformato e' un errore del client (400), non del server
            fail(ip)
            return JSONResponse({"error": "richiesta non valida"}, status_code=400)
        if not expected or not hmac.compare_digest(str(body.get("token", "")), expected):
            fail(ip)
            return JSONResponse({"error": "token non valido"}, status_code=401)
        return JSONResponse({"user": USER})

    async def get_doc(req, b):
        return JSONResponse({"data": st.get(b["path"])})

    async def run_query(req, b):
        return JSONResponse({"docs": st.query(b["coll"], b.get("where"), b.get("order"), b.get("limit"))})

    async def write(req, b):
        op = b["op"]
        if op == "set":
            st.set(b["path"], b["data"], b.get("merge", False))
        elif op == "update":
            st.update(b["path"], b["data"])
        elif op == "delete":
            st.delete(b["path"])
        elif op == "add":
            return JSONResponse({"id": st.add(b["coll"], b["data"])})
        elif op == "batch":
            st.batch(b["ops"])
        else:
            raise ValueError(f"operazione sconosciuta: {op}")
        return JSONResponse({"ok": True})

    async def listen(req, b):
        """Flusso SSE: prima lo stato attuale, poi uno nuovo a ogni modifica della collezione. Il client lo legge con fetch (serve l'header di autorizzazione)."""
        is_doc = b.get("kind") == "doc"
        coll = st._split(b["path"])[0] if is_doc else b["coll"]

        def snapshot() -> str:
            if is_doc:
                return json.dumps({"doc": st.get(b["path"])}, ensure_ascii=False)
            return json.dumps({"docs": st.query(coll, b.get("where"), b.get("order"), b.get("limit"))}, ensure_ascii=False)

        async def stream():
            q = st.subscribe(coll)
            try:
                yield f"data: {snapshot()}\n\n"
                while True:
                    try:
                        await asyncio.wait_for(q.get(), KEEPALIVE_S)
                        yield f"data: {snapshot()}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                    if await req.is_disconnected():
                        break
            finally:
                st.unsubscribe(q)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})

    async def spa(req: Request):
        if not dist.exists():
            return Response("App non compilata: esegui `npm run build` in app/ (o imposta HLAB_APP_DIST).", status_code=404)
        rel = req.path_params.get("rest") or "index.html"
        if chr(0) in rel:                                                      # un byte nullo fa sollevare ValueError al sistema operativo: risposta pulita, non 500
            return Response("non trovato", status_code=404)
        f = (dist / rel).resolve()
        if dist.resolve() not in f.parents and f != dist.resolve():          # niente uscite dalla cartella
            return Response(status_code=404)
        is_asset = rel.startswith("assets/")
        if f.is_file():
            # I file in assets/ hanno l'impronta nel nome: si possono tenere in cache a lungo. Tutto il resto (soprattutto index.html) va rivalidato
            # a ogni apertura, altrimenti dopo una pubblicazione il browser resta con una pagina che punta a file che non esistono piu' (schermo bianco).
            return FileResponse(f, headers={"cache-control": "public, max-age=31536000, immutable" if is_asset else "no-cache"})
        if is_asset:                                                        # un asset mancante e' un errore vero, non una pagina HTML travestita da script
            return Response(status_code=404)
        return FileResponse(dist / "index.html", headers={"cache-control": "no-cache"})

    routes = [
        Route("/api/health", health),
        Route("/api/login", login, methods=["POST"]),
        Route("/api/db/get", guard(get_doc), methods=["POST"]),
        Route("/api/db/query", guard(run_query), methods=["POST"]),
        Route("/api/db/write", guard(write), methods=["POST"]),
        Route("/api/db/listen", guard(listen), methods=["POST"]),
        Route("/{rest:path}", spa),
    ]
    @asynccontextmanager
    async def lifespan(_app):
        if on_start:
            await on_start()
        yield

    app = Starlette(routes=routes, lifespan=lifespan)
    app.add_middleware(SecurityHeaders)
    app.state.store = st
    return app


def resolve_host(host: str, wait_s: int = 90) -> str:
    """`tailscale` = l'indirizzo privato di questo server nella rete Tailscale (si aspetta che il collegamento sia pronto)."""
    if host != "tailscale":
        return host
    import subprocess
    for _ in range(max(1, wait_s // 3)):
        try:
            ip = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
            if ip and ip[0].count(".") == 3:
                return ip[0]
        except (OSError, subprocess.SubprocessError):
            pass
        time.sleep(3)
    raise RuntimeError("Tailscale non e' collegato: `sudo tailscale up` e apri il link per autorizzare questo server")


def serve(host: str = "127.0.0.1", port: int = 8770) -> None:
    import uvicorn

    host = resolve_host(host)

    from . import api_sync
    store = Store()
    tok = load_token(create=True)
    app = make_app(store, tok, on_start=api_sync.start(store))
    uvicorn.run(app, host=host, port=port, log_level="warning", timeout_graceful_shutdown=3, server_header=False)   # le connessioni in tempo reale restano aperte: senza limite il riavvio aspetta il kill
