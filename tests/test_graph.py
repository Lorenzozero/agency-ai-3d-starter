import asyncio
import json

from hlab import config
from hlab.graph import Graph
from hlab.memory import MemoryStore
from hlab.tools import graph_tools


def stores():
    shared = MemoryStore(config.SHARED_STORE)
    shared.write("/regole/fiscale-clienti-usa.md", "Clienti USA: 0% VAT art. 7-ter, modulo W-8BEN-E per la ritenuta del 30%. Fiscale e bollo.")
    shared.write("/regole/listino-prezzi.md", "Listino: landing 400 euro, webapp 800 euro, mobile 1.200 euro.")
    shared.write("/regole/valute-eur-usd.md", "Valute EUR e USD, mai convertite. Cliente in dollari: documenti in inglese.")
    acc = MemoryStore("account_crm")
    acc.write("/clienti/quick-key.md", "Officina Rapida, cliente in Nevada, USA, progetto pilota in dollari, landing page con contratto B2B.")
    acc.write("/clienti/ristorante-aurora.md", "Ristorante Aurora, ristorante di Arzerello (Padova), landing 3D chiusa a 2.050 euro, acconto pagato.")
    return [shared, acc]


def test_manual_graph_paths_and_depth():
    g = Graph()
    a, b, c, d = (g.upsert_node(k, n) for k, n in (("cliente", "Alfa"), ("servizio", "landing"), ("regola", "listino"), ("paese", "usa")))
    g.link(a, "mentions", b, 1.0, "alfa.md")
    g.link(c, "mentions", b, 1.0, "listino.md")
    g.link(c, "mentions", d, 1.0, "listino.md")
    rel = {r["name"]: r for r in g.related("alfa", depth=1)}
    assert set(rel) == {"landing"} and "mentions" in rel["landing"]["percorso"]
    deep = {r["name"] for r in g.related("alfa", depth=3)}
    assert deep == {"landing", "listino", "usa"}
    assert g.related("inesistente") == [] and g.find("alf")[0]["name"] == "Alfa"
    assert g.upsert_node("cliente", "Alfa") == a                                   # idempotente


def test_rebuild_links_a_client_to_the_rules_that_apply_to_it():
    g = Graph()
    info = g.rebuild(stores())
    assert info["nodi"] > 10 and info["archi"] > 8
    rel = g.related("quick-key", depth=2)
    names = {r["name"] for r in rel}
    assert {"usa", "usd", "landing"} <= names                                      # entita' citate nella scheda
    assert {"fiscale-clienti-usa", "valute-eur-usd", "listino-prezzi"} <= names    # regole raggiunte passando dalle entita'
    fiscal = next(r for r in rel if r["name"] == "fiscale-clienti-usa")
    assert "paese:usa" in fiscal["percorso"] and fiscal["prova"].startswith("/")   # percorso spiegabile, con la prova
    aurora = {r["name"] for r in g.related("ristorante-aurora", depth=2)}
    assert "listino-prezzi" in aurora and "fiscale-clienti-usa" not in aurora    # un cliente italiano non si collega alle regole USA
    assert g.rebuild(stores())["nodi"] == info["nodi"]                             # ricostruzione idempotente


def test_graph_tools_for_the_agent():
    Graph().rebuild(stores())
    t = {x.name: x for x in graph_tools.make_tools()}
    r = asyncio.run(t["graph_related"].handler({"entity": "quick-key", "depth": 2}))
    assert any(x["name"] == "fiscale-clienti-usa" for x in json.loads(r["content"][0]["text"]))
    assert asyncio.run(t["graph_related"].handler({"entity": "sconosciuto"}))["is_error"]
    assert json.loads(asyncio.run(t["graph_find"].handler({"query": "quick"}))["content"][0]["text"])[0]["tipo"] == "cliente"
