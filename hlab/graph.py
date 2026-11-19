"""Memoria a grafo leggera (SQLite): collega clienti, progetti, regole e servizi senza leggere interi file.

E' un indice DERIVATO dalle memorie (si ricostruisce con `python -m hlab graph build`): nodi per voce di memoria e per entita' note
(servizi, paesi, valute, clienti), archi `mentions` (la voce cita l'entita') e `similar` (due voci parlano della stessa cosa, per
somiglianza di embedding). `related()` percorre il grafo e restituisce i collegamenti con la prova: es. dalla scheda di un cliente USA
si arriva alle regole fiscali USA passando dal nodo `paese:usa`.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager

import numpy as np

from . import config
from .embed import get_embedder

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, norm TEXT NOT NULL, props TEXT NOT NULL DEFAULT '{}',
  UNIQUE(kind, norm));
CREATE TABLE IF NOT EXISTS edges(src INTEGER NOT NULL, rel TEXT NOT NULL, dst INTEGER NOT NULL, weight REAL NOT NULL DEFAULT 1, evidence TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(src, rel, dst));
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst);
"""
ENTITIES = {   # entita' note: tipo, nome canonico, parole che la citano
    ("servizio", "landing"): r"landing|sito vetrina|sito web", ("servizio", "webapp"): r"web ?app|area riservata|gestionale",
    ("servizio", "mobile"): r"app mobile|app ios|flutter", ("servizio", "manutenzione"): r"manutenzione|canone mensile",
    ("paese", "usa"): r"\busa\b|stati uniti|americano|americani|\bus\b|nevada|new york|queens|las vegas", ("paese", "italia"): r"italia|italian[oi]|padova|\bpd\b|piove di sacco",
    ("valuta", "usd"): r"\busd\b|dollar[oi]|\$", ("valuta", "eur"): r"\beur\b|euro|€",
    ("tema", "acconto"): r"acconto|anticipo|caparra", ("tema", "fiscale"): r"\biva\b|bollo|forfettario|fiscal[ei]|w-?8ben|7-ter",
}
SIMILAR_MIN = 0.86


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


class Graph:
    def __init__(self, path=None):
        self.path = path or (config.DATA_DIR / "graph.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path)
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

    def upsert_node(self, kind: str, name: str, props: dict | None = None) -> int:
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO nodes(kind,name,norm,props) VALUES(?,?,?,?)", (kind, name, norm(name), json.dumps(props or {})))
            return c.execute("SELECT id FROM nodes WHERE kind=? AND norm=?", (kind, norm(name))).fetchone()["id"]

    def link(self, src: int, rel: str, dst: int, weight: float = 1.0, evidence: str = "") -> None:
        if src == dst:
            return
        with self._conn() as c:
            c.execute("INSERT INTO edges(src,rel,dst,weight,evidence) VALUES(?,?,?,?,?) ON CONFLICT(src,rel,dst) DO UPDATE SET "
                      "weight=max(weight, excluded.weight)", (src, rel, dst, weight, evidence[:200]))

    def find(self, text: str, limit: int = 8) -> list[dict]:
        n = norm(text)
        with self._conn() as c:
            rows = c.execute("SELECT * FROM nodes WHERE norm=? OR norm LIKE ? ORDER BY (norm=?) DESC, length(norm) LIMIT ?",
                             (n, f"%{n}%", n, limit)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._conn() as c:
            return {"nodi": c.execute("SELECT count(*) FROM nodes").fetchone()[0], "archi": c.execute("SELECT count(*) FROM edges").fetchone()[0]}

    def related(self, entity: str, depth: int = 2, limit: int = 25) -> list[dict]:
        """Ricerca in ampiezza (archi non orientati). Ogni risultato porta il percorso che lo collega e la prova."""
        start = self.find(entity, 1)
        if not start:
            return []
        depth = max(1, min(int(depth), 3))
        seen = {start[0]["id"]: ""}
        frontier, out = [(start[0]["id"], [start[0]["name"]])], []
        with self._conn() as c:
            for _ in range(depth):
                nxt = []
                for nid, path in frontier:
                    rows = c.execute("SELECT dst AS o, rel, weight, evidence, 1 AS fw FROM edges WHERE src=? UNION ALL "
                                     "SELECT src AS o, rel, weight, evidence, 0 FROM edges WHERE dst=?", (nid, nid)).fetchall()
                    for r in sorted(rows, key=lambda r: -r["weight"]):
                        if r["o"] in seen:
                            continue
                        node = c.execute("SELECT kind,name FROM nodes WHERE id=?", (r["o"],)).fetchone()
                        step = f" -{r['rel']}-> " if r["fw"] else f" <-{r['rel']}- "
                        p = path + [step.strip() + " " + f"{node['kind']}:{node['name']}"]
                        seen[r["o"]] = "x"
                        out.append({"kind": node["kind"], "name": node["name"], "percorso": " ".join(p), "prova": r["evidence"], "peso": round(r["weight"], 2)})
                        nxt.append((r["o"], p))
                frontier = nxt
        out = [o for o in out if o["kind"] != "paese" or True]
        return sorted(out, key=lambda o: (len(o["percorso"].split("->")) + len(o["percorso"].split("<-")), -o["peso"]))[:limit]

    # ---- costruzione dalle memorie --------------------------------------------------------
    def rebuild(self, stores: list, embedder=None) -> dict:
        """Ricostruisce il grafo da zero a partire dalle voci di memoria (dato derivato: nessuna perdita)."""
        with self._conn() as c:
            c.executescript("DELETE FROM edges; DELETE FROM nodes;")
        entities = {k: self.upsert_node(k[0], k[1]) for k in ENTITIES}
        entries: list[tuple[int, str, str]] = []
        for s in stores:
            for row in s.list("/", limit=2000):
                e = s.get(row["path"])
                kind = {"clienti": "cliente", "progetti": "progetto", "regole": "regola", "lezioni": "lezione"}.get(e["path"].split("/")[1], "nota")
                label = e["path"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
                nid = self.upsert_node(kind, label, {"path": e["path"], "store": s.name})
                entries.append((nid, e["path"], e["text"]))
                if kind == "cliente":
                    entities[("cliente", norm(label))] = nid
        client_pat = {k: re.compile(r"\b" + re.escape(k[1].replace("-", " ")) + r"\b", re.I) for k in entities if k[0] == "cliente"}
        for nid, path, text in entries:
            low = text.lower()
            for key, pat in ENTITIES.items():
                if re.search(pat, low):
                    self.link(nid, "mentions", entities[key], 1.0, path)
            for key, pat in client_pat.items():
                if entities[key] != nid and pat.search(text):
                    self.link(nid, "mentions", entities[key], 1.0, path)
        n_sim = 0
        if len(entries) > 1:
            emb = embedder or get_embedder()
            vecs = emb.embed_docs([f"{p}. {t}" for _, p, t in entries])
            sims = vecs @ vecs.T
            for i in range(len(entries)):
                for j in range(i + 1, len(entries)):
                    if sims[i, j] >= SIMILAR_MIN:
                        self.link(entries[i][0], "similar", entries[j][0], float(sims[i, j]), f"{entries[i][1]} ~ {entries[j][1]}")
                        n_sim += 1
        return {**self.stats(), "archi_similar": n_sim}
