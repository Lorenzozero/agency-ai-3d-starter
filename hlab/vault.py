"""Cassaforte delle credenziali del laboratorio (GitHub, Netlify...): cifrata, con livelli e revisione.

Cosa possono fare gli agenti (richiesta di Lorenzo: leggere, inserire, cancellare) e COME, per non regalare i segreti a un'iniezione di prompt:
  - LEGGERE: elenco dei METADATI (nome, servizio, ambito, ultimo uso) libero; il TESTO IN CHIARO non passa mai dal modello. Le credenziali si USANO
    attraverso gli esecutori (powers.py), che le iniettano da soli nella chiamata. Il testo in chiaro lo mostra solo la CLI a Lorenzo (`cred reveal`).
  - INSERIRE: l'agente puo' solo CHIEDERE (proposta con motivo); il valore lo scrive Lorenzo nella CLI con input nascosto (`cred add`).
  - CANCELLARE: l'agente puo' solo CHIEDERE; la cancellazione e' logica (30 giorni di recupero con `cred restore`), quella definitiva (`cred purge`) e' solo umana.
Cifratura AES-256-GCM, chiave in `data/vault.key` (permessi 600) o in HLAB_VAULT_KEY (base64). Ogni lettura in chiaro e ogni uso restano nel registro.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import config

RETENTION_S = 30 * 86400
SCHEMA = """
CREATE TABLE IF NOT EXISTS creds(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, service TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '', nonce BLOB NOT NULL, secret BLOB NOT NULL,
  meta TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, updated REAL NOT NULL, last_used REAL, uses INTEGER NOT NULL DEFAULT 0, deleted_at REAL);
