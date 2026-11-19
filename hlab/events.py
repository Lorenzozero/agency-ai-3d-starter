"""Registro eventi e coda di approvazioni (SQLite). Ogni azione degli agenti lascia una riga."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, run_id TEXT, agent TEXT, kind TEXT NOT NULL,
  tool TEXT, ok INTEGER, detail TEXT, cost_usd REAL, tokens INTEGER);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, agent TEXT NOT NULL, tool TEXT NOT NULL, args TEXT NOT NULL,
  args_hash TEXT NOT NULL, risk TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  decided_by TEXT, decided_ts REAL);
CREATE INDEX IF NOT EXISTS approvals_hash ON approvals(agent, tool, args_hash, status);
CREATE TABLE IF NOT EXISTS proposals(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, agent TEXT NOT NULL, entity TEXT NOT NULL, entity_id TEXT NOT NULL,
  field TEXT NOT NULL, new_value TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  decided_ts REAL);
CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, agent TEXT NOT NULL, source TEXT NOT NULL, source_id TEXT,
  verdict TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '', processed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS learned_rules(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, agent TEXT NOT NULL, rule TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'proposed', decided_ts REAL);
CREATE TABLE IF NOT EXISTS drafts(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, agent TEXT NOT NULL, kind TEXT NOT NULL, client TEXT NOT NULL,
  project_id TEXT, currency TEXT NOT NULL, payload TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft', decided_ts REAL);
"""


def args_hash(args) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


