"""Coda durevole nativa (SQLite): lo stesso modello di Temporal/Inngest, senza servizi in piu'.

- Un lavoro e' una funzione fatta di PASSI con nome: il risultato di ogni passo si salva appena finisce (checkpoint).
- Se il processo muore o il server si riavvia, un altro giro del worker riprende il lavoro (lease scaduto) e salta i passi gia' fatti.
- Errori: retry con attesa crescente (5 s, 10 s, 20 s... fino a 5 minuti), poi "dead". Timeout per lavoro.
- Chiave di idempotenza: lo stesso lavoro non entra due volte in coda.
Garanzia: un passo e' eseguito AL PIU' una volta per tentativo e salvato dopo; se il processo muore tra l'effetto e il salvataggio il passo
si ripete, quindi i passi con effetti esterni (deploy, invii) devono essere idempotenti (es. commit con lo stesso messaggio, chiave di deduplica).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, key TEXT, status TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3, timeout_s INTEGER NOT NULL DEFAULT 900,
  next_run REAL NOT NULL, lease_until REAL, worker TEXT, result TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_active_key ON jobs(key) WHERE key IS NOT NULL AND status IN ('queued','running','retry');
CREATE TABLE IF NOT EXISTS steps(job_id INTEGER NOT NULL, name TEXT NOT NULL, result TEXT, ts REAL NOT NULL, PRIMARY KEY(job_id, name));
"""
_MISSING = object()


class Durable:
    def __init__(self, path=None):
        self.path = path or (config.DATA_DIR / "jobs.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path)
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        c.close()

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path, timeout=15, isolation_level=None)   # transazioni esplicite
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    # ---- coda ------------------------------------------------------------------------
    def submit(self, kind: str, payload: dict | None = None, key: str | None = None, max_attempts: int = 3,
               timeout_s: int = 900, delay_s: float = 0) -> int:
        now = time.time()
        with self._conn() as c:
            if key:
                r = c.execute("SELECT id FROM jobs WHERE key=? AND status IN ('queued','running','retry','done') ORDER BY id DESC LIMIT 1", (key,)).fetchone()
                if r:
                    return r["id"]
            return c.execute("INSERT INTO jobs(kind,payload,key,max_attempts,timeout_s,next_run,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                             (kind, json.dumps(payload or {}, ensure_ascii=False), key, max_attempts, timeout_s, now + delay_s, now, now)).lastrowid

    def claim(self, worker: str, lease_s: float = 120) -> dict | None:
        """Prende il prossimo lavoro pronto, oppure uno 'running' il cui lease e' scaduto (il worker precedente e' morto)."""
        now = time.time()
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("SELECT * FROM jobs WHERE (status IN ('queued','retry') AND next_run<=?) OR (status='running' AND lease_until<?) "
                          "ORDER BY next_run LIMIT 1", (now, now)).fetchone()
            if not r:
                c.execute("COMMIT")
                return None
            c.execute("UPDATE jobs SET status='running', attempts=attempts+1, worker=?, lease_until=?, updated=? WHERE id=?",
                      (worker, now + lease_s, now, r["id"]))
            c.execute("COMMIT")
            return {**dict(r), "attempts": r["attempts"] + 1}

    def heartbeat(self, job_id: int, worker: str, lease_s: float = 120) -> None:
        with self._conn() as c:
            c.execute("UPDATE jobs SET lease_until=?, updated=? WHERE id=? AND worker=? AND status='running'",
                      (time.time() + lease_s, time.time(), job_id, worker))

    def complete(self, job_id: int, result) -> None:
        with self._conn() as c:
            c.execute("UPDATE jobs SET status='done', result=?, error=NULL, lease_until=NULL, updated=? WHERE id=?",
                      (json.dumps(result, ensure_ascii=False, default=str), time.time(), job_id))

    def fail(self, job: dict, error: str, dead: bool = False) -> str:
        n, now = job["attempts"], time.time()
        status = "dead" if dead or n >= job["max_attempts"] else "retry"
        wait = min(5 * 2 ** (n - 1), 300)
        with self._conn() as c:
            c.execute("UPDATE jobs SET status=?, error=?, next_run=?, lease_until=NULL, updated=? WHERE id=?",
                      (status, error[:500], now + wait, now, job["id"]))
        return status

    def cancel(self, job_id: int) -> bool:
        with self._conn() as c:
            return c.execute("UPDATE jobs SET status='cancelled', updated=? WHERE id=? AND status IN ('queued','retry')",
                             (time.time(), job_id)).rowcount == 1

    def get(self, job_id: int) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(r) if r else None

    def list(self, status: str | None = None, limit: int = 30) -> list[dict]:
        with self._conn() as c:
            q, p = "SELECT id,kind,status,attempts,max_attempts,key,error,updated FROM jobs", []
            if status:
                q, p = q + " WHERE status=?", [status]
            return [dict(r) for r in c.execute(q + " ORDER BY id DESC LIMIT ?", p + [limit])]

    # ---- passi (checkpoint) ----------------------------------------------------------------
    def get_step(self, job_id: int, name: str):
        with self._conn() as c:
            r = c.execute("SELECT result FROM steps WHERE job_id=? AND name=?", (job_id, name)).fetchone()
        return _MISSING if r is None else json.loads(r["result"])

    def save_step(self, job_id: int, name: str, result) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO steps(job_id,name,result,ts) VALUES(?,?,?,?)",
                      (job_id, name, json.dumps(result, ensure_ascii=False, default=str), time.time()))

    def steps(self, job_id: int) -> list[str]:
        with self._conn() as c:
            return [r["name"] for r in c.execute("SELECT name FROM steps WHERE job_id=? ORDER BY ts", (job_id,))]