CREATE UNIQUE INDEX IF NOT EXISTS creds_live ON creds(name) WHERE deleted_at IS NULL;
CREATE TABLE IF NOT EXISTS cred_log(id INTEGER PRIMARY KEY, ts REAL NOT NULL, name TEXT NOT NULL, action TEXT NOT NULL, who TEXT NOT NULL, purpose TEXT NOT NULL DEFAULT '');
"""
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,40}$")
SERVICES = ("github", "netlify", "other")
SECRET_RE = re.compile(r"(ghp_|github_pat_|gho_|ghs_|nfp_|sk-or-v1-|sk-ant-|gsk_|AKIA[0-9A-Z]{8}|xox[bp]-|-----BEGIN [A-Z ]*PRIVATE KEY)")


def looks_like_secret(text: str) -> bool:
    return bool(SECRET_RE.search(text or ""))


class Vault:
    def __init__(self, path=None, key: bytes | None = None):
        self.path = path or (config.DATA_DIR / "vault.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.key = key or self._load_key()
        c = sqlite3.connect(self.path)
        c.executescript(SCHEMA)
        c.close()
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    def _load_key(self) -> bytes:
        env = os.environ.get("HLAB_VAULT_KEY")
        if env:
            return base64.b64decode(env)
        kp = self.path.parent / "vault.key"
        if not kp.exists():
            kp.write_bytes(os.urandom(32))
            if os.name != "nt":
                os.chmod(kp, 0o600)
        return kp.read_bytes()

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _log(self, c, name: str, action: str, who: str, purpose: str = "") -> None:
        c.execute("INSERT INTO cred_log(ts,name,action,who,purpose) VALUES(?,?,?,?,?)", (time.time(), name, action, who, purpose[:200]))

    # ---- inserimento e aggiornamento (solo umano: serve il valore) ----
    def add(self, name: str, service: str, secret: str, scope: str = "", meta: dict | None = None, who: str = "lorenzo") -> int:
        if not NAME_RE.match(name):
            raise ValueError("nome non valido: minuscole, cifre e . _ - (2-41 caratteri)")
        if service not in SERVICES:
            raise ValueError(f"servizio non valido: {', '.join(SERVICES)}")
        if len(secret) < 8:
            raise ValueError("segreto troppo corto")
        nonce = os.urandom(12)
        blob = AESGCM(self.key).encrypt(nonce, secret.encode(), f"{name}|{service}".encode())        # nome e servizio sono dati autenticati
        now = time.time()
        with self._conn() as c:
            old = c.execute("SELECT id FROM creds WHERE name=? AND deleted_at IS NULL", (name,)).fetchone()
            if old:
                c.execute("UPDATE creds SET service=?, scope=?, nonce=?, secret=?, meta=?, updated=? WHERE id=?",
                          (service, scope, nonce, blob, json.dumps(meta or {}), now, old["id"]))
                self._log(c, name, "update", who)
                return old["id"]
            cid = c.execute("INSERT INTO creds(name,service,scope,nonce,secret,meta,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                            (name, service, scope, nonce, blob, json.dumps(meta or {}), now, now)).lastrowid
            self._log(c, name, "add", who)
            return cid

    # ---- lettura dei metadati (libera) ----
    def list(self, include_deleted: bool = False) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT id,name,service,scope,meta,created,updated,last_used,uses,deleted_at FROM creds "
                             + ("" if include_deleted else "WHERE deleted_at IS NULL ") + "ORDER BY name").fetchall()
        return [{**dict(r), "meta": json.loads(r["meta"])} for r in rows]

    def _decrypt(self, r) -> str:
        return AESGCM(self.key).decrypt(r["nonce"], r["secret"], f"{r['name']}|{r['service']}".encode()).decode()

    # ---- uso (esecutori) e rivelazione (solo umano) ----
    def use(self, name: str, purpose: str, who: str = "executor") -> str:
        """Per il codice degli esecutori: restituisce il segreto, registra l'uso. Mai chiamata da strumenti esposti al modello."""
        with self._conn() as c:
            r = c.execute("SELECT * FROM creds WHERE name=? AND deleted_at IS NULL", (name,)).fetchone()
            if not r:
                raise KeyError(f"credenziale '{name}' non trovata")
            c.execute("UPDATE creds SET last_used=?, uses=uses+1 WHERE id=?", (time.time(), r["id"]))
            self._log(c, name, "use", who, purpose)
            return self._decrypt(r)

    def reveal(self, name: str, who: str = "lorenzo") -> str:
        with self._conn() as c:
            r = c.execute("SELECT * FROM creds WHERE name=? AND deleted_at IS NULL", (name,)).fetchone()
            if not r:
                raise KeyError(f"credenziale '{name}' non trovata")
            self._log(c, name, "reveal", who)
            return self._decrypt(r)

    # ---- cancellazione: logica, recuperabile; definitiva solo umana ----
    def delete(self, name: str, who: str = "lorenzo") -> bool:
        with self._conn() as c:
            n = c.execute("UPDATE creds SET deleted_at=? WHERE name=? AND deleted_at IS NULL", (time.time(), name)).rowcount
            if n:
                self._log(c, name, "delete", who)
            return n == 1

    def restore(self, name: str, who: str = "lorenzo") -> bool:
        with self._conn() as c:
            r = c.execute("SELECT id FROM creds WHERE name=? AND deleted_at IS NOT NULL AND deleted_at>? ORDER BY deleted_at DESC LIMIT 1",
                          (name, time.time() - RETENTION_S)).fetchone()
            if not r or c.execute("SELECT 1 FROM creds WHERE name=? AND deleted_at IS NULL", (name,)).fetchone():
                return False
            c.execute("UPDATE creds SET deleted_at=NULL WHERE id=?", (r["id"],))
            self._log(c, name, "restore", who)
            return True

    def purge(self, name: str | None = None, who: str = "lorenzo") -> int:
        """Cancellazione definitiva: solo CLI umana. Senza nome elimina quelle cancellate da piu' di 30 giorni."""
        with self._conn() as c:
            if name:
                n = c.execute("DELETE FROM creds WHERE name=? AND deleted_at IS NOT NULL", (name,)).rowcount
            else:
                n = c.execute("DELETE FROM creds WHERE deleted_at IS NOT NULL AND deleted_at<?", (time.time() - RETENTION_S,)).rowcount
            if n:
                self._log(c, name or "*", "purge", who)
            return n

    def log(self, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT ts,name,action,who,purpose FROM cred_log ORDER BY id DESC LIMIT ?", (limit,))]
