import sqlite3
import stat
import tarfile

from hlab import backup


def make_data(tmp_path):
    data, agents = tmp_path / "data", tmp_path / "agents"
    (data / "memory" / "coo").mkdir(parents=True)
    agents.mkdir()
    (data / "memory" / "coo" / "nota.md").write_text("ricordo importante", encoding="utf-8")
    (agents / "coo.md").write_text("---\nname: coo\n---\nprompt", encoding="utf-8")
    (data / "api_token").write_text("segreto", encoding="utf-8")
    c = sqlite3.connect(data / "docs.db")
    c.execute("PRAGMA journal_mode=WAL")                                   # come in produzione: con il WAL aperto la copia a mano non basta
    c.execute("CREATE TABLE docs(path TEXT, data TEXT)")
    c.execute("INSERT INTO docs VALUES('tasks/a', '{\"tarea\": \"uno\"}')")
    c.commit()
    return data, agents, c


def test_backup_is_a_consistent_private_copy_of_everything_that_matters(tmp_path):
    data, agents, conn = make_data(tmp_path)
    out = backup.run(data, tmp_path / "bk", agents, day="2026-11-05")
    conn.close()
    snap = tmp_path / "bk" / "2026-11-05"
    assert {"docs.db", "api_token", "memory.tar.gz", "agents.tar.gz"} <= set(out["copiati"]) and out["giorno"] == "2026-11-05"
    assert sqlite3.connect(snap / "docs.db").execute("SELECT data FROM docs").fetchall() == [('{"tarea": "uno"}',)]     # la copia si apre ed e' completa
    with tarfile.open(snap / "memory.tar.gz") as t:
        assert "memory/coo/nota.md" in t.getnames()
    with tarfile.open(snap / "agents.tar.gz") as t:
        assert "agents/coo.md" in t.getnames()
    assert stat.S_IMODE((tmp_path / "bk").stat().st_mode) == 0o700 and stat.S_IMODE(snap.stat().st_mode) == 0o700          # cartella privata
    assert stat.S_IMODE((snap / "api_token").stat().st_mode) == 0o600
    assert not (snap / ".env").exists()


def test_only_the_last_seven_days_are_kept_and_a_second_run_in_a_day_replaces_the_copy(tmp_path):
    data, agents, conn = make_data(tmp_path)
    conn.close()
    for d in range(1, 11):
        backup.run(data, tmp_path / "bk", agents, day=f"2026-11-{d:02d}")
    kept = sorted(p.name for p in (tmp_path / "bk").iterdir())
    assert kept == [f"2026-11-{d:02d}" for d in range(4, 11)]                                    # i 7 piu' recenti
    (tmp_path / "bk" / "2026-11-10" / "vecchio.txt").write_text("residuo", encoding="utf-8")
    backup.run(data, tmp_path / "bk", agents, day="2026-11-10")
    assert not (tmp_path / "bk" / "2026-11-10" / "vecchio.txt").exists() and not list((tmp_path / "bk").glob("*.tmp"))
    (tmp_path / "bk" / "altro").mkdir()                                                           # una cartella che non e' un giorno non si tocca
    backup.run(data, tmp_path / "bk", agents, day="2026-11-11")
    assert (tmp_path / "bk" / "altro").exists()


def test_missing_optional_files_are_skipped_without_failing(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    out = backup.run(data, tmp_path / "bk", tmp_path / "nessun_agente", day="2026-11-05")
    assert out["copiati"] == [] and (tmp_path / "bk" / "2026-11-05").is_dir()
