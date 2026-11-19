"""Memoria per agente: SQLite con ricerca ibrida (FTS5 BM25 + sqlite-vec) fusa con RRF.

Un file .db per agente (memoria privata) piu' uno store condiviso `_shared`. Ogni modifica crea una
versione (audit e recupero), le cancellazioni sono logiche. Pensata per il VPS: nessun server, nessuna GPU.
"""
from __future__ import annotations

import math
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import sqlite_vec

from . import config
from .embed import get_embedder

PATH_RE = re.compile(r"^/[a-z0-9][a-z0-9_\-./]{0,120}$")
EMBED_FMT = "2"   # 2: il titolo ricavato dal percorso entra nel testo da cui si calcola l'embedding


def embed_text(path: str, text: str) -> str:
    """Il nome del file e' un'etichetta scritta da chi l'ha creato: aiuta il modello a capire di cosa parla la voce."""
    title = re.sub(r"[-_./]+", " ", path.rsplit(".", 1)[0]).strip()
    return f"{title}. {text}"
TRUST = ("trusted", "untrusted")  # untrusted = derivato da web, email, messaggi di terzi
_TOK = re.compile(r"\w{2,}", re.UNICODE)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS mem(
  id INTEGER PRIMARY KEY, path TEXT NOT NULL, text TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '',
  trust TEXT NOT NULL DEFAULT 'trusted', importance REAL NOT NULL DEFAULT 0.5, author TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, updated REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS mem_path_live ON mem(path) WHERE deleted=0;
CREATE TABLE IF NOT EXISTS mem_versions(
  id INTEGER PRIMARY KEY, mem_id INTEGER NOT NULL, version INTEGER NOT NULL, op TEXT NOT NULL,
  text TEXT, author TEXT, ts REAL NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(text, tags, path, tokenize='unicode61 remove_diacritics 2');
"""


def store_path(name: str) -> Path:
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise ValueError(f"nome store non valido: {name!r}")
    return config.MEMORY_DIR / f"{name}.db"


class MemoryStore:
    def __init__(self, name: str, readonly: bool = False, embedder=None):
        self.name, self.readonly = name, readonly
        self.path = store_path(name)
        self.emb = embedder or get_embedder()
        self.dim = self.emb.dim
        self._init()

    # ---- connessione e schema -------------------------------------------------
    @contextmanager
    def _conn_rw(self):
        """Connessione di manutenzione: ignora la sola lettura logica (serve solo a migrare gli indici)."""
        with self._conn(force_rw=True) as c:
            yield c

    @contextmanager
    def _conn(self, force_rw: bool = False):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        c.enable_load_extension(False)
        if self.readonly and not force_rw:
            c.execute("PRAGMA query_only=ON")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def _init(self):
        config.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        fresh = not self.path.exists()
        c = sqlite3.connect(self.path, timeout=15)
        try:
            c.enable_load_extension(True)
            sqlite_vec.load(c)
            c.enable_load_extension(False)
            if fresh:
                c.execute("PRAGMA journal_mode=WAL")
                c.executescript(SCHEMA)
                c.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS mem_vec USING vec0(embedding float[{self.dim}] distance_metric=cosine)")
                c.executemany("INSERT OR REPLACE INTO meta VALUES(?,?)",
                              [("embed_model", self.emb.name), ("dim", str(self.dim)), ("embed_fmt", EMBED_FMT)])
                c.commit()
            else:
                row = c.execute("SELECT v FROM meta WHERE k='dim'").fetchone()
                model = c.execute("SELECT v FROM meta WHERE k='embed_model'").fetchone()
                fmt = c.execute("SELECT v FROM meta WHERE k='embed_fmt'").fetchone()
                stale = (not fmt or fmt[0] != EMBED_FMT) or (row and int(row[0]) != self.dim) or (model and model[0] != self.emb.name)
                resize = bool(row and int(row[0]) != self.dim)
        finally:
            c.close()
        if not fresh and stale:
            self._reindex(resize)   # modello o formato cambiati: manutenzione interna (il contenuto non cambia), anche per gli store in sola lettura

    def reindex(self) -> int:
        self._need_write()
        return self._reindex(False)

    def _reindex(self, resize: bool) -> int:
        """Ricalcola gli embedding delle voci vive col modello e il formato correnti. Testo e versioni restano invariati."""
        with self._conn_rw() as c:
            if resize:
                c.execute("DROP TABLE IF EXISTS mem_vec")
                c.execute(f"CREATE VIRTUAL TABLE mem_vec USING vec0(embedding float[{self.dim}] distance_metric=cosine)")
            rows = c.execute("SELECT id, path, text FROM mem WHERE deleted=0 ORDER BY id").fetchall()
            c.execute("INSERT OR REPLACE INTO meta VALUES('dim',?)", (str(self.dim),))
            c.execute("INSERT OR REPLACE INTO meta VALUES('embed_model',?)", (self.emb.name,))
            if rows:
                vecs = self.emb.embed_docs([embed_text(r["path"], r["text"]) for r in rows])
                for r, v in zip(rows, vecs):
                    c.execute("DELETE FROM mem_vec WHERE rowid=?", (r["id"],))
                    c.execute("INSERT INTO mem_vec(rowid,embedding) VALUES(?,?)", (r["id"], sqlite_vec.serialize_float32(v.tolist())))
            c.execute("INSERT OR REPLACE INTO meta VALUES('embed_fmt',?)", (EMBED_FMT,))
        return len(rows)

    def _need_write(self):
        if self.readonly:
            raise PermissionError(f"lo store '{self.name}' e' in sola lettura per questo agente")

    @staticmethod
    def _norm_path(path: str) -> str:
        p = "/" + path.strip().lstrip("/")
        p = re.sub(r"/{2,}", "/", p).lower()
        if not PATH_RE.match(p) or ".." in p:
            raise ValueError("percorso non valido: usa minuscole, cifre e - _ . / (es. /clienti/ristorante-aurora.md)")
        return p

    # ---- scrittura --------------------------------------------------------------
    def write(self, path: str, text: str, tags=(), importance: float = 0.5, author: str = "",
              trust: str = "trusted") -> dict:
        self._need_write()
        path, text = self._norm_path(path), (text or "").strip()
        if not text:
            raise ValueError("testo vuoto")
        if len(text) > config.MEM_MAX_CHARS:
            raise ValueError(f"{len(text)} caratteri: spezza in file piccoli e mirati (max {config.MEM_MAX_CHARS})")
        from .vault import looks_like_secret
        if looks_like_secret(text):
            raise ValueError("sembra contenere un segreto (token, chiave): le credenziali non si scrivono in memoria")
        if trust not in TRUST:
            raise ValueError(f"trust deve essere uno tra {TRUST}")
        tags_s = " ".join(sorted({t.strip().lower() for t in tags if t and t.strip()}))
        imp = min(1.0, max(0.0, float(importance)))
        vec = sqlite_vec.serialize_float32(self.emb.embed_docs([embed_text(path, text)])[0].tolist())
        now = time.time()
        with self._conn() as c:
            row = c.execute("SELECT id, text, version FROM mem WHERE path=? AND deleted=0", (path,)).fetchone()
            if row and row["text"] == text:
                c.execute("UPDATE mem SET tags=?, importance=?, trust=? WHERE id=?", (tags_s, imp, trust, row["id"]))
                return {"path": path, "id": row["id"], "version": row["version"], "op": "noop"}
            if row:
                mid, ver, op = row["id"], row["version"] + 1, "update"
                c.execute("UPDATE mem SET text=?, tags=?, trust=?, importance=?, author=?, version=?, updated=? WHERE id=?",
                          (text, tags_s, trust, imp, author, ver, now, mid))
                c.execute("DELETE FROM mem_fts WHERE rowid=?", (mid,))
                c.execute("DELETE FROM mem_vec WHERE rowid=?", (mid,))
            else:
                ver, op = 1, "create"
                mid = c.execute("INSERT INTO mem(path,text,tags,trust,importance,author,version,created,updated) "
                                "VALUES(?,?,?,?,?,?,?,?,?)", (path, text, tags_s, trust, imp, author, 1, now, now)).lastrowid
            c.execute("INSERT INTO mem_fts(rowid,text,tags,path) VALUES(?,?,?,?)", (mid, text, tags_s, path))
            c.execute("INSERT INTO mem_vec(rowid,embedding) VALUES(?,?)", (mid, vec))
            c.execute("INSERT INTO mem_versions(mem_id,version,op,text,author,ts) VALUES(?,?,?,?,?,?)",
                      (mid, ver, op, text, author, now))
        return {"path": path, "id": mid, "version": ver, "op": op}

    def forget(self, path: str, author: str = "") -> dict:
        """Cancellazione logica: la versione resta nello storico e si puo' recuperare."""
        self._need_write()
        path = self._norm_path(path)
        now = time.time()
        with self._conn() as c:
            row = c.execute("SELECT id, text, version FROM mem WHERE path=? AND deleted=0", (path,)).fetchone()
            if not row:
                raise ValueError(f"niente da dimenticare in {path}")
            c.execute("UPDATE mem SET deleted=1, updated=? WHERE id=?", (now, row["id"]))
            c.execute("DELETE FROM mem_fts WHERE rowid=?", (row["id"],))
            c.execute("DELETE FROM mem_vec WHERE rowid=?", (row["id"],))
            c.execute("INSERT INTO mem_versions(mem_id,version,op,text,author,ts) VALUES(?,?,?,?,?,?)",
                      (row["id"], row["version"] + 1, "delete", row["text"], author, now))
        return {"path": path, "op": "delete"}

    # ---- lettura ------------------------------------------------------------------
    def get(self, path: str) -> dict | None:
        path = self._norm_path(path)
        with self._conn() as c:
            r = c.execute("SELECT * FROM mem WHERE path=? AND deleted=0", (path,)).fetchone()
        return dict(r) if r else None

    def list(self, prefix: str = "/", limit: int = 100) -> list[dict]:
        prefix = "/" + prefix.strip().lstrip("/")
        with self._conn() as c:
            rows = c.execute("SELECT path, substr(text,1,100) AS head, trust, importance, updated FROM mem "
                             "WHERE deleted=0 AND path LIKE ? ORDER BY path LIMIT ?", (prefix + "%", limit)).fetchall()
        return [dict(r) for r in rows]

    def history(self, path: str) -> list[dict]:
        path = self._norm_path(path)
        with self._conn() as c:
            rows = c.execute("SELECT v.version, v.op, v.author, v.ts, v.text FROM mem_versions v JOIN mem m ON m.id=v.mem_id "
                             "WHERE m.path=? ORDER BY v.id", (path,)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._conn() as c:
            n = c.execute("SELECT count(*) FROM mem WHERE deleted=0").fetchone()[0]
            ch = c.execute("SELECT coalesce(sum(length(text)),0) FROM mem WHERE deleted=0").fetchone()[0]
        return {"store": self.name, "entries": n, "chars": ch, "model": self.emb.name, "dim": self.dim,
                "file_kb": round(self.path.stat().st_size / 1024)}

    @staticmethod
    def _fts_query(q: str) -> str:
        """Radici a prefisso per l'italiano: 'rimborsare' trova 'rimborsabile', 'pagamento' trova 'pagamenti'."""
        toks = _TOK.findall(q.lower())
        return " OR ".join((t[:5] + "*") if len(t) >= 7 else (t + "*") if len(t) >= 4 else t for t in toks[:12])

    def search(self, query: str, k: int = 5, prefix: str | None = None) -> list[dict]:
        """Fusione RRF di BM25 e similarita' coseno, con piccola spinta per importanza e freschezza."""
        query = (query or "").strip()
        if not query:
            return []
        pool = config.SEARCH_POOL
        qvec = sqlite_vec.serialize_float32(self.emb.embed_query(query).tolist())
        pfx = ("/" + prefix.strip().lstrip("/")) if prefix else None
        with self._conn() as c:
            fts_q = self._fts_query(query)
            fts = []
            if fts_q:
                try:
                    fts = [r[0] for r in c.execute("SELECT rowid FROM mem_fts WHERE mem_fts MATCH ? ORDER BY bm25(mem_fts, 1.0, 2.5, 2.5) LIMIT ?",
                                                   (fts_q, pool * 2))]
                except sqlite3.OperationalError:
                    fts = []
            vec = [(r[0], r[1]) for r in c.execute("SELECT rowid, distance FROM mem_vec WHERE embedding MATCH ? AND k=? ORDER BY distance",
                                                   (qvec, pool * 2))]
            ids = list({*fts, *(i for i, _ in vec)})
            if not ids:
                return []
            marks = ",".join("?" * len(ids))
            rows = {r["id"]: r for r in c.execute(f"SELECT * FROM mem WHERE deleted=0 AND id IN ({marks})", ids)}
        if pfx:
            rows = {i: r for i, r in rows.items() if r["path"].startswith(pfx)}
        score: dict[int, float] = {}
        for rank, i in enumerate([i for i in fts if i in rows][:pool]):
            score[i] = score.get(i, 0) + config.FTS_WEIGHT / (config.RRF_K + rank)
        dist = dict(vec)
        for rank, i in enumerate([i for i, _ in vec if i in rows][:pool]):
            score[i] = score.get(i, 0) + 1 / (config.RRF_K + rank)
        now = time.time()
        out = []
        for i, s in score.items():
            r = rows[i]
            # importanza e freschezza sono SOLO spareggi (±2% e 0,5%): i punteggi RRF di posizioni vicine differiscono dell'1-2%,
            # un fattore piu' grande farebbe vincere l'importanza sulla pertinenza (visto nelle misure: le voci "importanti" occupavano i primi posti)
            rec = 0.995 + 0.005 * math.exp(-max(0, now - r["updated"]) / 86400 / 180)
            out.append({"path": r["path"], "score": round(s * (1 + 0.04 * (r["importance"] - 0.5)) * rec, 6),
                        "trust": r["trust"], "tags": r["tags"], "updated": r["updated"],
                        "cos": round(1 - dist[i], 3) if i in dist else None, "text": r["text"]})
        out.sort(key=lambda x: -x["score"])
        return out[:k]
