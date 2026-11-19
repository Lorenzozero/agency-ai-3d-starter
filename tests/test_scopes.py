import asyncio

import pytest

from hlab import config, policy, registry, scopes
from hlab.events import Events
from hlab.registry import AgentSpec
from hlab.tools import crm_tools
from test_crm_tools import FakeDb


def specs():
    return registry.load_all()


def hardened():
    return {t.name: t for t in scopes.harden("crm", crm_tools.make_tools(FakeDb()))}


def call(tool, **args):
    r = asyncio.run(tool.handler(args))
    return r, r["content"][0]["text"]


def test_scope_mapping():
    assert scopes.scope_of("mcp__crm__get_client") == "crm"
    assert scopes.scope_of("mcp__crm__money_summary") == "finance"
    assert scopes.scope_of("mcp__projects__list_deadlines") == "projects"
    assert scopes.scope_of("mcp__mem_account_crm__memory_write") == "memory:account_crm"
    assert scopes.scope_of("mcp__mem_x__shared_search") == "shared:read" and scopes.scope_of("mcp__mem_x__shared_write") == "shared:write"
    assert scopes.scope_of("mcp__draft_commerciale__save_quote_draft") == "draft:commerciale"
    assert scopes.scope_of("Bash") is None


def test_each_agent_owns_only_its_scopes():
    s = specs()
    acc, pm, com, keeper = (scopes.agent_scopes(s[n]) for n in ("account-crm", "project-manager", "commerciale", "memory-keeper"))
    assert {"crm", "finance", "projects", "graph", "memory:account_crm", "shared:read"} <= acc
    assert not ({"crm", "finance", "pricing"} & pm) and "prop:project_manager" in pm        # il PM non vede clienti ne' soldi
    assert "pricing" in com and "draft:commerciale" in com and "learn:commerciale" not in com
    assert "shared:write" in keeper and "shared:write" not in acc


def test_call_without_token_is_refused_and_with_valid_token_works():
    t = hardened()["get_client"]
    r, msg = call(t, client="aurora")
    assert r["is_error"] and "gettone" in msg
    tok = scopes.mint("account-crm", "mcp__crm__get_client", {"client": "aurora"})
    r, msg = call(t, client="aurora", _cap=tok)
    assert not r.get("is_error") and "Ristorante Aurora" in msg


def test_token_is_bound_to_agent_tool_and_exact_arguments():
    h = hardened()
    t = h["get_client"]
    # emesso per un agente senza la capacita' 'crm' (il Project Manager non vede i clienti)
    r, msg = call(t, client="x", _cap=scopes.mint("project-manager", "mcp__crm__get_client", {"client": "x"}))
    assert r["is_error"] and "capacita' 'crm'" in msg
    # argomenti diversi da quelli per cui e' stato emesso
    r, msg = call(t, client="altro", _cap=scopes.mint("account-crm", "mcp__crm__get_client", {"client": "x"}))
    assert r["is_error"] and "argomenti" in msg
    # gettone di un altro strumento riusato
    r, msg = call(t, client="x", _cap=scopes.mint("account-crm", "mcp__crm__list_clients", {"client": "x"}))
    assert r["is_error"] and "altro strumento" in msg
    # scaduto, manomesso, di un agente che non esiste
    assert call(t, client="x", _cap=scopes.mint("account-crm", "mcp__crm__get_client", {"client": "x"}, ttl=-1))[0]["is_error"]
    good = scopes.mint("account-crm", "mcp__crm__get_client", {"client": "x"})
    assert call(t, client="x", _cap=good[:-3] + "abc")[0]["is_error"]
    assert "capacita'" in call(t, client="x", _cap=scopes.mint("fantasma", "mcp__crm__get_client", {"client": "x"}))[1]


def test_a_lead_hunter_or_factory_style_agent_can_never_reach_finance():
    """Agenti futuri (Lead Hunter, Fabbrica): senza il gruppo crm_read non hanno ne' 'crm' ne' 'finance', anche con un gettone emesso a mano."""
    hunter = AgentSpec(name="lead-hunter", description="cerca lead " * 3, prompt="x" * 60, mcp=["pricing"])
    factory = AgentSpec(name="fabbrica", description="costruisce siti " * 3, prompt="x" * 60, mcp=[])
    t = hardened()["money_summary"]
    for s in (hunter, factory):
        tok = scopes.mint(s.name, "mcp__crm__money_summary", {})
        r, msg = call(t, _cap=tok)
        assert r["is_error"]
    custom = {"lead-hunter": hunter, "fabbrica": factory}
    ok, why = scopes.verify(scopes.mint("lead-hunter", "mcp__crm__money_summary", {}), "mcp__crm__money_summary", {}, specs=custom)
    assert not ok and "finance" in why


def test_cap_is_stripped_before_the_real_handler_and_hidden_from_the_model():
    t = hardened()["list_clients"]
    assert "_cap" not in t.input_schema["properties"]            # il modello non lo vede e non puo' provare a compilarlo
    tok = scopes.mint("account-crm", "mcp__crm__list_clients", {"q": "tin"})
    r, _ = call(t, q="tin", _cap=tok)
    assert not r.get("is_error")


def test_off_and_warn_modes_execute_but_only_warn_leaves_a_trace(monkeypatch):
    monkeypatch.setattr(config, "CAPS_MODE", "off")
    assert not call(hardened()["get_client"], client="aurora")[0].get("is_error")
    assert Events().recent(5) == []
    monkeypatch.setattr(config, "CAPS_MODE", "warn")
    assert not call(hardened()["get_client"], client="aurora")[0].get("is_error")       # senza gettone: esegue...
    ev = Events().recent(5)
    assert ev[0]["kind"] == "cap_missing" and "gettone" in ev[0]["detail"]               # ...ma lo segnala
    assert call(hardened()["get_client"], client="aurora", _cap=scopes.mint("account-crm", "mcp__crm__get_client", {"client": "aurora"}))[0].get("is_error") is None
    assert len(Events().recent(5)) == 1                                                  # con gettone valido nessun avviso nuovo
    monkeypatch.setattr(config, "CAPS_MODE", "enforce")
    assert call(hardened()["get_client"], client="aurora")[0]["is_error"]


def test_policy_hook_mints_a_token_the_handler_accepts_end_to_end():
    ev = Events()
    hook = policy.pre_tool_hook(specs(), ev)
    args = {"client": "aurora"}
    out = asyncio.run(hook({"hook_event_name": "PreToolUse", "agent_type": "account-crm", "tool_name": "mcp__crm__get_client", "tool_input": args}, "i", None))
    new_args = out["hookSpecificOutput"]["updatedInput"]
    r, msg = call(hardened()["get_client"], **new_args)
    assert not r.get("is_error") and "Ristorante Aurora" in msg
    # lo stesso gettone non vale per un altro agente che prova a riusarlo con gli stessi argomenti
    pm_out = asyncio.run(hook({"hook_event_name": "PreToolUse", "agent_type": "project-manager", "tool_name": "mcp__crm__get_client", "tool_input": args}, "i", None))
    assert pm_out["hookSpecificOutput"]["permissionDecision"] == "deny"            # il hook lo nega prima ancora di emettere un gettone
