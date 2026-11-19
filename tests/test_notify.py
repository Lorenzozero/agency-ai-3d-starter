import asyncio
import json
from datetime import datetime

import httpx
import pytest

from hlab import notify, powers
from hlab.events import Events

DAY = datetime(2026, 11, 3, 10, 0, tzinfo=notify.TZ)
FILES = {"docs/x.md": "ciao"}


@pytest.fixture(autouse=True)
def tg(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:test-token")
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "111,222")
    sent = []

    def handler(req: httpx.Request):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True})
    return sent, httpx.MockTransport(handler)


async def digest():
    return "☀️ riepilogo di prova"


async def no_uptime(persist=True):
    return [], {}


def no_plan(**kw):
    return None


def go(ev, now, transport, **kw):
    return asyncio.run(notify.run(ev, now, transport, digest=kw.pop("digest", digest), uptime_run=kw.pop("uptime_run", no_uptime), plan_fn=kw.pop("plan_fn", no_plan), **kw))


def test_first_run_is_silent_then_only_new_things_are_announced(tg):
    sent, tr = tg
    ev = Events()
    powers.propose(ev, "deployer", "github_pr", "o/r", "Vecchia modifica", {"files": FILES})
    assert go(ev, DAY, tr)["primo_avvio"] and sent == []                         # il passato non si racconta
    cid = powers.propose(ev, "deployer", "github_pr", "agency-ai/sds", "Nuova hero", {"files": FILES})
    ev.add_proposal("account-crm", "lead", "0a1b2c3d-0000-4000-8000-123456789abc", "notes", "x", "il cliente ha richiamato oggi")
    r = go(ev, DAY, tr)
    texts = " ".join(m["text"] for m in sent)
    assert r["inviati"] == 6 and {m["chat_id"] for m in sent} == {111, 222}          # 2 decisioni + riepilogo del mattino, una volta per chat autorizzata
    assert "Nuova hero" in texts and "le note" in texts and "il cliente ha richiamato" in texts
    btn = {b["callback_data"] for m in sent if "reply_markup" in m for row in m["reply_markup"]["inline_keyboard"] for b in row}
    assert btn == {f"hc:ok:{cid}", f"hc:no:{cid}", "hp:ok:1", "hp:no:1"}              # ogni decisione ha il suo Conferma/Annulla
    sent.clear()
    assert go(ev, DAY, tr)["inviati"] == 0 and sent == []                        # niente doppioni


def test_status_changes_are_told_in_plain_italian(tg):
    sent, tr = tg
    ev = Events()
    go(ev, DAY, tr)
    cid = powers.propose(ev, "deployer", "netlify_preview", "vetreria-alfa", "Anteprima nuova hero", {"files": FILES})
    go(ev, DAY, tr)
    powers._set(ev, cid, "previewed", {"anteprima": "https://d1--vetreria-alfa.netlify.app"})
    sent.clear()
    go(ev, DAY, tr)
    assert "pronta" in sent[0]["text"] and "https://d1--vetreria-alfa.netlify.app" in sent[0]["text"] and "restano tuoi" in sent[0]["text"]


def test_quiet_hours_hold_alerts_until_the_morning(tg):
    sent, tr = tg
    ev = Events()
    go(ev, DAY, tr)
    powers.propose(ev, "deployer", "github_pr", "o/r", "Modifica notturna", {"files": FILES})
    night = datetime(2026, 11, 3, 23, 30, tzinfo=notify.TZ)
    assert go(ev, night, tr) == {"inviati": 0, "in_attesa_orario": 1, "ora": "23:30"} and sent == []
    early = datetime(2026, 11, 4, 7, 0, tzinfo=notify.TZ)
    assert go(ev, early, tr)["inviati"] == 0
    assert go(ev, datetime(2026, 11, 4, 7, 45, tzinfo=notify.TZ), tr)["inviati"] == 2         # la mattina arriva, una volta


def test_morning_digest_is_sent_once_a_day_and_a_failing_crm_does_not_block_alerts(tg):
    sent, tr = tg
    ev = Events()
    go(ev, DAY, tr)
    go(ev, datetime(2026, 11, 4, 8, 5, tzinfo=notify.TZ), tr)
    assert any("riepilogo di prova" in m["text"] for m in sent)
    sent.clear()
    go(ev, datetime(2026, 11, 4, 9, 0, tzinfo=notify.TZ), tr)
    assert sent == []                                                              # gia' mandato oggi
    go(ev, datetime(2026, 11, 5, 8, 5, tzinfo=notify.TZ), tr, digest=notify.digest_text)    # CRM assente nei test: il riepilogo si degrada, non esplode
    assert sent and "Buongiorno" in sent[0]["text"]


