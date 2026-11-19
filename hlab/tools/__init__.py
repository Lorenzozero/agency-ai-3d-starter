"""Catalogo degli strumenti: chi puo' avere cosa, con che rischio. Ogni agente riceve SOLO i suoi."""
from __future__ import annotations

from ..registry import AgentSpec
from . import audit_tools, commercial_tools, crm_tools, graph_tools, learning_tools, memory_tools, ops_tools, powers_tools, proposals_tools, site_tools, system_tools

# gruppo (campo `mcp` dell'agente) -> (server, strumenti). I gruppi condivisi sono senza stato (CRM in lettura, calcoli);
# memoria, proposte e bozze sono per agente: l'autore e' sempre chi le fa.
SHARED_GROUPS = {"crm_read": ("crm", crm_tools.CRM_NAMES), "projects_read": ("projects", crm_tools.PROJECT_NAMES),
                 "pricing": ("pricing", commercial_tools.PRICING_NAMES), "graph_read": ("graph", graph_tools.NAMES),
                 "ops_read": ("ops", ops_tools.NAMES), "web_audit": ("audit", audit_tools.NAMES),
                 "site_design": ("sites", site_tools.DESIGN), "site_build": ("sites", site_tools.BUILD), "site_copy": ("sites", site_tools.COPY), "site_art": ("sites", site_tools.ART),
                 "system_read": ("system", system_tools.READ), "system_improve": ("sysops", system_tools.IMPROVE)}
CRM_RISK = {n: "read" for n in crm_tools.NAMES}
NATIVE_RISK = {"Read": "read", "Grep": "read", "Glob": "read", "WebFetch": "external", "WebSearch": "external"}
ALWAYS_OK = {"Agent", "Task", "Skill", "ToolSearch", "SubagentHandback"}   # SubagentHandback: come il subagente restituisce il risultato (visto alla prova reale)   # delega e caricamento skill: non toccano il mondo


def full(server: str, name: str) -> str:
    return f"mcp__{server}__{name}"


def tool_names(spec: AgentSpec) -> list[str]:
    """Nomi completi di tutti gli strumenti concessi all'agente (nativi + MCP)."""
    names = list(spec.builtin)
    for g in spec.mcp:
        if g in SHARED_GROUPS:
            srv, tl = SHARED_GROUPS[g]
            names += [full(srv, n) for n in tl]
    if "proposals" in spec.mcp:
        names += [full(proposals_tools.server_name(spec), n) for n in proposals_tools.NAMES]
    if "drafts" in spec.mcp:
        names += [full(commercial_tools.draft_server_name(spec), n) for n in commercial_tools.DRAFT_NAMES]
    if "learning" in spec.mcp:
        names += [full(learning_tools.server_name(spec), n) for n in learning_tools.NAMES]
    if "powers" in spec.mcp:
        names += [full(powers_tools.server_name(spec), n) for n in powers_tools.NAMES]
    names += [full(spec.server_name, n) for n in memory_tools.tool_names(spec)]
    return names


def server_names(spec: AgentSpec) -> list[str]:
    s = [SHARED_GROUPS[g][0] for g in spec.mcp if g in SHARED_GROUPS]
    if "proposals" in spec.mcp:
        s.append(proposals_tools.server_name(spec))
    if "drafts" in spec.mcp:
        s.append(commercial_tools.draft_server_name(spec))
    if "learning" in spec.mcp:
        s.append(learning_tools.server_name(spec))
    if "powers" in spec.mcp:
        s.append(powers_tools.server_name(spec))
    if memory_tools.tool_names(spec):
        s.append(spec.server_name)
    return s


def build_servers(specs: dict[str, AgentSpec], crm_db=None) -> dict:
    """Un server MCP per gruppo condiviso e uno di memoria (e di proposte) per agente."""
    servers: dict = {}
    for spec in specs.values():
        for g in spec.mcp:
            if g == "pricing" and "pricing" not in servers:
                servers["pricing"] = commercial_tools.build_pricing()
            elif g == "graph_read" and "graph" not in servers:
                servers["graph"] = graph_tools.build()
            elif g == "ops_read" and "ops" not in servers:
                servers["ops"] = ops_tools.build()
            elif g == "web_audit" and "audit" not in servers:
                servers["audit"] = audit_tools.build()
            elif g in ("site_design", "site_build", "site_copy", "site_art") and "sites" not in servers:
                servers["sites"] = site_tools.build()
            elif g in ("system_read", "system_improve") and SHARED_GROUPS[g][0] not in servers:
                servers[SHARED_GROUPS[g][0]] = system_tools.build(SHARED_GROUPS[g][0])
            elif g in SHARED_GROUPS and SHARED_GROUPS[g][0] not in servers:
                servers[SHARED_GROUPS[g][0]] = crm_tools.build(crm_db, g)
        if "proposals" in spec.mcp:
            servers[proposals_tools.server_name(spec)] = proposals_tools.build(spec)
        if "drafts" in spec.mcp:
            servers[commercial_tools.draft_server_name(spec)] = commercial_tools.build_drafts(spec)
        if "learning" in spec.mcp:
            servers[learning_tools.server_name(spec)] = learning_tools.build(spec)
        if "powers" in spec.mcp:
            servers[powers_tools.server_name(spec)] = powers_tools.build(spec)
        if memory_tools.tool_names(spec):
            servers[spec.server_name] = memory_tools.build(spec)
    return servers


def risk_of(name: str) -> str:
    """read | reversible | external | delegate. Strumento sconosciuto = external (si chiude, non si apre)."""
    if name in ALWAYS_OK:
        return "delegate"
    if name in NATIVE_RISK:
        return NATIVE_RISK[name]
    if name.startswith("mcp__"):
        _, server, tool = name.split("__", 2)
        if server in ("crm", "projects"):
            return CRM_RISK.get(tool, "external")
        if server == "graph":
            return graph_tools.RISK.get(tool, "external")
        if server == "ops":
            return ops_tools.RISK.get(tool, "external")
        if server == "audit":
            return audit_tools.RISK.get(tool, "external")
        if server == "sites":
            return site_tools.RISK.get(tool, "external")
        if server in ("system", "sysops"):
            return system_tools.RISK.get(tool, "external")
        if server.startswith("learn_"):
            return learning_tools.RISK.get(tool, "external")
        if server.startswith("pow_"):
            return powers_tools.RISK.get(tool, "external")
        if server == "pricing" or server.startswith("draft_"):
            return commercial_tools.RISK.get(tool, "external")
        if server.startswith("mem_"):
            return memory_tools.RISK.get(tool, "external")
        if server.startswith("prop_"):
            return proposals_tools.RISK.get(tool, "external")
    return "external"
