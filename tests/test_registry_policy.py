import asyncio
from pathlib import Path

import pytest

from hlab import policy, registry, tools
from hlab.events import Events
from hlab.registry import AgentSpec


def specs():
    return registry.load_all()


def test_real_registry_is_valid():
    s = specs()
    assert {"coo", "account-crm", "project-manager", "commerciale", "memory-keeper", "deployer"} <= set(s)
    assert registry.validate(s) == []


def test_template_is_ignored_and_bad_specs_are_reported(tmp_path):
    (tmp_path / "_template.md").write_text("---\nname: x\n---\nignorato")
    (tmp_path / "coo.md").write_text("---\nname: coo\ndescription: orchestratore di prova per i test\nmodel: boh\n"
                                     "tools: [Bash]\nmcp: [nuovo]\nskills: [inesistente]\nautonomy: 5\n---\n" + "prompt " * 10)
    errs = registry.validate(registry.load_all(tmp_path), skills_dir=tmp_path)
    joined = "\n".join(errs)
    for frag in ("modello sconosciuto", "Bash", "gruppo strumenti sconosciuto", "skill mancante", "autonomy"):
        assert frag in joined


def test_missing_frontmatter_and_duplicates(tmp_path):
    (tmp_path / "a.md").write_text("niente frontmatter")
    with pytest.raises(ValueError):
        registry.load_all(tmp_path)


def test_each_agent_gets_only_its_own_tools():
    s = specs()
    crm, coo = tools.tool_names(s["account-crm"]), tools.tool_names(s["coo"])
    assert "mcp__crm__get_client" in crm and "mcp__crm__get_client" not in coo
    assert all("mem_coo" not in t for t in crm)
    assert "mcp__mem_account_crm__memory_write" in crm
    assert "mcp__mem_account_crm__shared_write" not in crm      # condiviso in sola lettura
    servers = tools.build_servers(s, crm_db=object())
    assert set(servers) == {"crm", "projects", "pricing", "graph", "mem_coo", "mem_account_crm", "mem_project_manager", "prop_project_manager",
                            "mem_commerciale", "draft_commerciale", "mem_memory_keeper", "learn_memory_keeper", "mem_deployer", "pow_deployer", "ops", "mem_ops_uptime", "prop_account_crm",
                            "audit", "mem_performance", "mem_seo", "mem_sicurezza", "sites", "mem_direttore_creativo", "mem_costruttore_siti", "mem_copywriter", "mem_art_director", "system", "sysops", "mem_sentinella"}           # + analisi dei siti: un solo server condiviso, una memoria per agente


def test_project_manager_is_separated_from_client_data():
    s = specs()
    pm, crm = tools.tool_names(s["project-manager"]), tools.tool_names(s["account-crm"])
    assert "mcp__projects__list_deadlines" in pm and "mcp__projects__project_overview" in pm
    assert not any(t.startswith("mcp__crm__") for t in pm)                    # niente contatti, lead, incassi
    assert "mcp__prop_project_manager__propose_change" in pm
    assert "mcp__prop_account_crm__propose_change" in crm and not any("prop_project_manager" in t for t in crm)   # ognuno propone solo come se stesso
    assert not any("mem_project_manager" in t for t in crm)                    # memorie separate


def test_risk_classification_fails_closed():
    assert tools.risk_of("mcp__crm__list_clients") == "read"
    assert tools.risk_of("mcp__mem_x__memory_write") == "reversible"
    assert tools.risk_of("mcp__crm__qualcosa_di_nuovo") == "external"
    assert tools.risk_of("Bash") == "external"
    assert tools.risk_of("Agent") == "delegate"


def test_policy_autonomy_levels_and_one_shot_approval():
    s, ev = specs(), Events()
    crm, coo = s["account-crm"], s["coo"]
    assert policy.decide(crm, "mcp__crm__get_client", {"client": "x"}, ev)[0] == "allow"
    assert policy.decide(crm, "mcp__mem_account_crm__memory_write", {"path": "/a.md"}, ev)[0] == "allow"      # autonomia 2
    v, why = policy.decide(coo, "mcp__mem_coo__memory_write", {"path": "/a.md"}, ev)                          # autonomia 1
    assert v == "deny" and "richiesta #" in why
    aid = ev.pending()[0]["id"]
    assert ev.decide(aid, True)
    assert policy.decide(coo, "mcp__mem_coo__memory_write", {"path": "/a.md"}, ev)[0] == "allow"
    assert policy.decide(coo, "mcp__mem_coo__memory_write", {"path": "/a.md"}, ev)[0] == "deny"             # l'ok vale una volta
    assert policy.decide(coo, "mcp__mem_coo__memory_write", {"path": "/b.md"}, ev)[0] == "deny"             # altri argomenti