class Ctx:
    """Passato al lavoro: `await ctx.step("nome", funzione, ...)` esegue una volta e ricorda il risultato."""

    def __init__(self, db: Durable, job: dict, worker: str):
        self.db, self.job, self.worker = db, job, worker

    async def step(self, name: str, fn, *args, **kw):
        cached = self.db.get_step(self.job["id"], name)
        if cached is not _MISSING:
            return cached
        r = fn(*args, **kw)
        if inspect.isawaitable(r):
            r = await r
        self.db.save_step(self.job["id"], name, r)
        return json.loads(json.dumps(r, default=str))      # stesso valore che si vedrebbe alla ripresa

    @property
    def attempt(self) -> int:
        return self.job["attempts"]


async def run_one(db: Durable, handlers: dict, worker: str = "", lease_s: float = 120) -> str | None:
    """Esegue UN lavoro. Ritorna lo stato finale (done/retry/dead) o None se la coda e' vuota."""
    worker = worker or f"w-{uuid.uuid4().hex[:6]}"
    job = db.claim(worker, lease_s)
    if not job:
        return None
    handler = handlers.get(job["kind"])
    if not handler:
        return db.fail(job, f"nessun gestore per '{job['kind']}'", dead=True)

    async def beat():
        while True:
            await asyncio.sleep(lease_s / 3)
            db.heartbeat(job["id"], worker, lease_s)

    hb = asyncio.create_task(beat())
    try:
        res = await asyncio.wait_for(handler(Ctx(db, job, worker), json.loads(job["payload"])), job["timeout_s"])
    except asyncio.TimeoutError:
        return db.fail(job, f"timeout dopo {job['timeout_s']} s")
    except Exception as e:  # noqa: BLE001 - qualunque errore del lavoro diventa un retry
        return db.fail(job, f"{type(e).__name__}: {e}")
    finally:
        hb.cancel()
    db.complete(job["id"], res)
    return "done"


async def worker_loop(db: Durable, handlers: dict, poll_s: float = 2.0, stop: asyncio.Event | None = None) -> None:
    worker = f"w-{uuid.uuid4().hex[:6]}"
    while not (stop and stop.is_set()):
        if await run_one(db, handlers, worker) is None:
            await asyncio.sleep(poll_s)