class Events:
    def __init__(self, path=None):
        self.path = path or config.EVENTS_DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path)
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        c.close()

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def log(self, kind: str, agent: str = "", tool: str = "", ok: bool | None = None, detail: str = "",
            run_id: str = "", cost_usd: float | None = None, tokens: int | None = None) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO events(ts,run_id,agent,kind,tool,ok,detail,cost_usd,tokens) VALUES(?,?,?,?,?,?,?,?,?)",
                      (time.time(), run_id, agent, kind, tool, None if ok is None else int(ok), detail[:500], cost_usd, tokens))

    # ---- approvazioni ---------------------------------------------------------------
    def request_approval(self, agent: str, tool: str, args, risk: str) -> int:
        h = args_hash(args)
        with self._conn() as c:
            r = c.execute("SELECT id FROM approvals WHERE agent=? AND tool=? AND args_hash=? AND status='pending'",
                          (agent, tool, h)).fetchone()
            if r:
                return r["id"]
            return c.execute("INSERT INTO approvals(ts,agent,tool,args,args_hash,risk) VALUES(?,?,?,?,?,?)",
                             (time.time(), agent, tool, json.dumps(args, ensure_ascii=False, default=str)[:2000], h, risk)).lastrowid

    def consume_approval(self, agent: str, tool: str, args) -> bool:
        """Un'approvazione vale per UNA sola esecuzione identica."""
        with self._conn() as c:
            r = c.execute("SELECT id FROM approvals WHERE agent=? AND tool=? AND args_hash=? AND status='approved' ORDER BY id LIMIT 1",
                          (agent, tool, args_hash(args))).fetchone()
            if not r:
                return False
            c.execute("UPDATE approvals SET status='used' WHERE id=?", (r["id"],))
            return True

    def decide(self, approval_id: int, approve: bool, by: str = "lorenzo") -> bool:
        with self._conn() as c:
            cur = c.execute("UPDATE approvals SET status=?, decided_by=?, decided_ts=? WHERE id=? AND status='pending'",
                            ("approved" if approve else "denied", by, time.time(), approval_id))
            return cur.rowcount == 1

    # ---- proposte di modifica al CRM (l'agente propone; applicarle resta una decisione umana) ----
    def add_proposal(self, agent: str, entity: str, entity_id: str, field: str, new_value: str, reason: str) -> int:
        with self._conn() as c:
            dup = c.execute("SELECT id FROM proposals WHERE agent=? AND entity=? AND entity_id=? AND field=? AND new_value=? "
                            "AND status='pending'", (agent, entity, entity_id, field, new_value)).fetchone()
            if dup:
                return dup["id"]
            return c.execute("INSERT INTO proposals(ts,agent,entity,entity_id,field,new_value,reason) VALUES(?,?,?,?,?,?,?)",
                             (time.time(), agent, entity, entity_id, field, new_value[:500], reason[:500])).lastrowid

    def proposals(self, status: str = "pending") -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM proposals WHERE status=? ORDER BY id", (status,))]

    def decide_proposal(self, pid: int, accept: bool) -> bool:
        with self._conn() as c:
            cur = c.execute("UPDATE proposals SET status=?, decided_ts=? WHERE id=? AND status='pending'",
                            ("accepted" if accept else "rejected", time.time(), pid))
            return cur.rowcount == 1

    def mark_proposal(self, pid: int, status: str) -> bool:
        """accepted -> applied: la proposta e' stata davvero eseguita sul CRM."""
        with self._conn() as c:
            return c.execute("UPDATE proposals SET status=? WHERE id=? AND status='accepted'", (status, pid)).rowcount == 1

    # ---- feedback e regole apprese (vedi learning.py) ----
    def add_feedback(self, agent: str, source: str, source_id, verdict: str, comment: str = "") -> int:
        with self._conn() as c:
            return c.execute("INSERT INTO feedback(ts,agent,source,source_id,verdict,comment) VALUES(?,?,?,?,?,?)",
                             (time.time(), agent, source, None if source_id is None else str(source_id), verdict, comment[:1000])).lastrowid

    def feedback_rows(self, agent: str | None = None, processed: bool = False, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            q, p = "SELECT * FROM feedback WHERE processed=?", [int(processed)]
            if agent:
                q, p = q + " AND agent=?", p + [agent]
            return [dict(r) for r in c.execute(q + " ORDER BY id LIMIT ?", p + [limit])]

    def mark_feedback(self, ids: list[int]) -> int:
        with self._conn() as c:
            return sum(c.execute("UPDATE feedback SET processed=1 WHERE id=?", (int(i),)).rowcount for i in ids)

    def add_rule(self, agent: str, rule: str, evidence: list[int]) -> int:
        with self._conn() as c:
            return c.execute("INSERT INTO learned_rules(ts,agent,rule,evidence) VALUES(?,?,?,?)",
                             (time.time(), agent, rule, json.dumps(evidence))).lastrowid

    def rule_rows(self, agent: str | None = None, status: str | None = None) -> list[dict]:
        with self._conn() as c:
            q, p, w = "SELECT * FROM learned_rules", [], []
            if agent:
                w.append("agent=?")
                p.append(agent)
            if status:
                w.append("status=?")
                p.append(status)
            rows = [dict(r) for r in c.execute(q + (" WHERE " + " AND ".join(w) if w else "") + " ORDER BY id", p)]
        for r in rows:
            r["evidence"] = json.loads(r["evidence"])
        return rows

    def set_rule_status(self, rid: int, status: str) -> bool:
        with self._conn() as c:
            return c.execute("UPDATE learned_rules SET status=?, decided_ts=? WHERE id=?", (status, time.time(), rid)).rowcount == 1

    def get_proposal(self, pid: int) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None

    # ---- bozze (preventivi): restano locali, non partono verso nessuno finche' Lorenzo non le approva ----
    def add_draft(self, agent: str, kind: str, client: str, project_id: str | None, currency: str, payload: dict, notes: str = "") -> int:
        with self._conn() as c:
            return c.execute("INSERT INTO drafts(ts,agent,kind,client,project_id,currency,payload,notes) VALUES(?,?,?,?,?,?,?,?)",
                             (time.time(), agent, kind, client[:200], project_id, currency,
                              json.dumps(payload, ensure_ascii=False, default=str), notes[:1000])).lastrowid

    def drafts(self, status: str = "draft", agent: str | None = None) -> list[dict]:
        with self._conn() as c:
            q, p = "SELECT * FROM drafts WHERE status=?", [status]
            if agent:
                q, p = q + " AND agent=?", p + [agent]
            rows = [dict(r) for r in c.execute(q + " ORDER BY id", p)]
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return rows

    def get_draft(self, did: int) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        return d

    def decide_draft(self, did: int, approve: bool) -> bool:
        with self._conn() as c:
            cur = c.execute("UPDATE drafts SET status=?, decided_ts=? WHERE id=? AND status='draft'",
                            ("approved" if approve else "rejected", time.time(), did))
            return cur.rowcount == 1

    def pending(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT id, agent, tool, args, risk FROM approvals WHERE status='pending' ORDER BY id")]

    def tool_counts(self) -> list[dict]:
        """Quante volte ogni agente ha usato ogni strumento: serve a sapere qual e' quello che usa di piu'."""
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT agent, tool, COUNT(*) AS n FROM events WHERE kind='tool_done' AND tool!='' AND agent!='' GROUP BY agent, tool")]

    def active_since(self, window_s: float, now: float | None = None) -> dict[str, dict]:
        """Agenti che hanno fatto qualcosa negli ultimi `window_s` secondi -> {agente: ultimo evento}. E' cio' che l'ufficio mostra come "al lavoro"."""
        t = (now if now is not None else time.time()) - window_s
        with self._conn() as c:
            rows = c.execute("SELECT agent, tool, kind, ts FROM events WHERE ts>=? AND agent!='' AND agent!='lean' "
                             "AND kind IN ('tool_allow','tool_done','run_start','chat') ORDER BY id", (t,))
            return {r["agent"]: dict(r) for r in rows}

    def recent(self, n: int = 20) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (n,))]

    def agent_stats(self) -> dict[str, dict]:
        """Calcola dai dati reali di events.db i token consumati, il numero di azioni e l'ultima attività per ogni agente.
        I token di `run_end` sono ripartiti proporzionalmente tra gli agenti attivi nella finestra (`run_start`..`run_end`)."""
        with self._conn() as c:
            rows = list(c.execute("SELECT ts, agent, kind, tokens, cost_usd FROM events ORDER BY id"))
        tokens: dict[str, int] = {}
        actions: dict[str, int] = {}
        costs: dict[str, float] = {}
        last_ts: dict[str, float] = {}
        last_start = 0
        for i, r in enumerate(rows):
            ag = (r["agent"] or "").strip()
            if ag:
                actions[ag] = actions.get(ag, 0) + 1
                last_ts[ag] = max(last_ts.get(ag, 0.0), float(r["ts"] or 0.0))
            if r["kind"] == "run_start":
                last_start = i
            elif r["kind"] == "run_end" and (r["tokens"] or 0) > 0:
                tok = int(r["tokens"])
                usd = float(r["cost_usd"] or 0.0)
                win = rows[last_start:i + 1]
                counts: dict[str, int] = {}
                for w in win:
                    wag = (w["agent"] or "").strip()
                    if wag and wag != "lean":
                        counts[wag] = counts.get(wag, 0) + 1
                tot = sum(counts.values()) or 1
                for wag, cnt in counts.items():
                    share = cnt / tot
                    tokens[wag] = tokens.get(wag, 0) + round(tok * share)
                    costs[wag] = round(costs.get(wag, 0.0) + usd * share, 4)
                last_start = i + 1
            elif (r["tokens"] or 0) > 0 and ag:
                tokens[ag] = tokens.get(ag, 0) + int(r["tokens"])
                costs[ag] = round(costs.get(ag, 0.0) + float(r["cost_usd"] or 0.0), 4)
        all_agents = set(tokens) | set(actions)
        return {
            a: {
                "tokensUsed": tokens.get(a, 0),
                "actionsCount": actions.get(a, 0),
                "costUsd": costs.get(a, 0.0),
                "lastActiveTs": int(last_ts.get(a, 0)),
            }
            for a in all_agents
        }
