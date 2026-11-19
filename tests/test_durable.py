import asyncio
import sqlite3
import time

import pytest

from hlab import durable
from hlab.durable import Durable


def run(coro):
    return asyncio.run(coro)


def test_submit_claim_complete_and_idempotency_key():
    db = Durable()
    a = db.submit("echo", {"x": 1}, key="k1")
    assert db.submit("echo", {"x": 1}, key="k1") == a                  # stesso lavoro attivo: non si duplica
    j = db.claim("w1")
    assert j["id"] == a and j["attempts"] == 1 and db.claim("w2") is None
    db.complete(a, {"ok": True})
    assert db.get(a)["status"] == "done" and db.submit("echo", {"x": 1}, key="k1") == a      # gia' fatto: stesso id
    assert db.submit("echo", {"x": 1}, key="k2") != a


def test_steps_are_memoised_across_retries():
    db, calls = Durable(), []

    async def handler(ctx, payload):
        a = await ctx.step("a", lambda: calls.append("a") or 1)
        b = await ctx.step("b", lambda: calls.append("b") or a + 1)
        if ctx.attempt == 1:
            raise RuntimeError("deploy fallito")                       # cade dopo i passi a e b
        return await ctx.step("c", lambda: calls.append("c") or b + 1)

    jid = db.submit("w", {})
    assert run(durable.run_one(db, {"w": handler})) == "retry"
    with sqlite3.connect(db.path) as c:                                # salta l'attesa del retry
        c.execute("UPDATE jobs SET next_run=0")
    assert run(durable.run_one(db, {"w": handler})) == "done"
    assert calls == ["a", "b", "c"]                                    # a e b NON sono ripetuti
    assert db.get(jid)["result"] == "3" and db.steps(jid) == ["a", "b", "c"]


def test_crashed_worker_is_recovered_from_the_checkpoint():
    db, calls = Durable(), []
    jid = db.submit("w", {})
    job = db.claim("morto", lease_s=60)                                 # un worker prende il lavoro...
    db.save_step(job["id"], "scarica", {"file": "ok"})                  # ...fa un passo e muore senza chiudere
    assert db.claim("vivo") is None                                     # il lease non e' ancora scaduto
    with sqlite3.connect(db.path) as c:
        c.execute("UPDATE jobs SET lease_until=?", (time.time() - 1,))  # il lease scade
    async def handler(ctx, payload):
        d = await ctx.step("scarica", lambda: calls.append("scarica") or {"file": "NUOVO"})
        await ctx.step("pubblica", lambda: calls.append("pubblica") or "fatto")
        return d
    assert run(durable.run_one(db, {"w": handler}, "vivo")) == "done"
    assert calls == ["pubblica"] and db.get(jid)["attempts"] == 2      # riprende dal punto di interruzione


def test_backoff_then_dead_after_max_attempts():
    db = Durable()
    jid = db.submit("w", {}, max_attempts=2)

    async def boom(ctx, p):
        raise ValueError("no")

    assert run(durable.run_one(db, {"w": boom})) == "retry"
    j = db.get(jid)
    assert 3 < j["next_run"] - time.time() <= 5.1 and "ValueError" in j["error"]
    with sqlite3.connect(db.path) as c:
        c.execute("UPDATE jobs SET next_run=0")
    assert run(durable.run_one(db, {"w": boom})) == "dead"
    assert db.get(jid)["status"] == "dead" and run(durable.run_one(db, {"w": boom})) is None


def test_timeout_and_unknown_handler():
    db = Durable()
    t = db.submit("lento", {}, timeout_s=0.05, max_attempts=1)

    async def slow(ctx, p):
        await asyncio.sleep(2)

    assert run(durable.run_one(db, {"lento": slow})) == "dead" and "timeout" in db.get(t)["error"]
    u = db.submit("sconosciuto", {})
    assert run(durable.run_one(db, {})) == "dead" and "nessun gestore" in db.get(u)["error"]


def test_delay_cancel_and_listing():
    db = Durable()
    later = db.submit("w", {}, delay_s=3600)
    assert db.claim("w1") is None and db.cancel(later) and db.get(later)["status"] == "cancelled"
    assert [j["status"] for j in db.list()] == ["cancelled"] and db.list("done") == []


def test_two_workers_never_take_the_same_job():
    db = Durable()
    db.submit("w", {})
    first, second = db.claim("a"), db.claim("b")
    assert first and second is None
