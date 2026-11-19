"""Archivio di documenti in SQLite con la semantica di Firestore che serve al frontend (collezioni, sottocollezioni, query, merge, valori speciali).

Serve a far girare sul TUO server il frontend di Agentes IA 3D (MIT, ederland) senza Firebase: i dati restano qui.
Percorsi come in Firestore: `tasks/abc`, `agentChats/x/mensajes/y`. Valori speciali (codificati dal client):
  {"__ts": ms}                      timestamp
  {"__op": "serverTimestamp"}       ora del server
  {"__op": "increment", "n": 5}     somma
  {"__op": "arrayUnion"|"arrayRemove", "v": [...]}
  {"__op": "delete"}                toglie il campo
Ogni scrittura avvisa gli ascoltatori (`subscribe`), che rieseguono la loro query.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from . import config

OPS = {
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
    "<": lambda a, b: a is not None and a < b, "<=": lambda a, b: a is not None and a <= b,
    ">": lambda a, b: a is not None and a > b, ">=": lambda a, b: a is not None and a >= b,
    "in": lambda a, b: a in b, "not-in": lambda a, b: a not in b,
    "array-contains": lambda a, b: isinstance(a, list) and b in a,
}


def _sort_key(v):
    """Ordine stabile fra tipi diversi: None prima, poi numeri/timestamp, poi testo."""
    if v is None:
        return (0, 0, "")
    if isinstance(v, dict) and "__ts" in v:
        return (1, v["__ts"], "")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (1, v, "")
    return (2, 0, str(v))


def _plain(v):
    return v["__ts"] if isinstance(v, dict) and "__ts" in v and len(v) == 1 else v


def _get_path(data: dict, dotted: str):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set_path(data: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    cur = data
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def _del_path(data: dict, dotted: str) -> None:
    parts = dotted.split(".")
    cur = data
    for part in parts[:-1]:
        cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


def _apply(old, value, now_ms: int):
    """Risolve i valori speciali rispetto al valore precedente."""
    if isinstance(value, dict) and "__op" in value:
        op = value["__op"]
        if op == "serverTimestamp":
            return {"__ts": now_ms}
        if op == "increment":
            return (old if isinstance(old, (int, float)) else 0) + value["n"]
        if op == "arrayUnion":
            cur = list(old) if isinstance(old, list) else []
            return cur + [x for x in value["v"] if x not in cur]
        if op == "arrayRemove":
            return [x for x in (old if isinstance(old, list) else []) if x not in value["v"]]
        if op == "delete":
            return None
    if isinstance(value, dict) and "__ts" not in value:
        return {k: _apply((old or {}).get(k) if isinstance(old, dict) else None, v, now_ms) for k, v in value.items()}
    return value


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or config.DATA_DIR / "docs.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._conn()) as c:
            c.executescript("CREATE TABLE IF NOT EXISTS docs(path TEXT PRIMARY KEY, coll TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL);"
                            "CREATE INDEX IF NOT EXISTS docs_coll ON docs(coll);")
        self._subs: list[tuple[str, asyncio.Queue]] = []
        self._loop: asyncio.AbstractEventLoop | None = None   # il ciclo dell'API: le scritture fatte da altri thread avvisano da li'
        self.hooks: list = []                                  # f(path, before, after): reazioni alle scritture (es. decisioni)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=15)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    # ---- lettura -------------------------------------------------------------------------
    def get(self, path: str) -> dict | None:
        with closing(self._conn()) as c:
            r = c.execute("SELECT data FROM docs WHERE path=?", (path,)).fetchone()
        return json.loads(r[0]) if r else None

    def query(self, coll: str, where: list | None = None, order: list | None = None, limit: int | None = None) -> list[dict]:
        with closing(self._conn()) as c:
            rows = [{"id": i, "data": json.loads(d)} for i, d in c.execute("SELECT id, data FROM docs WHERE coll=?", (coll,))]
        for f, op, v in where or []:
            fn = OPS[op]
            rows = [r for r in rows if fn(_plain(_get_path(r["data"], f)), _plain(v))]
        for f, direction in reversed(order or []):
            rows.sort(key=lambda r: _sort_key(_get_path(r["data"], f)), reverse=(direction == "desc"))
        return rows[:limit] if limit else rows

    # ---- scrittura -----------------------------------------------------------------------
    @staticmethod
    def _split(path: str) -> tuple[str, str]:
        parts = [p for p in path.split("/") if p]
        if len(parts) < 2 or len(parts) % 2:
            raise ValueError(f"percorso di documento non valido: {path}")
        return "/".join(parts[:-1]), parts[-1]

    def _write(self, path: str, fn) -> dict:
        coll, doc_id = self._split(path)
        now = int(time.time() * 1000)
        with closing(self._conn()) as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("SELECT data FROM docs WHERE path=?", (path,)).fetchone()
            before = json.loads(r[0]) if r else None
            after = fn(json.loads(json.dumps(before)) if before is not None else None, now)
            c.execute("INSERT INTO docs(path, coll, id, data) VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET data=excluded.data",
                      (path, coll, doc_id, json.dumps(after, ensure_ascii=False)))
            c.commit()
        self._notify(coll)
        for h in self.hooks:
            h(path, before, after)
        return after

    def set(self, path: str, data: dict, merge: bool = False) -> dict:
        def fn(old, now):
            base = old if (merge and old) else {}
            for k, v in data.items():
                if "." in k:
                    cur = _get_path(base, k)
                    _set_path(base, k, _apply(cur, v, now))
                else:
                    base[k] = _apply(base.get(k), v, now)
            return {k: v for k, v in base.items() if v is not None}
        return self._write(path, fn)

    def update(self, path: str, data: dict) -> dict:
        if self.get(path) is None:
            raise KeyError(f"documento inesistente: {path}")

        def fn(old, now):
            for k, v in data.items():
                cur = _get_path(old, k)
                val = _apply(cur if (not isinstance(v, dict) or "__op" in v) else None, v, now)       # updateDoc sostituisce le mappe annidate, non le fonde
                if val is None:
                    _del_path(old, k)
                else:
                    _set_path(old, k, val)
            return old
        return self._write(path, fn)

    def add(self, coll: str, data: dict) -> str:
        doc_id = uuid.uuid4().hex[:20]
        self.set(f"{coll.strip('/')}/{doc_id}", data)
        return doc_id

    def delete(self, path: str) -> None:
        coll, _ = self._split(path)
        with closing(self._conn()) as c:
            c.execute("DELETE FROM docs WHERE path=?", (path,))
            c.commit()
        self._notify(coll)

    def batch(self, ops: list[dict]) -> None:
        for o in ops:
            {"set": lambda: self.set(o["path"], o["data"], o.get("merge", False)), "update": lambda: self.update(o["path"], o["data"]),
             "delete": lambda: self.delete(o["path"])}[o["op"]]()

    # ---- ascolto -------------------------------------------------------------------------
    def subscribe(self, coll: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._subs.append((coll, q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs = [(c, x) for c, x in self._subs if x is not q]

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _notify(self, coll: str) -> None:
        if self._loop is not None and self._loop.is_running():       # le code asyncio non sono thread-safe: si passa sempre dal ciclo
            self._loop.call_soon_threadsafe(self._push, coll)
        else:
            self._push(coll)

    def _push(self, coll: str) -> None:
        for c, q in list(self._subs):
            if c == coll and q.empty():                       # una sola notifica in coda: basta a far rieseguire la query
                try:
                    q.put_nowait(1)
                except asyncio.QueueFull:
                    pass
