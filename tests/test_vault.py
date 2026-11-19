import os
import sqlite3
import time

import pytest
from cryptography.exceptions import InvalidTag

from hlab.memory import MemoryStore
from hlab.vault import Vault, looks_like_secret

TOKEN = "ghp_" + "A1b2C3d4" * 5


def test_secret_is_encrypted_at_rest_and_list_shows_only_metadata():
    v = Vault()
    v.add("github-azienda", "github", TOKEN, scope="repo agency-ai")
    assert TOKEN.encode() not in v.path.read_bytes() and TOKEN[4:20].encode() not in v.path.read_bytes()
    row = v.list()[0]
    assert row["name"] == "github-azienda" and row["scope"] == "repo agency-ai" and "secret" not in row and "nonce" not in row
    assert v.reveal("github-azienda") == TOKEN


def test_use_is_logged_and_updates_usage_counters():
    v = Vault()
    v.add("netlify-azienda", "netlify", "nfp_" + "x" * 30)
    assert v.use("netlify-azienda", "preview #3", who="executor").startswith("nfp_")
    assert v.list()[0]["uses"] == 1 and v.list()[0]["last_used"]
    log = v.log()
    assert [(r["action"], r["who"], r["purpose"]) for r in log[:2]] == [("use", "executor", "preview #3"), ("add", "lorenzo", "")]
    with pytest.raises(KeyError):
        v.use("non-esiste", "x")


def test_ciphertext_is_bound_to_name_and_service():
    v = Vault()
    v.add("a-cred", "github", TOKEN)
    with sqlite3.connect(v.path) as c:
        c.execute("UPDATE creds SET name='b-cred'")                # spostare il blob su un altro nome non lo rende leggibile
    with pytest.raises(InvalidTag):
        v.reveal("b-cred")


@pytest.mark.parametrize("name,service,secret", [("MAIUSCOLO", "github", TOKEN), ("ok-name", "inventato", TOKEN), ("ok-name", "github", "corto"), ("a", "github", TOKEN)])
def test_invalid_inputs_are_refused(name, service, secret):
    with pytest.raises(ValueError):
        Vault().add(name, service, secret)


def test_update_keeps_the_same_entry():
    v = Vault()
    first = v.add("tok", "github", TOKEN)
    assert v.add("tok", "github", TOKEN + "nuovo") == first and len(v.list()) == 1 and v.reveal("tok").endswith("nuovo")


def test_delete_is_soft_recoverable_for_30_days_and_purge_is_final():
    v = Vault()
    v.add("tok", "github", TOKEN)
    assert v.delete("tok") and v.list() == [] and len(v.list(include_deleted=True)) == 1
    with pytest.raises(KeyError):
        v.use("tok", "x")                                          # cancellata: non usabile
    assert v.restore("tok") and v.reveal("tok") == TOKEN
    v.delete("tok")
    with sqlite3.connect(v.path) as c:
        c.execute("UPDATE creds SET deleted_at=?", (time.time() - 31 * 86400,))
    assert not v.restore("tok")                                    # oltre i 30 giorni
    assert v.purge() == 1 and v.list(include_deleted=True) == []


def test_files_are_private_and_key_can_come_from_the_environment(monkeypatch, tmp_path):
    v = Vault()
    assert oct(os.stat(v.path).st_mode)[-3:] == "600" and oct(os.stat(v.path.parent / "vault.key").st_mode)[-3:] == "600"
    import base64
    monkeypatch.setenv("HLAB_VAULT_KEY", base64.b64encode(os.urandom(32)).decode())
    v2 = Vault(path=tmp_path / "altra" / "vault.db")
    v2.add("tok", "github", TOKEN)
    assert not (tmp_path / "altra" / "vault.key").exists() and v2.reveal("tok") == TOKEN


def test_secrets_can_never_be_written_into_agent_memory():
    for s in (TOKEN, "chiave sk-or-v1-" + "a" * 40, "gsk_" + "b" * 30, "-----BEGIN PRIVATE KEY-----"):
        assert looks_like_secret(s)
        with pytest.raises(ValueError, match="segreto"):
            MemoryStore("a").write("/note/x.md", f"ricordati: {s}")
    assert not looks_like_secret("il token del bot non deve finire nei log")
    MemoryStore("a").write("/note/ok.md", "il token del bot non deve finire nei log")
