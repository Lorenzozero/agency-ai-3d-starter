import asyncio
import json

import pytest

from hlab import registry
from hlab.events import Events
from hlab.tools import proposals_tools

PID = "33333333-3333-3333-3333-333333333333"


def tools_for(agent: str):
    spec = registry.load_all()[agent]
    return {t.name: t for t in proposals_tools.make_tools(spec, Events())}


def run(t, **a):
    r = asyncio.run(t.handler(a))
    return r, r["content"][0]["text"]


def test_valid_proposal_is_recorded_and_crm_untouched():
    t = tools_for("project-manager")
    r, msg = run(t["propose_change"], entity="project", entity_id=PID, field="deadline", new_value="2026-12-01",
                 reason="il cliente ha chiesto lo spostamento")
    assert not r.get("is_error") and "non e' stato modificato" in msg
    p = Events().proposals()
    assert len(p) == 1 and p[0]["agent"] == "project-manager" and p[0]["field"] == "deadline"


def test_duplicates_are_merged():
    t = tools_for("project-manager")
    for _ in range(3):
        run(t["propose_change"], entity="task", entity_id=PID, field="done", new_value="true", reason="documento approvato dal cliente")
    assert len(Events().proposals()) == 1


@pytest.mark.parametrize("kw", [
    dict(entity="client", entity_id=PID, field="name", new_value="x", reason="motivo abbastanza lungo"),      # entita' non ammessa
    dict(entity="project", entity_id=PID, field="currency", new_value="USD", reason="motivo abbastanza lungo"),  # campo non ammesso
    dict(entity="project", entity_id="1; drop table", field="status", new_value="live", reason="motivo abbastanza lungo"),
    dict(entity="project", entity_id=PID, field="status", new_value="live", reason="boh"),                    # motivo troppo corto
])
def test_invalid_proposals_are_rejected(kw):
    r, _ = run(tools_for("project-manager")["propose_change"], **kw)
    assert r["is_error"] and Events().proposals() == []


def test_each_agent_sees_only_its_own_proposals_and_decisions_stick():
    run(tools_for("project-manager")["propose_change"], entity="lead", entity_id=PID, field="status", new_value="contattato",
        reason="il lead ha risposto al messaggio")
    _, mine = run(tools_for("project-manager")["my_proposals"])
    _, other = run(tools_for("account-crm")["my_proposals"]) if "my_proposals" in tools_for("account-crm") else (None, "[]")
    assert len(json.loads(mine)) == 1 and json.loads(other) == []
    ev = Events()
    pid = ev.proposals()[0]["id"]
    assert ev.decide_proposal(pid, True) and not ev.decide_proposal(pid, False)
    assert ev.proposals() == [] and len(ev.proposals("accepted")) == 1
