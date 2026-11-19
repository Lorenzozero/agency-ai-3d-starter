import asyncio
import sqlite3

from hlab import durable, workflows
from hlab.durable import Durable


def test_echo_workflow_runs_and_leaves_checkpoints():
    db = Durable()
    jid = db.submit("echo", {"n": 5})
    assert asyncio.run(durable.run_one(db, workflows.HANDLERS)) == "done"
    assert db.get(jid)["result"] == '{"risultato": 11}' and db.steps(jid) == ["a", "b"]


def test_learn_workflow_does_nothing_when_there_is_no_feedback():
    db = Durable()
    jid = db.submit("learn", {})
    assert asyncio.run(durable.run_one(db, workflows.HANDLERS)) == "done"
    assert "niente da elaborare" in db.get(jid)["result"]


def test_heal_workflow_resumes_without_repeating_the_expensive_step(monkeypatch, tmp_path):
    """Il passo 'heal' (QA + patch) si salva: se il processo muore dopo, alla ripresa non si rifa'."""
    calls = []

    async def fake_heal(site, patcher, attempts):
        calls.append(1)
        return {"healed": True, "prima": {"punteggio": 9, "problemi": []}, "dopo": {"punteggio": 0, "problemi": []}, "work_dir": "/tmp/x", "diff": "d"}

    monkeypatch.setattr(workflows.factory_heal, "heal", fake_heal)
    monkeypatch.setattr(workflows.factory_heal, "llm_patcher", lambda: None)
    db = Durable()
    jid = db.submit("heal_site", {"site_dir": str(tmp_path)})
    job = db.claim("morto")
    db.save_step(job["id"], "heal", asyncio.run(fake_heal(None, None, 2)))          # il worker muore subito dopo il passo
    calls.clear()
    with sqlite3.connect(db.path) as c:
        c.execute("UPDATE jobs SET lease_until=0")
    assert asyncio.run(durable.run_one(db, workflows.HANDLERS, "nuovo")) == "done"
    assert calls == [] and '"healed": true' in db.get(jid)["result"]