def test_policy_blocks_foreign_tools_and_deletes_and_level_zero():
    s, ev = specs(), Events()
    assert policy.decide(s["coo"], "mcp__crm__get_client", {}, ev)[0] == "deny"
    pm = s["project-manager"]
    assert policy.decide(pm, "mcp__crm__money_summary", {}, ev)[0] == "deny"
    assert policy.decide(pm, "mcp__projects__list_deadlines", {"days": 7}, ev)[0] == "allow"
    assert policy.decide(pm, "mcp__prop_project_manager__propose_change", {"field": "deadline"}, ev)[0] == "allow"
    assert policy.decide(pm, "mcp__prop_account_crm__propose_change", {}, ev)[0] == "deny"   # non puo' firmare per un altro
    assert policy.decide(s["account-crm"], "Bash", {"command": "ls"}, ev)[0] == "deny"
    assert policy.decide(s["coo"], "Agent", {"subagent_type": "account-crm"}, ev) == ("allow", "")
    assert policy.decide(None, "Agent", {}, ev)[0] == "deny"
    watcher = AgentSpec(name="osservatore", description="x" * 30, prompt="y" * 50, autonomy=0)
    assert policy.decide(watcher, "mcp__mem_osservatore__memory_write", {}, ev)[0] == "deny"


def test_hook_returns_sdk_deny_shape_and_logs():
    s, ev = specs(), Events()
    hook = policy.pre_tool_hook(s, ev)
    out = asyncio.run(hook({"hook_event_name": "PreToolUse", "tool_name": "mcp__crm__get_client", "tool_input": {}}, "id1", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    sub = asyncio.run(hook({"hook_event_name": "PreToolUse", "agent_type": "account-crm",
                            "tool_name": "mcp__crm__get_client", "tool_input": {}}, "id2", None))
    assert sub["hookSpecificOutput"]["permissionDecision"] == "allow" and sub["hookSpecificOutput"]["updatedInput"]["_cap"]   # gettone di capacita'
    assert asyncio.run(hook({"hook_event_name": "PostToolUse"}, None, None)) == {}
    kinds = [e["kind"] for e in ev.recent(5)]
    assert "tool_deny" in kinds and "tool_allow" in kinds


def test_build_options_gives_each_subagent_separate_tools():
    from hlab.runner import build_options
    o = build_options(crm_db=object())
    assert set(o.agents) == {"account-crm", "project-manager", "commerciale", "memory-keeper", "deployer", "ops-uptime", "performance", "seo", "sicurezza", "direttore-creativo", "costruttore-siti", "copywriter", "art-director", "sentinella"}
    for name in ("performance", "seo", "sicurezza"):                          # i tre analisti dei siti: solo analisi + progetti in lettura + la propria memoria, niente CRM con dati personali
        assert o.agents[name].mcpServers == ["audit", "projects", f"mem_{name}"] and not any(t.startswith("mcp__crm__") for t in o.agents[name].tools)
    assert o.agents["direttore-creativo"].mcpServers == ["crm", "projects", "audit", "sites", "mem_direttore_creativo"]        # la fabbrica di siti: chi progetta e chi costruisce hanno strumenti diversi
    assert o.agents["costruttore-siti"].mcpServers == ["sites", "mem_costruttore_siti"] and "mcp__sites__site_build" not in o.agents["direttore-creativo"].tools
    ag = o.agents["account-crm"]
    assert "mcp__crm__get_client" in ag.tools and ag.skills == ["scheda-cliente", "memoria-agente"]
    assert ag.mcpServers == ["crm", "projects", "graph", "prop_account_crm", "mem_account_crm"]
    pm = o.agents["project-manager"]
    assert pm.mcpServers == ["projects", "prop_project_manager", "mem_project_manager"]
    assert pm.skills == ["controllo-scadenze", "memoria-agente"] and not set(pm.tools) & set(ag.tools[:4])
    assert o.max_budget_usd > 0 and o.setting_sources == ["project"]
    assert o.env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] == "1"
    assert "PreToolUse" in o.hooks
