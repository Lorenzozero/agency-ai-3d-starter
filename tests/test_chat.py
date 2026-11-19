import asyncio
import time

import pytest

from hlab import chat
from hlab.docstore import Store
from hlab.lean import Answer


class FakeLean:
    def __init__(self, text="Tutto ok", boom=None, intent="status"):
        self.text, self.boom, self.intent, self.asked, self.history, self.persona = text, boom, intent, [], None, None

    async def ask(self, text, history=None, persona=None):
        self.asked.append(text)
        self.history, self.persona = history, persona
        if self.boom:
            raise self.boom
        return Answer(self.text, self.intent)


from hlab import lean as _lean_mod

REAL_FACTS = _lean_mod.Lean._facts                                         # il vero riassunto del CRM: lo provano solo i test dedicati


@pytest.fixture(autouse=True)
def no_crm_facts(monkeypatch):
    async def none(self):
        return ""
    monkeypatch.setattr(_lean_mod.Lean, "_facts", none)                    # nelle altre prove nessuna lettura del CRM vero


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "docs.db")


def say(store, chat_id, text, **extra):
    return store.add(f"agentChats/{chat_id}/mensajes", {"from": "humano", "text": text, "status": "pending", "createdAt": {"__op": "serverTimestamp"}, **extra})


def replies(store, chat_id):
    return [r["data"] for r in store.query(f"agentChats/{chat_id}/mensajes", where=[["from", "==", "agente"]])]


def test_target_agent():
    names = {"coo": "COO", "commerciale": "Commerciale", "account-crm": "Account CRM"}
    assert chat.target_agent("commerciale", "ciao", names) == "commerciale"
    assert chat.target_agent("grupo", "ciao a tutti", names) == "coo"
    assert chat.target_agent("grupo", "@commerciale fai il preventivo", names) == "commerciale"
    assert chat.target_agent("grupo", "@Account puoi guardare?", names) == "account-crm"


def test_message_is_answered_once_in_italian(store):
    mid = say(store, "coo", "come va la situazione?")
    lean = FakeLean("Progetti: 3 attivi.")
    assert asyncio.run(chat.run_once(store, lean)) == 1
    r = replies(store, "coo")
    assert len(r) == 1 and r[0]["text"] == "Progetti: 3 attivi." and r[0]["agentId"] == "coo" and r[0]["replyTo"] == mid
    assert store.get(f"agentChats/coo/mensajes/{mid}")["status"] == "done"
    assert asyncio.run(chat.run_once(store, lean)) == 0                  # non risponde due volte
    assert len(lean.asked) == 1


def test_failure_becomes_a_clear_answer(store):
    say(store, "coo", "stato")
    asyncio.run(chat.run_once(store, FakeLean(boom=RuntimeError("crm giu"))))
    assert "Non sono riuscito a rispondere" in replies(store, "coo")[0]["text"]


def test_group_message_goes_to_the_tagged_agent_and_stale_work_is_retaken(store):
    mid = say(store, "grupo", "@commerciale preventivo?")
    store.update(f"agentChats/grupo/mensajes/{mid}", {"status": "working", "workStart": int((time.time() - 1000) * 1000)})
    asyncio.run(chat.run_once(store, FakeLean("ok")))
    assert replies(store, "grupo")[0]["agentId"] == "commerciale"


def test_crm_sync_fills_clients_and_info_cards_and_clears_old_ones(store):
    from hlab import api_sync

    class FakeCrm:
        async def _call(self, name, **a):
            return {"list_clients": [{"name": "Beta Srl"}, {"name": "Alfa Snc"}], "stale_projects": [{"id": "p9", "name": "Sito Gamma", "client": "Alfa Snc"}],
                    "list_deadlines": [{"kind": "progetto", "title": "Portale", "client": "Beta Srl", "due": "2026-11-06", "status": "in_sviluppo"}]}[name]
    r = api_sync.crm_sync_once(store, FakeCrm())
    assert r == {"clienti": 2, "schede": 2}
    assert store.get("config/clients")["list"] == ["Alfa Snc", "Beta Srl"]
    cards = [d["data"] for d in store.query("tasks") if (d["data"].get("hlab") or {}).get("kind") in ("deadline", "stale")]
    assert {c["estado"] for c in cards} == {"pendiente"} and any(c["tarea"].startswith("Scadenza") for c in cards) and any(c["tarea"].startswith("Progetto fermo") for c in cards)

    class Quiet(FakeCrm):
        async def _call(self, name, **a):
            return [] if name != "list_clients" else [{"name": "Beta Srl"}]
    api_sync.crm_sync_once(store, Quiet())
    assert not [d for d in store.query("tasks") if (d["data"].get("hlab") or {}).get("kind") in ("deadline", "stale")]


def test_tool_families_and_most_used_tool_per_agent(tmp_path):
    from hlab import api_sync
    from hlab.events import Events
    assert api_sync.family_of("mcp__crm__list_clients")["id"] == "crm"
    assert api_sync.family_of("mcp__prop_coo__propose")["id"] == "proposals"
    assert api_sync.family_of("WebSearch")["id"] == "web"
    assert api_sync.family_of("qualcosa_di_ignoto") is None
    ev = Events(tmp_path / "ev.db")
    for _ in range(3):
        ev.log("tool_done", "coo", "mcp__crm__list_clients", True)
    ev.log("tool_done", "coo", "WebSearch", True)
    class Spec:
        mcp, builtin = ["pricing"], []
    tops = api_sync.top_tools(ev, {"coo": Spec(), "nuovo": Spec()})
    assert tops["coo"]["id"] == "crm"                    # il piu' usato vince
    assert tops["nuovo"]["id"] == "pricing"              # senza storia: il primo strumento che gli e' stato dato


def test_live_work_appears_in_the_office_and_goes_away(store, tmp_path):
    from hlab import api_sync
    from hlab.events import Events
    ev = Events(tmp_path / "ev.db")
    ev.log("tool_allow", "coo", "mcp__crm__list_clients", True)
    assert api_sync.live_sync_once(store, ev) == 1
    t = store.get("tasks/live-coo")
    assert t["estado"] == "en_progreso" and t["asignado"] == "coo" and "CRM" in t["tarea"]
    assert store.get("config/agentWorker")["busyWith"] == "COO"
    assert api_sync.live_sync_once(store, ev, now=time.time() + api_sync.ACTIVE_S + 5) == 0     # fermo da un po': torna libero
    assert store.get("tasks/live-coo") is None
    assert store.get("config/agentWorker")["busyWith"] == ""


