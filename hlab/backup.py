"""Copia notturna dei dati di hlab: archivio della console, eventi, memoria, grafo, code, vault, agenti e file di stato.

I database SQLite si copiano con l'API di backup di SQLite: una fotografia coerente anche mentre il servizio scrive (copiare il file a mano, con il
WAL aperto, puo' dare una copia rotta). Ogni giorno una cartella `AAAA-MM-GG`, si tengono gli ultimi 7. La cartella e' privata (700) perche' contiene
anche il token e la chiave del vault. NON contiene il file `.env` con le chiavi dei fornitori: quelle si rifanno, e non vanno moltiplicate.

Le copie stanno sullo STESSO server: proteggono da errori, cancellazioni e file rovinati, non dalla perdita del server. Per quello serve una
destinazione esterna.

Uso: `python -m hlab.backup`  (lo lancia il timer hlab-backup.timer, ogni notte)
Ripristino: ferma i servizi, copia i file della cartella del giorno in data/ (e agents/.tar.gz in agents/), riavvia.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tarfile
import time
from pathlib import Path

from . import config

DBS = ("docs.db", "events.db", "graph.db", "jobs.db", "vault.db")
FILES = ("api_token", "vault.key", "notify_state.json", "netlify_plan.json", "uptime_state.json")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def snapshot_db(src: Path, dst: Path) -> None:
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        with d:
            s.backup(d)
    finally:
        s.close()
        d.close()


def pack(folder: Path, dst: Path, arcname: str) -> None:
    with tarfile.open(dst, "w:gz") as t:
        t.add(folder, arcname=arcname)


def run(data: Path | None = None, root: Path | None = None, agents: Path | None = None, keep: int = 7, day: str | None = None) -> dict:
    data = Path(data or config.DATA_DIR)
    root = Path(root or Path.home() / "hermes_backups")
    agents = Path(agents or config.AGENTS_DIR)
    day = day or time.strftime("%Y-%m-%d")
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    tmp, target = root / f"{day}.tmp", root / day
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(mode=0o700)
    done = []
    for name in DBS:
        if (data / name).exists():
            snapshot_db(data / name, tmp / name)
            os.chmod(tmp / name, 0o600)
            done.append(name)
    for name in FILES:
        if (data / name).exists():
            shutil.copy2(data / name, tmp / name)
            os.chmod(tmp / name, 0o600)
            done.append(name)
    for folder, arc, out in ((data / "memory", "memory", "memory.tar.gz"), (agents, "agents", "agents.tar.gz")):
        if folder.is_dir():
            pack(folder, tmp / out, arc)
            os.chmod(tmp / out, 0o600)
            done.append(out)
    if target.exists():                                              # secondo giro nello stesso giorno: sostituisce la copia, non ne aggiunge
        shutil.rmtree(target)
    tmp.rename(target)
    os.chmod(target, 0o700)
    days = sorted(p for p in root.iterdir() if p.is_dir() and DAY_RE.match(p.name))
    for old in days[:-keep] if keep > 0 else []:
        shutil.rmtree(old)
    return {"giorno": day, "copiati": done, "cartelle_conservate": len(days[-keep:]) if keep > 0 else len(days)}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