def test_missing_token_fails_clearly_and_dry_run_sends_nothing(tg, monkeypatch):
    sent, tr = tg
    ev = Events()
    go(ev, DAY, tr)
    powers.propose(ev, "deployer", "github_pr", "o/r", "Modifica da annunciare", {"files": FILES})
    assert go(ev, DAY, tr, dry=True)["in_attesa_orario"] == 1 and sent == []
    monkeypatch.delenv("TELEGRAM_TOKEN")
    with pytest.raises(RuntimeError, match="TELEGRAM_TOKEN"):
        go(ev, DAY, tr)


def test_site_down_alerts_bypass_quiet_hours_but_other_uptime_news_waits(tg):
    sent, tr = tg
    ev = Events()
    go(ev, DAY, tr)
    night = datetime(2026, 11, 3, 23, 30, tzinfo=notify.TZ)

    async def up(persist=True):
        return [(True, "🔴 Atelier sembra giù"), (False, "🔐 certificato in scadenza")], {}
    go(ev, night, tr, uptime_run=up)
    assert [m["text"] for m in sent] == ["🔴 Atelier sembra giù"] * 2                                  # di notte solo il sito giù, una volta per chat
    sent.clear()
    go(ev, DAY, tr, uptime_run=up)
    assert any("certificato" in m["text"] for m in sent)

    async def broken(persist=True):
        raise RuntimeError("monitoraggio rotto")
    sent.clear()
    assert go(ev, DAY, tr, uptime_run=broken)["inviati"] >= 0                                       # un guasto del monitoraggio non ferma gli avvisi


def test_netlify_credit_alerts_fire_once_per_level_and_restart_with_the_new_period(tg):
    sent, tr = tg
    ev = Events()
    go(ev, DAY, tr)
    plan = {"piano": "Free", "crediti_inclusi": 300, "crediti_usati": 100, "fine_periodo": "2026-11-26T00:00:00"}
    fn = lambda **k: plan                                                                            # noqa: E731
    go(ev, DAY, tr, plan_fn=fn)
    assert not any("Netlify" in m["text"] for m in sent)                                             # 33%: niente
    plan["crediti_usati"] = 215                                                                      # 72%
    go(ev, DAY, tr, plan_fn=fn)
    assert sum("215 crediti su 300" in m["text"] for m in sent) == 2                                 # una volta per chat
    sent.clear()
    go(ev, DAY, tr, plan_fn=fn)
    assert sent == []                                                                                # stesso livello: non si ripete
    plan["crediti_usati"] = 280                                                                      # 93%: urgente anche di notte
    go(ev, datetime(2026, 11, 3, 23, 30, tzinfo=notify.TZ), tr, plan_fn=fn)
    assert sent == []                                                                                # di notte i crediti aspettano la mattina
    go(ev, datetime(2026, 11, 4, 9, 0, tzinfo=notify.TZ), tr, plan_fn=fn)
    assert any("280 crediti su 300" in m["text"] for m in sent)
    sent.clear()
    plan.update(fine_periodo="2026-12-26T00:00:00", crediti_usati=250)                               # nuovo mese: si riparte
    go(ev, datetime(2026, 12, 27, 9, 0, tzinfo=notify.TZ), tr, plan_fn=fn)
    assert any("250 crediti" in m["text"] for m in sent)


def test_critical_user_changes_are_flagged_and_confirmed_with_buttons_only(tg):
    sent, tr = tg
    ev = Events()
    go(ev, DAY, tr)
    pid = ev.add_proposal("account-crm", "user", "herems@agent.com", "is_active", "false", "account duplicato creato per errore")
    sent.clear()
    go(ev, DAY, tr)
    m = next(x for x in sent if "CRITICA" in x["text"])
    assert "herems@agent.com" in m["text"] and "false" in m["text"]
    assert [b["callback_data"] for b in m["reply_markup"]["inline_keyboard"][0]] == [f"hp:ok:{pid}", f"hp:no:{pid}"]


# ---- avvisi sui nuovi flussi della console ----

def _store(tmp_path):
    from hlab.docstore import Store
    return Store(tmp_path / "docs.db")


def _task(store, **kw):
    return store.add("tasks", {"tarea": "Scrivi la newsletter", "estado": "en_espera", "asignado": "commerciale", "createdBy": "x", **kw})


