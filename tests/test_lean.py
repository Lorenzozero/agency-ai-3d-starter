import asyncio
import json

import pytest

from hlab import lean
from hlab.events import Events
from hlab.lean import Budget, BudgetExceeded, Lean


class Db:
    """Finto CRM: risponde per forma della query, come nei test degli strumenti."""
    def query(self, sql, params=()):
        if "union all" in sql:
            return [{"kind": "progetto", "title": "Portale", "client": "Officina Rapida", "due": "2026-11-15", "status": "in_sviluppo"}]
        if "days_idle" in sql:
            return [{"id": "p", "name": "Sito", "client": "Fenio", "status": "in_sviluppo", "deadline": None, "days_idle": 20}]
        if "from leads" in sql:
            return [{"name": "Pizzeria X", "category": "ristorante", "status": "nuovo", "score": 92}, {"name": "Bar Y", "category": "bar", "status": "qualificato", "score": 70}]
        if "from projects p join clients" in sql:
            return [{"name": "A", "status": "in_sviluppo"}, {"name": "B", "status": "in_sviluppo"}]
        if "from clients where name" in sql:
            return [{"id": "11111111-1111-1111-1111-111111111111", "name": "Ristorante Aurora", "contact_name": "Marco", "contact_email": "m@x.it", "contact_phone": "+39 3451234567",
                     "vat_number": None, "address": None, "opening_hours": None, "old_website": None, "notes": None, "created_at": "", "updated_at": ""}]
        if "from projects where client_id" in sql:
            return [{"id": "p1", "name": "Landing", "status": "lanciato", "deadline": "2026-12-01"}]
        if "count(*) as n from tasks" in sql:
            return [{"n": 2}]
        return []


def run(coro):
    return asyncio.run(coro)


def no_llm(*a, **k):
    raise AssertionError("questo percorso non deve chiamare il modello")


def eng(complete=None):
    return Lean(db=Db(), events=Events(), complete=complete or no_llm)


@pytest.mark.parametrize("text,intent", [
    ("com'e' lo stato di tutto?", "status"), ("cosa scade nei prossimi 10 giorni", "deadlines"), ("quali progetti sono fermi", "stale"),
    ("mostrami i lead", "leads"), ("crea un lead Pizzeria Roma 3351234567", "lead_create"), ("fammi un preventivo per Rossi: landing 1500", "quote"),
    ("crea un sito per una pasticceria a Torino", "site"), ("cosa sai di Ristorante Aurora?", "client"), ("boh, tu che dici?", "unknown")])
def test_intent_detection_is_rule_based(text, intent):
    assert lean.detect(text)[0] == intent


def test_status_deadlines_stale_leads_client_use_zero_tokens():
    e = eng()
    for q, frag in [("stato", "Progetti: 2 in_sviluppo"), ("scadenze", "Officina Rapida / Portale"), ("progetti fermi", "fermo da 20 giorni"),
                    ("lead", "Pizzeria X"), ("cosa sai di Ristorante Aurora", "1 progetti")]:
        a = run(e.ask(q))
        assert frag in a.text and a.tokens == 0 and a.calls == 0, q


def test_quote_parsed_with_rules_costs_nothing_and_saves_a_draft():
    ev = Events()
    a = run(Lean(db=Db(), events=ev, complete=no_llm).ask("preventivo per Studio Fenio: landing con design 1500 euro, testi e SEO 300, dominio e hosting 250"))
    assert a.tokens == 0 and "2.050,00 €" in a.text and "1.027,00 €" in a.text and "bozza #1" in a.text
    d = ev.drafts()[0]
    assert d["client"] == "Studio Fenio" and d["payload"]["quote"]["one_off_cents"] == 205_000 and d["agent"] == "lean"


def test_quote_in_dollars_with_monthly_item_and_warnings():
    a = run(eng().ask("preventivo per Officina Rapida: web app 3.200 $, manutenzione 20 al mese"))
    assert "$3,200.00" in a.text and "/mese" in a.text and "W-8BEN-E" in a.text