def test_answering_in_chat_puts_the_agent_to_work(store, tmp_path):
    from hlab.events import Events
    say(store, "coo", "come va?")
    asyncio.run(chat.run_once(store, FakeLean()))
    assert "coo" in Events().active_since(30)


# ---- motore dei task e modifiche dalla GUI ----

def put_task(store, **kw):
    return store.add("tasks", {"tarea": "Quanti lead abbiamo?", "descripcion": "", "estado": "pendiente", "asignado": "coo", "createdBy": "x", **kw})


def test_task_given_to_an_agent_is_done_and_delivered_for_approval(store):
    from hlab import taskrun
    tid = put_task(store)
    assert asyncio.run(taskrun.run_once(store, FakeLean("Ecco i lead"))) == 1
    t = store.get(f"tasks/{tid}")
    assert (t["estado"], t["agentStatus"], t["agentOutput"]) == ("en_espera", "done", "Ecco i lead")
    assert asyncio.run(taskrun.run_once(store, FakeLean())) == 0           # una consegna fatta non si rifa da sola


def test_task_failure_is_visible_and_sending_it_back_redoes_it_with_the_feedback(store):
    from hlab import taskrun
    tid = put_task(store)
    asyncio.run(taskrun.run_once(store, FakeLean(boom=RuntimeError("giu"))))
    t = store.get(f"tasks/{tid}")
    assert t["agentStatus"] == "error" and "RuntimeError" in t["agentError"]
    store.update(f"tasks/{tid}", {"estado": "en_revision", "feedback": "usa solo i lead nuovi"})
    lean = FakeLean("Fatto, solo nuovi")
    asyncio.run(taskrun.run_once(store, lean))
    assert "usa solo i lead nuovi" in lean.asked[0]
    assert store.get(f"tasks/{tid}")["agentStatus"] == "done"


def test_tasks_of_people_and_of_hlab_are_left_alone_and_reserve_agents_wait(store):
    from hlab import taskrun
    put_task(store, asignado="una-persona")
    put_task(store, hlab={"kind": "proposal", "id": 1})
    rid = put_task(store, asignado="commerciale")
    store.set("agents/commerciale", {"active": False})
    assert asyncio.run(taskrun.run_once(store, FakeLean())) == 1
    assert store.get(f"tasks/{rid}")["agentStatus"] == "queued"            # in riserva: in coda
    store.update("agents/commerciale", {"active": True})
    asyncio.run(taskrun.run_once(store, FakeLean("ok")))
    assert store.get(f"tasks/{rid}")["agentStatus"] == "done"


def test_gui_edits_to_agents_survive_the_sync_until_the_agent_file_changes(store, tmp_path):
    from hlab import api_sync
    from hlab.events import Events
    ev = Events(tmp_path / "ev2.db")
    api_sync.sync_once(store, ev, services={})
    store.update("agents/coo", {"active": False, "photo": "x.png"})
    api_sync.sync_once(store, ev, services={})
    a = store.get("agents/coo")
    assert a["active"] is False and a["photo"] == "x.png"                  # il giro successivo non annulla "Metti in riserva"
    store.update("agents/coo", {"specHash": "vecchio"})                    # simula un file agente modificato
    api_sync.sync_once(store, ev, services={})
    assert store.get("agents/coo")["active"] is True


def test_agent_in_reserve_does_not_answer_in_chat(store):
    store.set("agents/coo", {"active": False})
    say(store, "coo", "ci sei?")
    lean = FakeLean()
    asyncio.run(chat.run_once(store, lean))
    assert lean.asked == [] and "riserva" in replies(store, "coo")[0]["text"]


def test_new_agent_from_the_gui_becomes_a_real_agent_file_in_reserve(store, tmp_path):
    from hlab import api_sync, registry
    d = tmp_path / "agents"
    d.mkdir()
    (d / "coo.md").write_text("---\nname: coo\ndescription: Coordinatore della squadra di agenti.\n---\nSei il coordinatore, rispondi in italiano e delega agli specialisti.\n", encoding="utf-8")
    store.set("agents/zzRandomId", {"name": "Ads Studio", "role": "Campagne", "bio": "", "mission": "Gestisci le campagne pubblicitarie dei clienti con attenzione ai costi.", "active": False})
    name = api_sync.create_agent_file(store, "agents/zzRandomId", store.get("agents/zzRandomId"), agents_dir=d)
    assert name == "ads-studio"
    spec = registry.load_all(d)["ads-studio"]
    assert spec.problems() == [] and spec.autonomy == 1 and "campagne" in spec.prompt.lower()
    assert store.get("agents/zzRandomId") is None and store.get("agents/ads-studio")["active"] is False
    again = api_sync.create_agent_file(store, "agents/y", {"name": "Ads Studio", "role": "x"}, agents_dir=d)   # stesso nome: non sovrascrive
    assert again == "ads-studio-2" and len(registry.load_all(d)) == 3


# ---- briefing ----

def test_briefing_text_uses_real_feedback_and_waiting_items():
    from hlab import briefing
    fb = [{"agent": "coo", "verdict": "accepted", "comment": "", "processed": 0},
          {"agent": "commerciale", "verdict": "rejected", "comment": "troppo lungo, taglia", "processed": 0},
          {"agent": "commerciale", "verdict": "accepted", "comment": "", "processed": 1}]
    t = briefing.compose("05/11/2026", "Progetti: 5", "Officina Rapida il 15/11", fb, {"coo": "COO", "commerciale": "Commerciale"}, rules_waiting=2, proposals_waiting=1)
    assert "Commerciale: 1 accettati, 1 respinti" in t and "troppo lungo, taglia" in t
    assert "1 proposte da approvare, 2 regole proposte" in t and "non diventa attiva senza il tuo ok" in t
    assert "niente." in briefing.compose("d", "s", "x", [], {}, 0, 0) and "giudica i task" in briefing.compose("d", "s", "x", [], {}, 0, 0)


