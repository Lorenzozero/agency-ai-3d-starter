import pytest

from hlab import config
from hlab.embed import FakeEmbedder
from hlab.memory import MemoryStore


def test_write_update_noop_and_history():
    m = MemoryStore("a")
    assert m.write("/clienti/ristorante-aurora.md", "Landing bordeaux, acconto pagato.", ["cliente"], author="t")["op"] == "create"
    r = m.write("/clienti/ristorante-aurora.md", "Landing bordeaux, saldo pagato.", author="t")
    assert (r["op"], r["version"]) == ("update", 2)
    assert m.write("/clienti/ristorante-aurora.md", "Landing bordeaux, saldo pagato.")["op"] == "noop"
    assert [h["op"] for h in m.history("/clienti/ristorante-aurora.md")] == ["create", "update"]
    assert m.get("/clienti/ristorante-aurora.md")["text"].endswith("saldo pagato.")


def test_forget_is_logical_and_path_reusable():
    m = MemoryStore("a")
    m.write("/lezioni/x.md", "Non rigenerare le immagini gia' approvate.")
    m.forget("/lezioni/x.md", "t")
    assert m.get("/lezioni/x.md") is None
    assert m.search("immagini approvate") == []
    assert [h["op"] for h in m.history("/lezioni/x.md")] == ["create", "delete"]
    assert m.write("/lezioni/x.md", "Nuova versione.")["op"] == "create"
    with pytest.raises(ValueError):
        m.forget("/lezioni/inesistente.md")


@pytest.mark.parametrize("bad", ["../x", "/a/../b", "/Cartella con spazi/x", "", "/" + "a" * 200])
def test_path_validation(bad):
    with pytest.raises(ValueError):
        MemoryStore("a").write(bad, "testo")


def test_text_limits_and_trust():
    m = MemoryStore("a")
    with pytest.raises(ValueError):
        m.write("/x.md", "")
    with pytest.raises(ValueError):
        m.write("/x.md", "a" * (config.MEM_MAX_CHARS + 1))
    with pytest.raises(ValueError):
        m.write("/x.md", "ok", trust="boh")
    m.write("/web/p.md", "pagina del lead", trust="untrusted")
    assert m.search("pagina lead")[0]["trust"] == "untrusted"


def test_hybrid_search_text_prefix_and_ranking(monkeypatch):
    monkeypatch.setattr(config, "FTS_WEIGHT", 1.0)      # qui si prova il ramo per parole; con l'embedder finto i vettori non hanno senso
    m = MemoryStore("a")
    m.write("/clienti/studio-nova.md", "Studio Nova toelettatura Piove di Sacco, scadenza dominio il 15 novembre.", importance=0.7)
    m.write("/clienti/ristorante-aurora.md", "Ristorante Aurora ristorante Arzerello, acconto pagato, landing chiusa.")
    m.write("/regole/listino.md", "Listino: sito vetrina 1.650 euro, sito 3D 2.050 euro, acconto non rimborsabile.")
    hits = m.search("scadenze del dominio", k=3)           # "scadenze" trova "scadenza" grazie al prefisso
    assert hits and hits[0]["path"] == "/clienti/studio-nova.md"
    assert [h["path"] for h in m.search("acconto", prefix="/regole")] == ["/regole/listino.md"]
    assert m.search("") == []


def test_readonly_store_reads_but_never_writes():
    MemoryStore("_shared").write("/regole/iva.md", "Esenzione IVA articolo 7-ter per clienti USA.")
    ro = MemoryStore("_shared", readonly=True)
    assert ro.search("esenzione IVA")[0]["path"] == "/regole/iva.md"
    with pytest.raises(PermissionError):
        ro.write("/regole/x.md", "no")
    with pytest.raises(PermissionError):
        ro.forget("/regole/iva.md")


def test_changing_model_migrates_the_index_automatically():
    old = MemoryStore("a")
    old.write("/clienti/dog.md", "Studio Nova toelettatura scadenza dominio", author="t")

    class Small(FakeEmbedder):
        name, dim = "fake-128", 128

    new = MemoryStore("a", embedder=Small())                 # dimensione diversa: reindicizza da solo
    assert new.stats()["dim"] == 128 and new.stats()["model"] == "fake-128"
    assert new.search("scadenza dominio")[0]["path"] == "/clienti/dog.md"
    ro = MemoryStore("a", readonly=True, embedder=Small())   # lo store in sola lettura non resta rotto
    assert ro.search("toelettatura")[0]["path"] == "/clienti/dog.md"
    assert [h["op"] for h in new.history("/clienti/dog.md")] == ["create"]    # lo storico non cambia


def test_readonly_store_migrates_but_still_cannot_write():
    MemoryStore("_shared").write("/regole/x.md", "regola di prova sulla fatturazione")

    class Other(FakeEmbedder):
        name, dim = "fake-64", 64

    ro = MemoryStore("_shared", readonly=True, embedder=Other())
    assert ro.search("fatturazione")
    with pytest.raises(PermissionError):
        ro.write("/regole/y.md", "no")


def test_each_agent_has_its_own_file():
    a, b = MemoryStore("agente_a"), MemoryStore("agente_b")
    a.write("/clienti/x.md", "dato riservato dell'agente a")
    assert b.search("riservato") == []
    assert a.path != b.path