def test_quote_falls_back_to_one_tiny_model_call_when_rules_cannot_read_it():
    calls = []

    async def fake(prompt, system, max_tokens):
        calls.append(max_tokens)
        return '```json\n{"client":"Bianchi","project_type":"landing","currency":"EUR","items":[{"name":"Sito","price_cents":90000,"monthly":false},],}\n```'

    a = run(eng(fake).ask("serve un preventivo, il sito base per il signor Bianchi, novecento euro tutto compreso"))
    assert "Bianchi" in a.text and "900,00 €" in a.text and a.calls == 1 and 0 < a.tokens < lean.CAPS["quote"] and calls == [400]


def test_lead_creation_only_writes_a_proposal_never_the_crm():
    ev = Events()
    a = run(Lean(db=Db(), events=ev, complete=no_llm).ask("crea un lead Pizzeria Roma 335 1234567 info@pizzeriaroma.it"))
    assert a.tokens == 0 and "il CRM non e' stato toccato" in a.text
    p = ev.proposals()[0]
    body = json.loads(p["new_value"])
    assert p["entity"] == "lead" and p["field"] == "create" and body["email"] == "info@pizzeriaroma.it" and "Pizzeria Roma" in body["name"]


def test_unknown_requests_use_one_classification_call_under_900_tokens():
    seen = []

    async def fake(prompt, system, max_tokens):
        seen.append(max_tokens)
        return "stale" if "classifica" in system.lower() else "ciao"

    a = run(eng(fake).ask("boh, tu che dici?"))
    assert a.intent == "stale" and a.calls == 1 and a.tokens < 900 and seen == [300]


def test_budget_blocks_before_spending_and_after_overspending():
    b = Budget(100)
    with pytest.raises(BudgetExceeded):
        b.check(500, 100)
    with pytest.raises(BudgetExceeded):
        Budget(100).charge(80, 40)

    async def hungry(prompt, system, max_tokens):
        return "x" * 100_000                                         # risposta enorme: il tetto la ferma
    a = run(eng(hungry).ask("preventivo, il sito per il signor Bianchi, novecento euro"))
    assert "tetto di token" in a.text


def test_site_spec_is_one_call_capped_at_10k_tokens_and_repairs_dirty_json():
    calls = []
    spec = {"repo_name": "pasticceria-torino", "brand": {"name": "Dolce Torino"}, "sections": [{"type": "hero_image"}, {"type": "cta"}]}

    async def fake(prompt, system, max_tokens, budget):
        calls.append(max_tokens)
        budget.charge(2100, 3000)
        return "Ecco lo spec:\n```json\n" + json.dumps(spec)[:-1] + ",}\n```"                          # virgola finale sporca: la ripara il codice
    out, used = run(lean.site_spec("pasticceria artigianale a Torino, toni caldi", fake))
    assert out["brand"]["name"] == "Dolce Torino" and used == 5100 <= lean.SITE_CAP and len(calls) == 1 and calls[0] <= 5500
    assert lean.estimate_tokens({"p": lean.SITE_SYS}) < 700                                           # il prompt di sistema e' compatto


def test_site_spec_never_makes_a_second_call_and_never_exceeds_the_cap():
    calls = []

    async def garbage(prompt, system, max_tokens, budget):
        calls.append(1)
        budget.charge(2000, 2000)
        return "non e' json"
    with pytest.raises(ValueError, match="nessun secondo tentativo"):
        run(lean.site_spec("un sito", garbage))
    assert len(calls) == 1

    async def over(prompt, system, max_tokens, budget):
        budget.charge(6000, 6000)                                    # 12.000 > 10.000
        return "{}"
    with pytest.raises(BudgetExceeded):
        run(lean.site_spec("un sito", over))


def test_every_request_leaves_its_token_count_in_the_log():
    ev = Events()
    run(Lean(db=Db(), events=ev, complete=no_llm).ask("stato"))
    rows = [e for e in ev.recent(5) if e["kind"] == "lean"]
    assert rows and rows[0]["tool"] == "status" and rows[0]["tokens"] == 0


def test_cli_ask_and_tokens_commands(capsys, monkeypatch):
    from hlab import __main__ as cli
    monkeypatch.setattr(lean, "Lean", lambda: Lean(db=Db(), events=Events(), complete=no_llm))
    assert cli.main(["ask", "quali", "progetti", "sono", "fermi"]) == 0
    out = capsys.readouterr().out
    assert "fermo da 20 giorni" in out and "[stale | 0 token, 0 chiamate" in out
    assert cli.main(["tokens"]) == 0 and "stale" in capsys.readouterr().out