def test_briefing_runs_daily_on_weekdays_at_nine_and_on_request_in_chat(store, tmp_path):
    from datetime import datetime
    from hlab import briefing
    from hlab.events import Events
    z = briefing.TZ
    assert briefing.due(None, datetime(2026, 11, 5, 9, 1, tzinfo=z))            # giovedi' 9:01
    assert not briefing.due(None, datetime(2026, 11, 5, 8, 59, tzinfo=z))      # troppo presto
    assert not briefing.due("2026-11-05", datetime(2026, 11, 5, 10, 0, tzinfo=z))   # gia' fatto oggi
    assert not briefing.due(None, datetime(2026, 11, 7, 10, 0, tzinfo=z))      # sabato
    say(store, "grupo", "fai un briefing")
    asyncio.run(chat.run_once(store, FakeLean("Progetti: 5")))
    b = store.get("config/briefing")
    assert b["until"] - b["startedAt"] == briefing.MEETING_S * 1000 and "Briefing del" in b["text"]
    assert any("Briefing del" in r["text"] for r in replies(store, "grupo"))


# ---- apprendimento: feedback dai task, regole in GUI, elaborazione con tetto di costo ----

def test_verdict_on_an_agent_task_becomes_feedback_with_the_comment(store, tmp_path):
    from hlab import api_sync
    from hlab.events import Events
    from hlab.learning import Learning
    ev = Events(tmp_path / "fb.db")
    api_sync.attach_feedback_hooks(store, Learning(ev))
    a, b = put_task(store, asignado="commerciale", estado="en_espera", agentStatus="done"), put_task(store, asignado="commerciale", estado="en_espera", agentStatus="done")
    store.update(f"tasks/{a}", {"estado": "completada"})
    store.update(f"tasks/{b}", {"feedback": "troppo lungo, taglia", "estado": "en_revision"})
    rows = ev.feedback_rows()
    assert [(r["verdict"], r["comment"]) for r in rows] == [("accepted", ""), ("corrected", "troppo lungo, taglia")]
    c = put_task(store, asignado="una-persona", estado="en_espera", agentStatus="done")
    store.update(f"tasks/{c}", {"estado": "completada"})
    assert len(ev.feedback_rows()) == 2                                      # solo i task consegnati da agenti


def test_proposed_rules_show_as_cards_and_can_be_approved(tmp_path, monkeypatch):
    from hlab import api_sync, config
    from hlab.events import Events
    from hlab.learning import Learning
    ev = Events(tmp_path / "ru.db")
    fid = ev.add_feedback("commerciale", "task", "1", "corrected", "troppo lungo")
    rid = ev.add_rule("commerciale", "Scrivi risposte brevi: al massimo tre frasi per messaggio.", [fid])
    docs = api_sync.task_docs(ev)
    card = docs[f"r{rid}"]
    assert card["estado"] == "en_espera" and card["asignado"] == "commerciale" and card["hlab"] == {"kind": "rule", "id": rid}
    monkeypatch.setattr(config, "EVENTS_DB", tmp_path / "ru.db")
    assert "attivata" in api_sync._decide_rule(True, rid)
    assert Learning(ev).active_rules("commerciale") == ["Scrivi risposte brevi: al massimo tre frasi per messaggio."]
    assert api_sync.task_docs(ev)[f"r{rid}"]["estado"] == "completada"


def test_learning_commands_cost_cap_and_nothing_to_learn(store, tmp_path):
    from hlab import briefing
    from hlab.events import Events
    ev = Events(tmp_path / "le.db")
    calls = []

    async def fake_runner(prompt, on_text=None, budget=None):
        calls.append(budget)
        on_text("Proposta 1 regola per Commerciale.")
        return {"cost_usd": 0.04}
    assert "spento" in briefing.handle_learning(store, "disattiva apprendimento", "grupo", ev)
    assert "acceso" in briefing.handle_learning(store, "attiva l'apprendimento", "grupo", ev) and store.get("config/briefing")["learnAuto"] is True
    assert briefing.handle_learning(store, "ciao", "grupo", ev) is None
    assert "Niente da elaborare" in briefing.handle_learning(store, "elabora i feedback", "grupo", ev, fake_runner)      # nessun commento: costo 0
    assert calls == [] and briefing.learn_note(store, ev) == ""
    ev.add_feedback("commerciale", "task", "7", "corrected", "usa un tono piu' caldo")
    assert "0.30 $" in briefing.learn_note(store, ev) and "Automatico: acceso" in briefing.learn_note(store, ev)
    msg = asyncio.run(briefing.run_learning(store, ev, runner_fn=fake_runner))
    assert calls == [briefing.LEARN_CAP_USD] and "Costo: 0.04 $" in msg and "Proposta 1 regola" in msg
    assert "Costo: 0.04 $" in replies(store, "grupo")[-1]["text"]


def test_learning_failure_is_reported_not_silent(store, tmp_path):
    from hlab import briefing
    from hlab.events import Events
    ev = Events(tmp_path / "lf.db")
    ev.add_feedback("coo", "task", "1", "corrected", "sbagliato il cliente")

    async def boom(prompt, on_text=None, budget=None):
        raise RuntimeError("router spento")
    assert "RuntimeError" in asyncio.run(briefing.run_learning(store, ev, runner_fn=boom))


def test_auto_learning_runs_once_a_day_after_the_briefing_only_when_switched_on(store, tmp_path):
    from hlab import briefing
    from hlab.events import Events
    ev = Events(tmp_path / "la.db")
    ev.add_feedback("coo", "task", "1", "corrected", "sbagliato il cliente")
    calls = []

    async def fake_runner(prompt, on_text=None, budget=None):
        calls.append(1)
        return {"cost_usd": 0}

    async def go():
        orig = briefing.run_learning

        async def patched(s, e=None, chat_id="grupo", runner_fn=None, cap=None):
            return await orig(s, e, chat_id, fake_runner, cap)
        briefing.run_learning = patched
        try:
            await briefing.run(store, FakeLean("ok"), ev)                    # spento: niente costi
            await asyncio.sleep(0.05)
            assert calls == []
            store.set("config/briefing", {"learnAuto": True}, merge=True)
            await briefing.run(store, FakeLean("ok"), ev)
            await asyncio.sleep(0.05)
            await briefing.run(store, FakeLean("ok"), ev)                    # secondo briefing dello stesso giorno: non rilancia
            await asyncio.sleep(0.05)
        finally:
            briefing.run_learning = orig
    asyncio.run(go())
    assert calls == [1]


