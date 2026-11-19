"""Registro degli agenti: un file Markdown per agente in agents/ (frontmatter YAML + prompt).

Il formato ricalca i subagent di Claude Code; i campi in piu' (mcp, memory, autonomy) servono a noi.
Le skills sono cartelle `.claude/skills/<nome>/SKILL.md`, lette in modo nativo dall'SDK.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import config

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
GROUPS = ("crm_read", "projects_read", "proposals", "pricing", "drafts", "learning", "graph_read", "powers", "ops_read", "web_audit", "site_design", "site_build", "site_copy", "site_art", "system_read", "system_improve")   # gruppi di strumenti disponibili (vedi tools/__init__.py)
MODELS = ("fable", "opus", "sonnet", "haiku", "inherit")
BUILTIN_OK = {"Read", "Grep", "Glob", "WebFetch", "WebSearch"}  # strumenti nativi concedibili: niente Bash/Write/Edit
EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass
class AgentSpec:
    name: str
    description: str
    prompt: str
    model: str = "sonnet"
    effort: str = "medium"
    builtin: list[str] = field(default_factory=list)
    mcp: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    memory_private: bool = True
    memory_shared: str = "read"     # none | read | write
    autonomy: int = 1               # 0 osserva, 1 propone, 2 esegue azioni reversibili
    max_turns: int = 12
    path: Path | None = None

    @property
    def server_name(self) -> str:
        return "mem_" + self.name.replace("-", "_")

    def problems(self, skills_dir: Path | None = None) -> list[str]:
        sk = skills_dir or config.SKILLS_DIR
        p = []
        if not NAME_RE.match(self.name):
            p.append(f"nome non valido: {self.name!r}")
        if len(self.description.strip()) < 20:
            p.append("description troppo corta: serve a capire quando delegare a questo agente")
        if len(self.prompt.strip()) < 40:
            p.append("prompt troppo corto")
        if self.model not in MODELS and not self.model.startswith("claude-"):
            p.append(f"modello sconosciuto: {self.model!r}")
        if self.effort not in EFFORTS:
            p.append(f"effort sconosciuto: {self.effort!r}")
        if self.autonomy not in (0, 1, 2):
            p.append("autonomy deve essere 0, 1 o 2")
        if self.memory_shared not in ("none", "read", "write"):
            p.append("memory.shared deve essere none, read o write")
        for b in self.builtin:
            if b not in BUILTIN_OK:
                p.append(f"strumento nativo non concedibile: {b}")
        for g in self.mcp:
            if g not in GROUPS:
                p.append(f"gruppo strumenti sconosciuto: {g}")
        for s in self.skills:
            if not (sk / s / "SKILL.md").exists():
                p.append(f"skill mancante: {s}")
        if not 1 <= self.max_turns <= 60:
            p.append("max_turns fuori range (1-60)")
        return p


def parse(path: Path) -> AgentSpec:
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.S)
    if not m:
        raise ValueError(f"{path.name}: manca il frontmatter YAML")
    meta = yaml.safe_load(m.group(1)) or {}
    mem = meta.get("memory") or {}
    return AgentSpec(
        name=str(meta.get("name", path.stem)), description=str(meta.get("description", "")).strip(), prompt=m.group(2).strip(),
        model=str(meta.get("model", "sonnet")), effort=str(meta.get("effort", "medium")),
        builtin=list(meta.get("tools") or []), mcp=list(meta.get("mcp") or []), skills=list(meta.get("skills") or []),
        memory_private=bool(mem.get("private", True)), memory_shared=str(mem.get("shared", "read")),
        autonomy=int(meta.get("autonomy", 1)), max_turns=int(meta.get("max_turns", 12)), path=path)


def load_all(agents_dir: Path | None = None) -> dict[str, AgentSpec]:
    d = agents_dir or config.AGENTS_DIR
    out: dict[str, AgentSpec] = {}
    for f in sorted(d.glob("*.md")):
        if f.name.startswith("_"):   # _template.md e simili
            continue
        s = parse(f)
        if s.name in out:
            raise ValueError(f"agente duplicato: {s.name}")
        out[s.name] = s
    return out


def validate(specs: dict[str, AgentSpec], skills_dir: Path | None = None) -> list[str]:
    errs = []
    for s in specs.values():
        errs += [f"{s.name}: {p}" for p in s.problems(skills_dir)]
    if "coo" not in specs:
        errs.append("manca l'agente 'coo' (orchestratore)")
    return errs
