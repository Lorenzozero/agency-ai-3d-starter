import asyncio
import json
import re

import pytest

from hlab import config
from hlab.tools import crm_tools

CLIENT = {"id": "11111111-1111-1111-1111-111111111111", "name": "Ristorante Aurora", "vat_number": "IT01234567890",
          "contact_name": "Marco", "contact_email": "marco@aurora.it", "contact_phone": "+39 345 1234567",
          "address": "Arzerello", "opening_hours": None, "old_website": None, "notes": None,
          "created_at": "2026-10-01", "updated_at": "2026-10-19"}


class FakeDb:
    def __init__(self):
        self.calls = []

    def query(self, sql, params=()):
        self.calls.append((sql, params))
        assert re.match(r"^\s*(select|with)\b", sql, re.I), sql
        assert sql.count("%s") == len(params), (sql, params)       # niente parametri persi o in eccesso
        if "from clients where id" in sql or "from clients where name" in sql:
            return [CLIENT]
        if "from projects where client_id" in sql:
            return [{"id": "p1", "name": "Landing 3D", "status": "live", "deadline": None}]
        if "from payments" in sql:
            return [{"status": "pagato", "currency": "EUR", "n": 1, "cents": 205000}]
        if "count(*) as n from tasks" in sql:
            return [{"n": 2}]
        if "where p.id = %s" in sql:
            return [{"id": "p1", "name": "Landing 3D", "client": "Ristorante Aurora", "status": "in_sviluppo", "deadline": "2026-11-20"}]
        if "from tasks where project_id" in sql:
            return [{"title": "Hero", "phase": "realizzazione", "done": True, "due_date": None, "completed_at": "2026-10-20"},
                    {"title": "QA", "phase": "consegna", "done": False, "due_date": "2026-11-18", "completed_at": None}]
        if "from documents where project_id" in sql:
            return [{"type": "contratto", "title": "Contratto B2B", "status": "bozza", "version": 1, "updated_at": "2026-10-19"}]
        return []


def call(db, name, **args):
    t = next(t for t in crm_tools.make_tools(db) if t.name == name)
    r = asyncio.run(t.handler(args))
    return r, (json.loads(r["content"][0]["text"]) if not r.get("is_error") else r["content"][0]["text"])


def test_get_client_masks_contacts_by_default(monkeypatch):
    monkeypatch.setattr(config, "CRM_REDACT", True)
    _, d = call(FakeDb(), "get_client", client="aurora")
    c = d["cliente"]
    assert c["contact_email"] == "m***@aurora.it" and c["contact_phone"].startswith("***") and c["vat_number"] == "***890"
    assert d["incassi"][0]["cents"] == 205000 and d["task_aperti"] == 2


def test_redaction_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(config, "CRM_REDACT", False)
    _, d = call(FakeDb(), "get_client", client="aurora")
    assert d["cliente"]["contact_email"] == "marco@aurora.it"


def test_every_tool_runs_only_selects_with_valid_params():
    db = FakeDb()
    for name, args in [("list_clients", {"q": "tin"}), ("list_projects", {"status": "live", "client": "dog"}),
                       ("list_deadlines", {"days": 14}), ("list_leads", {"min_score": 80}), ("money_summary", {}),
                       ("money_summary", {"project_id": "11111111-1111-1111-1111-111111111111"}),
                       ("project_overview", {"project_id": "22222222-2222-2222-2222-222222222222"}), ("stale_projects", {"days": 21})]:
        r, _ = call(db, name, **args)
        assert not r.get("is_error"), name
    assert len(db.calls) == 6 + 4 + 1             # project_overview fa 4 letture: progetto, task, documenti, incassi


def test_project_overview_summarises_progress():
    _, d = call(FakeDb(), "project_overview", project_id="22222222-2222-2222-2222-222222222222")
    assert d["task"]["totale"] == 2 and d["task"]["fatti"] == 1 and d["documenti"][0]["status"] == "bozza"
    r, msg = call(FakeDb(), "project_overview", project_id="non-un-uuid")
    assert r["is_error"] and "uuid" in msg


def test_groups_become_separate_servers():
    assert crm_tools.build(FakeDb(), "crm_read")["name"] == "crm"
    assert crm_tools.build(FakeDb(), "projects_read")["name"] == "projects"
    assert set(crm_tools.CRM_NAMES) | set(crm_tools.PROJECT_NAMES) == {t.name for t in crm_tools.make_tools(FakeDb())}
    assert not set(crm_tools.CRM_NAMES) & set(crm_tools.PROJECT_NAMES)


def test_input_hardening():
    db = FakeDb()
    r, msg = call(db, "money_summary", project_id="1; drop table clients")
    assert r["is_error"] and "uuid" in msg and db.calls == []
    _, _ = call(db, "list_clients", limit=100000)
    assert db.calls[-1][1][-1] == 50                                  # limite massimo
    _, _ = call(db, "list_deadlines", days="abc")
    assert db.calls[-1][1] == (30, 30)                                # valore non valido -> default
    r, _ = call(db, "get_client", client="")
    assert r["is_error"]


def test_pgdb_refuses_non_select():
    with pytest.raises(PermissionError):
        crm_tools.PgDb().query("update clients set name='x'")
    with pytest.raises(PermissionError):
        crm_tools.PgDb().query("delete from clients")