def test_approvals_with_generic_comments_do_not_trigger_paid_learning(tmp_path):
    from hlab import briefing
    from hlab.events import Events
    ev = Events(tmp_path / "ap.db")
    ev.add_feedback("project-manager", "proposal", "3", "accepted", "richiesta esplicita di Lorenzo in chat")
    ev.add_feedback("deployer", "proposal", "1", "rejected", "")
    assert briefing.learnable(ev) == []
    ev.add_feedback("deployer", "task", "9", "rejected", "il titolo era sbagliato")
    assert len(briefing.learnable(ev)) == 1


# ---- correzioni al motore dei task (confronto con il motore di Agentes IA 3D) ----

def test_a_task_left_running_by_a_restart_goes_back_in_the_queue_and_gets_done(store):
    from hlab import taskrun
    stuck = put_task(store, estado="en_progreso", agentStatus="running")
    other = put_task(store, estado="en_progreso", agentStatus="running", hlab={"kind": "live", "id": "coo"})
    assert asyncio.run(taskrun.run_once(store, FakeLean())) == 0               # finche' non si fa il recupero, resta ferma
    assert taskrun.recover(store) == 1                                         # le schede hlab non si toccano
    assert store.get(f"tasks/{stuck}")["agentStatus"] == "queued" and store.get(f"tasks/{other}")["agentStatus"] == "running"
    assert asyncio.run(taskrun.run_once(store, FakeLean("Fatto dopo il riavvio"))) == 1
    t = store.get(f"tasks/{stuck}")
    assert (t["agentStatus"], t["estado"], t["agentOutput"]) == ("done", "en_espera", "Fatto dopo il riavvio")


def test_redo_carries_the_previous_output_and_the_feedback(store):
    from hlab import taskrun
    tid = put_task(store, tarea="Testo della newsletter", descripcion="breve")
    asyncio.run(taskrun.run_once(store, FakeLean("Primo testo troppo lungo")))
    store.update(f"tasks/{tid}", {"estado": "en_revision", "feedback": "accorcia a due righe"})
    lean = FakeLean("Testo corto")
    asyncio.run(taskrun.run_once(store, lean))
    asked = lean.asked[0]
    assert "Testo della newsletter" in asked and "Primo testo troppo lungo" in asked and "accorcia a due righe" in asked
    assert "Risultato precedente" in asked
    assert "Risultato precedente" not in taskrun.prompt_of({"tarea": "x", "agentStatus": "done"})          # senza risultato, niente sezione vuota
    assert len(taskrun.prompt_of({"tarea": "x", "agentStatus": "done", "agentOutput": "a" * 20000})) < 6500   # il risultato lungo si taglia


# ---- esecuzione completa: consenso, tetti, domande ----

class FakeRunner:
    def __init__(self, text="Ecco il testo completo.", cost=0.08, boom=None):
        self.text, self.cost, self.boom, self.calls = text, cost, boom, []

    async def __call__(self, prompt, on_text=None, budget=None):
        self.calls.append((prompt, budget))
        if self.boom:
            raise self.boom
        on_text(self.text)
        return {"cost_usd": self.cost}


def test_free_request_asks_for_consent_and_spends_nothing(store):
    from hlab import taskrun
    tid = put_task(store, tarea="Scrivi il testo della newsletter", asignado="commerciale")
    r = FakeRunner()
    asyncio.run(taskrun.run_once(store, FakeLean("risposta breve", intent="chat"), r))
    t = store.get(f"tasks/{tid}")
    assert r.calls == [] and (t["agentStatus"], t["estado"], t["needsFull"]) == ("needs_info", "en_espera", True)
    assert "0.50 $" in t["agentQuestion"] and "Autorizza esecuzione completa" in t["agentQuestion"]
    assert asyncio.run(taskrun.run_once(store, FakeLean(), r)) == 0              # in attesa di te: non si rifa da sola


def test_authorized_full_run_delegates_records_cost_and_keeps_consent(store):
    from hlab import taskrun
    tid = put_task(store, tarea="Scrivi il testo della newsletter", asignado="commerciale", cliente="Officina Rapida")
    asyncio.run(taskrun.run_once(store, FakeLean(intent="chat"), FakeRunner()))
    store.update(f"tasks/{tid}", {"fullRunOk": True, "needsFull": False, "estado": "en_revision"})    # il bottone della GUI
    r, lean = FakeRunner(), FakeLean(intent="chat")
    asyncio.run(taskrun.run_once(store, lean, r))
    prompt, budget = r.calls[0]
    assert lean.asked == [] and budget == taskrun.FULL_BUDGET_USD                # consenso dato: niente percorso leggero
    assert "subagente 'commerciale'" in prompt and "Officina Rapida" in prompt and "Scrivi il testo della newsletter" in prompt
    t = store.get(f"tasks/{tid}")
    assert (t["agentStatus"], t["estado"], t["agentOutput"]) == ("done", "en_espera", "Ecco il testo completo.")
    assert t["agentCostUsd"] == 0.08 and t["agentRuns"] == 1 and t["fullRunOk"] is True
    assert taskrun.spent_today(store) == 0.08


def test_agent_question_pauses_and_the_answer_restarts_it(store):
    from hlab import taskrun
    tid = put_task(store, tarea="Prepara l'offerta", asignado="coo", fullRunOk=True)
    asyncio.run(taskrun.run_once(store, FakeLean(), FakeRunner("DOMANDA: per quale cliente?")))
    t = store.get(f"tasks/{tid}")
    assert (t["agentStatus"], t["agentQuestion"], t["needsFull"]) == ("needs_info", "per quale cliente?", False)
    store.update(f"tasks/{tid}", {"feedback": "Officina Rapida", "estado": "en_revision"})
    r = FakeRunner("Offerta pronta")
    asyncio.run(taskrun.run_once(store, FakeLean(), r))
    assert "per quale cliente?" in r.calls[0][0] and "Officina Rapida" in r.calls[0][0] and "Sei tu l'agente incaricato" in r.calls[0][0]
    assert store.get(f"tasks/{tid}")["agentStatus"] == "done"