def test_new_agent_flows_are_announced_once_and_the_existing_ones_stay_silent(tg, tmp_path):
    sent, tr = tg
    ev, store = Events(), _store(tmp_path)
    _task(store, agentStatus="done")                                                  # c'era gia': non si racconta
    go(ev, DAY, tr, store=store)                                                      # primo avvio
    sent.clear()
    assert go(ev, DAY, tr, store=store)["inviati"] >= 0 and not [m for m in sent if "consegnato" in m["text"]]
    _task(store, tarea="Offerta Officina Rapida", agentStatus="needs_info", needsFull=True)
    _task(store, tarea="Brief del fabbro", agentStatus="needs_info", agentQuestion="Per quale cliente?")
    _task(store, tarea="Post di lancio", agentStatus="done")
    _task(store, tarea="Controllo siti", agentStatus="error", agentError="router spento")
    fid = ev.add_feedback("commerciale", "task", "9", "corrected", "troppo lungo")
    ev.add_rule("commerciale", "Scrivi risposte brevi: al massimo tre frasi per messaggio.", [fid])
    sent.clear()
    go(ev, DAY, tr, store=store)
    texts = " ".join(m["text"] for m in sent)
    for expected in ("chiede il consenso per eseguire «Offerta Officina Rapida»", "ha una domanda sul task «Brief del fabbro»: Per quale cliente?",
                     "ha consegnato «Post di lancio»", "ha avuto un problema con «Controllo siti»: router spento", "propone una regola per Commerciale"):
        assert expected in texts, expected
    sent.clear()
    go(ev, DAY, tr, store=store)
    assert not [m for m in sent if "consenso" in m["text"] or "consegnato" in m["text"]]          # una volta sola per voce


def test_task_alerts_wait_for_the_morning_and_are_not_lost(tg, tmp_path):
    sent, tr = tg
    ev, store = Events(), _store(tmp_path)
    go(ev, DAY, tr, store=store)                                                      # primo avvio
    _task(store, agentStatus="done")
    night = datetime(2026, 11, 3, 23, 30, tzinfo=notify.TZ)
    sent.clear()
    assert go(ev, night, tr, store=store)["in_attesa_orario"] >= 1 and not [m for m in sent if "consegnato" in m["text"]]
    go(ev, DAY, tr, store=store)
    assert [m for m in sent if "consegnato" in m["text"]]                             # al mattino arriva


def test_what_waits_for_you_is_counted_and_written_in_plain_italian(tmp_path):
    from hlab import taskrun
    store = _store(tmp_path)
    assert taskrun.waiting_line(taskrun.waiting(store)) == ""
    _task(store, agentStatus="done")
    _task(store, agentStatus="done")
    _task(store, agentStatus="needs_info", needsFull=True)
    _task(store, agentStatus="needs_info", agentQuestion="?")
    _task(store, agentStatus="error")
    _task(store, agentStatus="done", estado="completada")                              # chiuso: non aspetta piu' niente
    _task(store, agentStatus="done", hlab={"kind": "proposal", "id": 1})               # schede di sistema: non sono task di agenti
    counts = taskrun.waiting(store)
    assert counts == {"consent": 1, "question": 1, "delivery": 2, "error": 1}
    assert taskrun.waiting_line(counts) == "2 consegne da giudicare, 1 consenso da dare, 1 domanda a cui rispondere, 1 task con un problema"


def test_weekly_audit_speaks_only_when_something_is_wrong_and_remembers_last_week():
    from datetime import datetime
    mon, tue = datetime(2026, 11, 9, 10, 0, tzinfo=notify.TZ), datetime(2026, 11, 10, 10, 0, tzinfo=notify.TZ)
    good = {"riepilogo": {"sicurezza": "A", "seo": 90, "prestazioni": 80}, "priorita": []}
    bad = {"riepilogo": {"sicurezza": "F", "seo": 50, "prestazioni": 40}, "priorita": [{"gravita": "alta", "messaggio": "/.env esposto"}]}
    calls = []

    async def full(url):
        calls.append(url)
        return bad if "male" in url else good
    targets = lambda: {"Bene": "https://bene.test", "Male": "https://male.test"}      # noqa: E731
    st = {}
    out = asyncio.run(notify.weekly_audit(st, mon, targets, full))
    assert len(out) == 1 and "Male" in out[0] and "sicurezza F" in out[0] and "SEO 50" in out[0] and "/.env esposto" in out[0] and "Bene" not in out[0]
    assert st["audit_hist"]["Bene"]["sic"] == "A" and len(calls) == 2
    assert asyncio.run(notify.weekly_audit(st, mon, targets, full)) == [] and len(calls) == 2          # gia' fatto questa settimana
    assert asyncio.run(notify.weekly_audit({}, tue, targets, full)) == []                              # solo il lunedi
    worse = {"audit_hist": {"Bene": {"sic": "A", "seo": 90}}}
    async def drop(url):
        return {"riepilogo": {"sicurezza": "C", "seo": 70}, "priorita": []}
    out2 = asyncio.run(notify.weekly_audit(worse, mon, lambda: {"Bene": "https://bene.test"}, drop))
    assert "peggiorata (A → C)" in out2[0] and "era 90" in out2[0]
