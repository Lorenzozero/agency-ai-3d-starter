"""Costruisce le opzioni dell'Agent SDK dal registro e lancia una richiesta al COO.

Il COO e' l'agente principale; gli altri sono subagenti nativi (AgentDefinition) con strumenti, MCP e skills propri.
Le regole di sicurezza stanno negli hook (policy.py), il costo e' limitato da max_budget_usd.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from claude_agent_sdk import (AgentDefinition, AssistantMessage, ClaudeAgentOptions, HookMatcher, ResultMessage,
                              TextBlock, query)

from . import config, policy, registry, tools
from .events import Events


def load_env_file(path: Path | None = None) -> None:
    p = path or config.LAB_HOME / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def backend() -> str:
    """router = OpenRouter tramite l'instradatore locale (pool di modelli a rotazione); anthropic = chiave Anthropic diretta."""
    load_env_file()
    forced = os.environ.get("HLAB_BACKEND")
    if forced in ("router", "anthropic"):
        return forced
    return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "router"


def backend_env(kind: str) -> dict:
    """Variabili per la CLI dell'SDK. Con il router gli alias dei modelli puntano ai pool di models.yaml."""
    from .router import load_config
    env = {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": config.MAX_SUBAGENT_DEPTH,
           "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": config.MAX_CONCURRENT_SUBAGENTS}
    if kind == "router":
        port = load_config()["listen"]["port"]
        env.update({"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}", "ANTHROPIC_AUTH_TOKEN": "hlab-local", "ANTHROPIC_API_KEY": "",
                    "ANTHROPIC_DEFAULT_SONNET_MODEL": "hlab-strong", "ANTHROPIC_DEFAULT_OPUS_MODEL": "hlab-strong",
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "hlab-light",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})   # niente richieste di contorno che consumano la quota gratuita
    return env


def require_key(kind: str | None = None) -> str:
    kind = kind or backend()
    if kind == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("manca ANTHROPIC_API_KEY in ~/hermes_lab/.env (chmod 600), oppure usa il router con OPENROUTER_API_KEY")
    if kind == "router":
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise RuntimeError("manca OPENROUTER_API_KEY in ~/hermes_lab/.env (chmod 600), mai nel codice")
        import httpx

        from .router import load_config
        port = load_config()["listen"]["port"]
        try:
            httpx.get(f"http://127.0.0.1:{port}/health", timeout=3).raise_for_status()
        except Exception:
            raise RuntimeError(f"l'instradatore non risponde su 127.0.0.1:{port}: avvialo con `python -m hlab router` (o il servizio hlab-router)")
    return kind


def build_options(specs: dict | None = None, crm_db=None, events: Events | None = None,
                  budget: float | None = None, kind: str = "anthropic") -> ClaudeAgentOptions:
    specs = specs or registry.load_all()
    errs = registry.validate(specs)
    if errs:
        raise ValueError("registro agenti non valido:\n- " + "\n- ".join(errs))
    events = events or Events()
    coo = specs["coo"]
    from .learning import Learning
    lr = Learning(events)                    # regole apprese e approvate da Lorenzo: si aggiungono al prompt di quel ruolo
    agents = {
        n: AgentDefinition(description=s.description, prompt=s.prompt + lr.prompt_block(n), tools=tools.tool_names(s), model=s.model,
                           skills=s.skills or None, mcpServers=tools.server_names(s), maxTurns=s.max_turns, effort=s.effort)
        for n, s in specs.items() if n != "coo"
    }
    return ClaudeAgentOptions(
        system_prompt=coo.prompt + lr.prompt_block("coo"), model=coo.model, effort=coo.effort,
        tools=["Agent", *coo.builtin],                      # il COO delega; niente Bash, Write, Edit
        allowed_tools=["Agent", "Skill", *tools.tool_names(coo)],
        mcp_servers=tools.build_servers(specs, crm_db), agents=agents,
        skills=coo.skills, setting_sources=["project"], cwd=str(config.LAB_HOME),
        max_turns=coo.max_turns,
        max_budget_usd=budget or (config.ROUTER_RUN_BUDGET_USD if kind == "router" else config.RUN_BUDGET_USD),
        env=backend_env(kind),
        hooks={"PreToolUse": [HookMatcher(hooks=[policy.pre_tool_hook(specs, events)])],
               "PostToolUse": [HookMatcher(hooks=[policy.post_tool_hook(events)])]},
    )


async def run(prompt: str, on_text=print, budget: float | None = None) -> dict:
    kind = require_key()
    events = Events()
    run_id = uuid.uuid4().hex[:8]
    opts = build_options(events=events, budget=budget, kind=kind)
    events.log("run_start", "coo", detail=f"[{kind}] {prompt}", run_id=run_id)
    out: dict = {"run_id": run_id}
    async for msg in query(prompt=prompt, options=opts):
        if isinstance(msg, AssistantMessage):
            for b in msg.content:
                if isinstance(b, TextBlock) and b.text.strip():
                    on_text(b.text)
        elif isinstance(msg, ResultMessage):
            usage = msg.usage or {}
            out.update(subtype=msg.subtype, cost_usd=msg.total_cost_usd, turns=msg.num_turns, result=msg.result)
            events.log("run_end", "coo", ok=msg.subtype == "success", detail=msg.subtype, run_id=run_id,
                       cost_usd=msg.total_cost_usd, tokens=(usage.get("input_tokens", 0) + usage.get("output_tokens", 0)))
    return out