def test_chat_switch_runs_free_requests_by_itself_but_the_daily_cap_still_holds(store):
    from hlab import taskrun
    assert "accesa" in taskrun.handle_engine(store, "attiva esecuzione completa") and store.get("config/engine")["fullAuto"] is True
    tid = put_task(store, tarea="Scrivi un post", asignado="commerciale")
    r = FakeRunner(cost=1.4)
    asyncio.run(taskrun.run_once(store, FakeLean(intent="chat"), r))
    assert store.get(f"tasks/{tid}")["agentStatus"] == "done" and taskrun.spent_today(store) == 1.4
    tid2 = put_task(store, tarea="Scrivi un altro post", asignado="commerciale")
    r2 = FakeRunner()
    asyncio.run(taskrun.run_once(store, FakeLean(intent="chat"), r2))
    t2 = store.get(f"tasks/{tid2}")
    assert r2.calls[0][1] == round(taskrun.FULL_DAILY_USD - 1.4, 2) or r2.calls[0][1] < taskrun.FULL_BUDGET_USD     # il budget si restringe a cio' che resta
    store.set("config/engine", {"spentUsd": taskrun.FULL_DAILY_USD}, merge=True)
    tid3 = put_task(store, tarea="Scrivi il terzo post", asignado="commerciale")
    r3 = FakeRunner()
    asyncio.run(taskrun.run_once(store, FakeLean(intent="chat"), r3))
    assert r3.calls == [] and "Tetto giornaliero" in store.get(f"tasks/{tid3}")["agentQuestion"]
    assert "spenta" in taskrun.handle_engine(store, "disattiva esecuzione completa") and taskrun.handle_engine(store, "ciao") is None


def test_full_run_failure_is_reported_and_known_requests_never_pay(store):
    from hlab import taskrun
    tid = put_task(store, tarea="Scrivi un post", asignado="commerciale", fullRunOk=True)
    asyncio.run(taskrun.run_once(store, FakeLean(), FakeRunner(boom=RuntimeError("router spento"))))
    t = store.get(f"tasks/{tid}")
    assert t["agentStatus"] == "error" and "RuntimeError" in t["agentError"] and t["fullRunOk"] is True
    sid = put_task(store, tarea="Come va la situazione?", asignado="coo")
    r = FakeRunner()
    asyncio.run(taskrun.run_once(store, FakeLean("Progetti: 5", intent="status"), r))
    s = store.get(f"tasks/{sid}")
    assert r.calls == [] and s["agentStatus"] == "done" and s["agentCostUsd"] == 0 and s["agentRuns"] == 1


def test_cost_is_the_real_router_spend_not_the_sdk_estimate_and_only_the_final_text_is_delivered(store):
    import json
    from hlab import config, taskrun
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    rows = [{"ts": now - 5000, "cost": 9.0}, {"ts": now, "cost": 0.0}, {"ts": now, "cost": 0.003}]        # la prima e' di molto prima: non conta
    (config.DATA_DIR / "router.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert taskrun.real_cost(now - 60, now + 5, nominal=0.24) == 0.003
    (config.DATA_DIR / "router.jsonl").unlink()
    assert taskrun.real_cost(now - 60, now + 5, nominal=0.24) == 0.24                                        # senza registro vale la stima dell'SDK

    class Runner(FakeRunner):
        async def __call__(self, prompt, on_text=None, budget=None):
            on_text("Delego al subagente, torno appena consegna.")
            on_text("Aspetto il tuo ok.")
            return {"cost_usd": 0.24, "result": "Benvenuto in Agency AI."}
    tid = put_task(store, tarea="Scrivi un benvenuto", asignado="commerciale", fullRunOk=True)
    asyncio.run(taskrun.run_once(store, FakeLean(), Runner()))
    t = store.get(f"tasks/{tid}")
    assert t["agentOutput"] == "Benvenuto in Agency AI." and t["agentCostUsd"] == 0.24           # qui nessun registro: stima
    assert "SOLO il testo consegnato" in taskrun.full_prompt({"tarea": "x"}, "coo", "COO")


# ---- delega del COO: schede in Pipeline ----

BLOCK = ('Divido il lavoro in tre.\n```tareas\n[{"agente": "commerciale", "titulo": "Bozza offerta", "descripcion": "Prepara la bozza", "prioridad": "alta"},'
         ' {"agente": "account-crm", "titulo": "Scheda cliente", "descripcion": "Riassumi il cliente"},'
         ' {"agente": "fantasma", "titulo": "Cosa strana", "descripcion": "x"}]\n```')


def test_coo_splits_the_work_into_pipeline_cards_for_the_right_agents(store):
    from hlab import taskrun
    pid = put_task(store, tarea="Prepara tutto per il cliente Officina Rapida", asignado="coo", cliente="Officina Rapida", fullRunOk=True)
    asyncio.run(taskrun.run_once(store, FakeLean(), FakeRunner(BLOCK)))
    cards = [r["data"] for r in store.query("tasks") if r["data"].get("delegadoPor") == pid]
    assert sorted((c["asignado"], c["tarea"], c["estado"]) for c in cards) == [("account-crm", "Scheda cliente", "pendiente"), ("commerciale", "Bozza offerta", "pendiente")]
    assert {c["cliente"] for c in cards} == {"Officina Rapida"} and {c["createdBy"] for c in cards} == {"coo"}
    assert next(c for c in cards if c["asignado"] == "commerciale")["prioridad"] == "alta"
    p = store.get(f"tasks/{pid}")
    assert p["agentStatus"] == "done" and "Bozza offerta" in p["agentOutput"] and "fantasma" in p["agentOutput"] and "Non creati" in p["agentOutput"]
    assert any("Bozza offerta" in r["text"] for r in replies(store, "grupo"))
    asyncio.run(taskrun.run_once(store, FakeLean("Tutto ok"), FakeRunner()))               # il giro dopo: gli agenti prendono le loro schede
    kids = [r["data"] for r in store.query("tasks") if r["data"].get("delegadoPor") == pid]
    assert {k["agentStatus"] for k in kids} == {"needs_info"} and all(k["needsFull"] for k in kids)         # sono ordini di lavoro: ognuno chiede il suo consenso


def test_delegation_is_limited_deduplicated_not_chained_and_a_broken_block_is_just_text(store):
    from hlab import taskrun
    team = {"commerciale": "x", "account-crm": "y"}
    items = [{"agente": "commerciale", "titulo": f"Task {i}"} for i in range(8)]
    made, skipped = taskrun.create_delegated(store, "p1", {}, items, team)
    assert len(made) == taskrun.MAX_DELEGATED and len(skipped) == 3
    made2, skipped2 = taskrun.create_delegated(store, "p1", {}, [{"agente": "commerciale", "titulo": "task 0"}], team)
    assert made2 == [] and "uguale" in skipped2[0]                                       # niente doppioni tra i task aperti
    assert "```tareas" in taskrun.full_prompt({"tarea": "x"}, "coo", "COO", team)
    assert "```tareas" not in taskrun.full_prompt({"tarea": "x", "delegadoPor": "p1"}, "coo", "COO", team)    # un task nato da delega non delega ancora
    assert "```tareas" not in taskrun.full_prompt({"tarea": "x"}, "commerciale", "Commerciale", team)
    assert taskrun.parse_delegation("ciao\n```tareas\n[rotto\n```") == ("ciao\n```tareas\n[rotto\n```", None)
    tid = put_task(store, tarea="Organizza", asignado="coo", fullRunOk=True)
    asyncio.run(taskrun.run_once(store, FakeLean(), FakeRunner("Ecco il piano.\n```tareas\n[rotto\n```")))
    assert store.get(f"tasks/{tid}")["agentStatus"] == "done" and "Ecco il piano" in store.get(f"tasks/{tid}")["agentOutput"]


def test_reserve_agents_are_not_in_the_team_the_coo_can_delegate_to(store):
    from hlab import taskrun
    store.set("agents/commerciale", {"active": False})
    team = taskrun.roster(store)
    assert "commerciale" not in team and "coo" not in team and "account-crm" in team


def test_full_run_retries_once_on_a_passing_error_and_reports_the_detail_if_it_fails_twice(store, monkeypatch):
    from hlab import taskrun
    orig_sleep = asyncio.sleep
    monkeypatch.setattr(taskrun.asyncio, "sleep", lambda s: orig_sleep(0))                    # niente attesa vera nei test

    class Flaky(FakeRunner):
        async def __call__(self, prompt, on_text=None, budget=None):
            self.calls.append((prompt, budget))
            if len(self.calls) == 1:
                raise RuntimeError("passaggio fallito")
            on_text("Fatto al secondo tentativo")
            return {"cost_usd": 0}
    tid = put_task(store, tarea="Scrivi un post", asignado="commerciale", fullRunOk=True)
    r = Flaky()
    asyncio.run(taskrun.run_once(store, FakeLean(), r))
    assert len(r.calls) == 2 and store.get(f"tasks/{tid}")["agentStatus"] == "done"
    tid2 = put_task(store, tarea="Scrivi un altro post", asignado="commerciale", fullRunOk=True)
    r2 = FakeRunner(boom=RuntimeError("router spento"))
    asyncio.run(taskrun.run_once(store, FakeLean(), r2))
    t2 = store.get(f"tasks/{tid2}")
    assert len(r2.calls) == 2 and t2["agentStatus"] == "error" and "router spento" in t2["agentError"]


def test_work_orders_skip_the_rule_path_and_questions_still_use_it(store):
    from hlab import taskrun
    lean = FakeLean("dati del cliente", intent="client")                      # il percorso leggero lo scambierebbe per "info sul cliente"
    tid = put_task(store, tarea="Messaggio di benvenuto", descripcion="Scrivi un messaggio per il cliente Officina Rapida", asignado="commerciale")
    asyncio.run(taskrun.run_once(store, lean, FakeRunner()))
    t = store.get(f"tasks/{tid}")
    assert lean.asked == [] and t["agentStatus"] == "needs_info" and t["needsFull"] is True            # chiede il consenso per l'esecuzione completa
    for title in ("Come va la situazione?", "Scadenze di questa settimana", "Quanti lead abbiamo?"):
        assert not taskrun.WORK_ORDER_RE.search(title)
    for title in ("Scheda cliente. Riassumi cosa sappiamo", "Bozza offerta", "Prepara l'offerta", "Scrivi un post"):
        assert taskrun.WORK_ORDER_RE.search(title)


# ---- contesto nella chat: cronologia e identita' dell'agente ----

def talk(store, cid, who, text, ms):
    if who == "humano":
        return store.add(f"agentChats/{cid}/mensajes", {"from": "humano", "text": text, "status": "done", "authorName": "Lorenzo", "createdAt": {"__ts": ms}})
    return store.add(f"agentChats/{cid}/mensajes", {"from": "agente", "agentId": who, "agentName": who.upper(), "text": text, "createdAt": {"__ts": ms}})


def test_agent_gets_the_last_messages_and_its_own_identity(store):
    talk(store, "coo", "humano", "come va la situazione?", 1000)
    talk(store, "coo", "coo", "Progetti: 5 in sviluppo.", 2000)
    cur = say(store, "coo", "e i lead nuovi?", createdAt={"__ts": 3000})
    lean = FakeLean("Lead nuovi: 17")
    asyncio.run(chat.run_once(store, lean))
    assert lean.history == [("Lorenzo", "come va la situazione?"), ("COO", "Progetti: 5 in sviluppo.")]
    assert lean.persona.startswith("Sei COO, agente di Agency AI") and "Lorenzo" in lean.persona
    assert store.get(f"agentChats/coo/mensajes/{cur}")["status"] == "done"
    say(store, "coo", "e le scadenze?", createdAt={"__ts": 9000})                              # il giro dopo: ricorda anche lo scambio appena fatto
    asyncio.run(chat.run_once(store, lean))
    assert lean.history[-2:] == [("Lorenzo", "e i lead nuovi?"), ("COO", "Lead nuovi: 17")] and len(lean.history) == 4


def test_pending_messages_and_the_current_one_are_not_part_of_the_context(store):
    talk(store, "coo", "humano", "ciao", 1000)
    cur = say(store, "coo", "domanda attuale", createdAt={"__ts": 2000})
    say(store, "coo", "altra in coda", createdAt={"__ts": 3000})
    assert chat.history_for(store, "coo", cur, chat.agent_names()) == [("Lorenzo", "ciao")]


def test_history_keeps_only_the_last_sixteen_and_labels_the_speakers(store):
    for i in range(20):
        talk(store, "grupo", "humano" if i % 2 == 0 else "commerciale", f"messaggio {i}", 1000 + i)
    h = chat.history_for(store, "grupo", "nessuno", chat.agent_names())
    assert len(h) == chat.HISTORY_N == 16 and h[0] == ("Lorenzo", "messaggio 4") and h[-1] == ("COMMERCIALE", "messaggio 19")


def test_lean_uses_history_and_persona_only_where_a_model_answers(tmp_path):
    from hlab import lean as L
    from hlab.events import Events
    seen = []

    async def fake(prompt, system, max_tokens):
        seen.append((prompt, system, max_tokens))
        return "chat" if len(seen) % 2 == 1 else "Certo, ne parlo con il team."
    eng = L.Lean(db=None, events=Events(tmp_path / "l.db"), complete=fake)
    hist = [("Lorenzo", "stiamo lavorando al sito di Officina Rapida"), ("COO", "Si', scade il 15.")]
    a = asyncio.run(eng.ask("e quello di cui parlavamo prima?", history=hist, persona="Sei COO, agente di Agency AI."))
    assert a.intent == "chat" and a.text == "Certo, ne parlo con il team."
    classify_prompt, chat_prompt, chat_system = seen[0][0], seen[1][0], seen[1][1]
    assert "Lorenzo: stiamo lavorando al sito di Officina Rapida" in classify_prompt and "Nuovo messaggio: e quello di cui parlavamo prima?" in chat_prompt
    assert chat_system.startswith("Sei COO") and "capire a cosa si riferisce" in chat_system
    assert "NON hai accesso al CRM" in chat_system and "non inventare mai" in chat_system        # in chat non si inventano clienti, prezzi o numeri
    seen.clear()
    asyncio.run(eng.ask("e quello di cui parlavamo prima?"))                                   # senza contesto: comportamento di prima
    assert seen[0][0] == "e quello di cui parlavamo prima?" and "massimo 3 frasi" in seen[1][1]
    lean_calls = []
    rules = L.Lean(db=None, events=Events(tmp_path / "l2.db"), complete=lambda *a: lean_calls.append(a))
    assert L.detect("come va la situazione?")[0] == "status"                                  # le regole non guardano la cronologia: 0 token


def test_history_block_drops_the_oldest_to_fit_and_flattens_lines():
    from hlab.lean import history_block
    h = [("A", "primo " * 60), ("B", "due\nrighe  e spazi"), ("C", "ultimo")]
    out = history_block(h, 80)
    assert out == "B: due righe e spazi\nC: ultimo" and history_block(None) == "" and history_block([("A", "x")]) == "A: x"


def test_the_light_classifier_can_never_start_quotes_sites_leads_or_client_sheets(tmp_path):
    from hlab import lean as L
    from hlab.events import Events
    for wrong in ("quote", "site", "lead_create", "client"):
        calls = []

        async def fake(prompt, system, max_tokens, wrong=wrong):
            calls.append(system)
            return wrong if "classifica" in system.lower() else "Ti consiglio un tono caldo e diretto."
        a = asyncio.run(L.Lean(db=None, events=Events(tmp_path / f"c{wrong}.db"), complete=fake).ask("Che tono mi consigli per presentarci?", persona="Sei COO."))
        assert a.intent == "chat" and a.text == "Ti consiglio un tono caldo e diretto." and len(calls) == 2           # nessuna bozza, nessun costo extra


def test_an_empty_conversational_reply_is_retried_once_with_more_room(tmp_path):
    from hlab import lean as L
    from hlab.events import Events
    seen = []

    async def fake(prompt, system, max_tokens):
        seen.append(max_tokens)
        if "classifica" in system.lower():
            return "chat"
        return "" if len(seen) == 2 else "Ecco la mia idea."
    a = asyncio.run(L.Lean(db=None, events=Events(tmp_path / "e.db"), complete=fake).ask("Dammi un'idea", persona="Sei COO."))
    assert a.text == "Ecco la mia idea." and seen == [300, 700, 1000]


# ---- gruppo: piu' agenti che si rispondono ----

class RoleLean:
    """Risponde in base a chi parla (lo si legge dalla persona): permette di scrivere la conversazione che ci si aspetta."""
    def __init__(self, script):
        self.script, self.calls = script, []

    async def ask(self, text, history=None, persona=None):
        who = (persona or "Sei ?,").split(",")[0].replace("Sei ", "")
        self.calls.append((who, text, history, persona))
        return Answer(self.script.get(who, "ok"), "chat")


def group_replies(store):
    return [(r["data"]["agentName"], r["data"]["text"]) for r in store.query("agentChats/grupo/mensajes", where=[["from", "==", "agente"]], order=[["createdAt", "asc"]])]


def test_group_without_mention_goes_to_the_coo_and_with_mentions_to_each_cited_agent(store):
    assert chat.mentioned("ciao @commerciale e @Account, poi @commerciale ancora", chat.agent_names()) == ["commerciale", "account-crm"]
    assert chat.mentioned("scrivi a info@pizzeria.it", chat.agent_names()) == []
    lean = RoleLean({"COO": "Ci penso io."})
    say(store, "grupo", "come procediamo?", createdAt={"__ts": 1000})
    asyncio.run(chat.run_once(store, lean))
    assert [w for w, *_ in lean.calls] == ["COO"] and "chat di squadra" in lean.calls[0][3] and "Commerciale" in lean.calls[0][3]
    say(store, "grupo", "@commerciale e @account-crm che ne pensate?", createdAt={"__ts": 9000})
    lean2 = RoleLean({"Commerciale": "Io proporrei una landing.", "Account CRM": "Il cliente ha gia' un sito."})
    asyncio.run(chat.run_once(store, lean2))
    assert [w for w, *_ in lean2.calls] == ["Commerciale", "Account CRM"]
    assert group_replies(store)[-2:] == [("Commerciale", "Io proporrei una landing."), ("Account CRM", "Il cliente ha gia' un sito.")]
    assert any(h for w, t, h, p in lean2.calls if w == "Account CRM" and ("Commerciale", "Io proporrei una landing.") in (h or []))   # il secondo sente il primo


def test_an_agent_can_call_a_colleague_but_only_two_hops_and_nobody_speaks_twice(store):
    lean = RoleLean({"Commerciale": "Per i dati chiedo a @account-crm.", "Account CRM": "Ecco i dati. Avvisa @commerciale e @deployer.", "Deployer": "Non dovrei parlare."})
    say(store, "grupo", "@commerciale prepara la proposta per Officina Rapida", createdAt={"__ts": 1000})
    asyncio.run(chat.run_once(store, lean))
    assert [w for w, *_ in lean.calls] == ["Commerciale", "Account CRM"]                    # 2 passaggi: il Deployer, citato al terzo, non risponde
    who, prompt, hist, _ = lean.calls[1]
    assert prompt == "Commerciale ti scrive: Per i dati chiedo a @account-crm."
    assert hist[0] == ("Lorenzo", "@commerciale prepara la proposta per Officina Rapida")          # chi arriva dopo vede anche la domanda di Lorenzo
    assert [n for n, _ in group_replies(store)] == ["Commerciale", "Account CRM"]


def test_at_most_three_agents_answer_one_message_and_a_self_mention_changes_nothing(store):
    lean = RoleLean({"Commerciale": "Boh @commerciale."})
    say(store, "grupo", "@commerciale @account-crm @deployer @ops-uptime parlate", createdAt={"__ts": 1000})
    asyncio.run(chat.run_once(store, lean))
    assert [w for w, *_ in lean.calls] == ["Commerciale", "Account CRM", "Deployer"]


def test_reserve_agent_says_so_when_asked_directly_but_stays_silent_when_a_colleague_calls_it(store):
    store.set("agents/deployer", {"active": False})
    say(store, "grupo", "@deployer ci sei?", createdAt={"__ts": 1000})
    lean = RoleLean({})
    asyncio.run(chat.run_once(store, lean))
    assert lean.calls == [] and "riserva" in group_replies(store)[0][1]
    lean2 = RoleLean({"Commerciale": "Chiedo a @deployer."})
    say(store, "grupo", "@commerciale hai bisogno di qualcuno?", createdAt={"__ts": 9000})
    asyncio.run(chat.run_once(store, lean2))
    assert [w for w, *_ in lean2.calls] == ["Commerciale"] and len(group_replies(store)) == 2          # il Deployer in riserva non dice niente


# ---- chat ancorata ai dati veri e senza statistiche inventate ----

CRM = {"list_clients": [{"name": "Officina Meccanica Rapida", "contact_name": "Hen Biton", "id": "1"}, {"name": "Studio Nova S.r.l.", "contact_name": "S. P.", "id": "2"}],
       "list_projects": [{"name": "Dispatch Portal", "client": "Officina Rapida", "status": "in_sviluppo", "deadline": "2026-11-15"}],
       "list_deadlines": [{"due": "2026-11-15", "client": "Officina Rapida", "title": "Dispatch Portal", "kind": "progetto"}],
       "list_leads": [{"status": "nuovo"}, {"status": "nuovo"}, {"status": "qualificato"}]}


def lean_with_crm(tmp_path, complete, fail=False):
    from hlab.events import Events
    eng = _lean_mod.Lean(db=None, events=Events(tmp_path / "f.db"), complete=complete)
    eng.calls = []

    async def fake_call(name, **kw):
        eng.calls.append(name)
        if fail:
            raise RuntimeError("CRM non raggiungibile")
        return CRM[name]
    eng._call = fake_call
    eng._facts = lambda: REAL_FACTS(eng)
    return eng


def test_conversation_gets_a_real_crm_summary_without_contacts_and_cached(tmp_path):
    seen = []

    async def fake(prompt, system, max_tokens):
        seen.append(system)
        return "chat" if "classifica" in system.lower() else "Per Officina Rapida la scadenza e' il 15."
    eng = lean_with_crm(tmp_path, fake)
    a = asyncio.run(eng.ask("a che punto siamo con quel fabbro?", persona="Sei COO."))
    system = seen[1]
    assert "DATI REALI" in system and "Clienti (2): Officina Meccanica Rapida, Studio Nova S.r.l." in system
    assert "Dispatch Portal [Officina Rapida] in_sviluppo, scadenza 2026-11-15" in system and "Lead: 2 nuovo, 1 qualificato" in system
    assert "Hen Biton" not in system and "NON hai accesso" not in system                                   # niente contatti, e ora i dati ci sono
    assert "statistiche, percentuali, benchmark" in system
    n = len(eng.calls)
    asyncio.run(eng.ask("e il resto?", persona="Sei COO."))
    assert len(eng.calls) == n                                                                              # cache: il CRM non si rilegge entro 2 minuti
    assert a.intent == "chat"


def test_without_crm_the_chat_says_so_and_still_bans_invented_statistics(tmp_path):
    seen = []

    async def fake(prompt, system, max_tokens):
        seen.append(system)
        return "chat" if "classifica" in system.lower() else "Non ho i dati."
    eng = lean_with_crm(tmp_path, fake, fail=True)
    asyncio.run(eng.ask("dimmi dei clienti", persona="Sei COO."))
    assert "NON hai accesso al CRM" in seen[1] and "non inventare mai" in seen[1] and "statistiche, percentuali, benchmark" in seen[1]
    assert "DATI REALI" not in seen[1]


# ---- classificatore: leggibile anche quando il modello ragiona ad alta voce ----

def test_label_of_takes_the_last_label_and_ignores_noise():
    L = _lean_mod
    assert L.label_of("leads") == "leads" and L.label_of("  Chat.\n") == "chat" and L.label_of("") == "" and L.label_of(None) == ""
    assert L.label_of("Il messaggio parla di clienti, non chiede numeri: quindi chat") == "chat"
    assert L.label_of("Potrebbe essere status, ma chiede i contatti nuovi, quindi leads") == "leads"       # l'ultima vince
    assert L.label_of("boh, non saprei") == ""


def test_classifier_uses_room_to_answer_and_a_reasoning_style_reply_still_routes_to_the_data(tmp_path):
    from hlab.events import Events
    seen = []

    async def fake(prompt, system, max_tokens):
        seen.append((system, max_tokens))
        return "Il messaggio chiede quanti contatti nuovi ci sono: quindi leads" if "classifica" in system.lower() else "x"

    class Eng(_lean_mod.Lean):
        async def _leads(self, text, args, budget):
            return "10 lead"
    a = asyncio.run(Eng(db=None, events=Events(tmp_path / "k.db"), complete=fake).ask("quante persone interessate abbiamo in lista?"))
    assert a.intent == "leads" and a.text == "10 lead" and len(seen) == 1
    assert seen[0][1] == 300 and "Esempi:" in seen[0][0] and "Rispondi con la sola parola" in seen[0][0]
    assert _lean_mod.CAPS["classify"] >= 900 and a.tokens < _lean_mod.CAPS["classify"]
