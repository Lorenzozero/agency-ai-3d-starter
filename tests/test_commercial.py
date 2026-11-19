import asyncio
import json
import re
from pathlib import Path

import pytest

from hlab import commercial as co
from hlab import policy, registry, tools
from hlab.events import Events
from hlab.tools import commercial_tools

TS_REPO = Path("C:/Users/User/Desktop/agency-ai-website/packages/shared/src")


def spec():
    return registry.load_all()["commerciale"]


def run(t, **a):
    r = asyncio.run(t.handler(a))
    txt = r["content"][0]["text"]
    return r, (json.loads(txt) if not r.get("is_error") and txt.startswith(("{", "[")) else txt)


def pricing(name):
    return next(t for t in commercial_tools.pricing_tools() if t.name == name)


def drafts(name, ev=None):
    return next(t for t in commercial_tools.draft_tools(spec(), ev) if t.name == name)


ITEMS = [{"name": "Landing 3D", "price_cents": 205_000, "monthly": False}, {"name": "Manutenzione", "price_cents": 2_000, "monthly": True}]


def test_quote_matches_the_portal_billing_logic():
    q = co.compute_quote(ITEMS)
    assert (q["one_off_cents"], q["monthly_cents"]) == (205_000, 2_000)
    assert [(t["tranche"], t["net_cents"], t["stamp_duty_cents"], t["total_cents"]) for t in q["tranches"]] == [
        ("acconto", 102_500, 200, 102_700), ("saldo", 102_500, 200, 102_700)]


def test_rounding_is_javascript_style_not_bankers():
    assert co.js_round(50000.5) == 50001 and co.js_round(2.5) == 3 and round(2.5) == 2     # Python arrotonderebbe al pari
    q = co.compute_quote([{"name": "x", "price_cents": 100_001}])
    assert [t["net_cents"] for t in q["tranches"]] == [50_001, 50_000]                    # come computeQuote in billing.ts


def test_stamp_duty_threshold_and_monthly_exclusion():
    assert co.stamp_duty_for(7_747) == 0 and co.stamp_duty_for(7_748) == 200
    q = co.compute_quote([{"name": "solo canone", "price_cents": 2_000, "monthly": True}])
    assert q["tranches"] == [] and q["monthly_cents"] == 2_000
    assert co.compute_quote([{"name": "x", "price_cents": -5}])["one_off_cents"] == 0     # i negativi diventano 0


def test_money_formatting_and_language():
    assert co.money(205_000, "EUR") == "2.050,00 €" and co.money(205_000, "USD") == "$2,050.00"
    assert (co.document_language("USD"), co.document_language("EUR")) == ("en", "it")


def test_check_quote_rules():
    ok = co.check_quote("landing", "EUR", ITEMS)
    assert ok == {"errors": [], "warnings": []}
    low = co.check_quote("landing", "EUR", [{"name": "x", "price_cents": 30_000}])
    assert any("sotto il listino" in w for w in low["warnings"])
    usd = co.check_quote("landing", "USD", ITEMS)
    assert any("W-8BEN-E" in w for w in usd["warnings"]) and any("bollo" in w for w in usd["warnings"])
    assert any("altro" in w for w in co.check_quote("altro", "EUR", ITEMS)["warnings"])
    assert co.check_quote("landing", "GBP", ITEMS)["errors"] and co.check_quote("sito", "EUR", ITEMS)["errors"]
    assert co.check_quote("landing", "EUR", [])["errors"]
    assert co.check_quote("landing", "EUR", [{"name": "", "price_cents": 1}])["errors"]
    assert co.check_quote("landing", "EUR", [{"name": "x", "price_cents": "tanto"}])["errors"]


def test_pricing_tools():
    _, pl = run(pricing("price_list"))
    assert pl["landing"]["prezzo_partenza_cents"] == 40_000 and "Modulo contatti" in pl["landing"]["voci_suggerite"]
    _, q = run(pricing("compute_quote"), items=ITEMS, currency="USD")
    assert q["language"] == "en" and "flat-rate" in q["notice"] and q["testo"]["una_tantum"] == "$2,050.00"
    r, msg = run(pricing("compute_quote"), items=ITEMS, currency="GBP")
    assert r["is_error"] and "EUR o USD" in msg


def test_draft_is_saved_locally_and_decisions_stick():
    ev = Events()
    r, d = run(drafts("save_quote_draft", ev), client="Studio Fenio", project_type="landing", currency="EUR", items=ITEMS, notes="prima visita")
    assert not r.get("is_error") and "non inviata" in d["stato"] and d["totale_una_tantum"] == "2.050,00 €"
    saved = ev.drafts()[0]
    assert saved["agent"] == "commerciale" and saved["payload"]["quote"]["tranches"][0]["total_cents"] == 102_700
    _, mine = run(drafts("my_drafts", ev))
    assert len(mine) == 1
    assert ev.decide_draft(saved["id"], True) and not ev.decide_draft(saved["id"], False)
    assert ev.drafts() == [] and len(ev.drafts("approved")) == 1


@pytest.mark.parametrize("kw", [dict(client="X", project_type="landing", currency="GBP"), dict(client="X", project_type="boh"),
                                dict(client="", project_type="landing"), dict(client="X", project_type="landing", items=[])])
def test_invalid_drafts_are_refused(kw):
    ev = Events()
    kw.setdefault("items", ITEMS)
    r, _ = run(drafts("save_quote_draft", ev), **kw)
    assert r["is_error"] and ev.drafts() == []


def test_commerciale_tools_and_policy_separation():
    s = registry.load_all()
    c, pm = s["commerciale"], s["project-manager"]
    names = tools.tool_names(c)
    assert "mcp__pricing__compute_quote" in names and "mcp__draft_commerciale__save_quote_draft" in names
    assert "mcp__crm__get_client" in names and not any("prop_" in n for n in names)
    assert not any("pricing" in n or "draft_" in n for n in tools.tool_names(pm))        # il PM non fa preventivi
    ev = Events()
    assert policy.decide(c, "mcp__pricing__compute_quote", {}, ev)[0] == "allow"
    assert policy.decide(c, "mcp__draft_commerciale__save_quote_draft", {}, ev)[0] == "allow"
    assert policy.decide(pm, "mcp__pricing__compute_quote", {}, ev)[0] == "deny"
    assert policy.decide(c, "Bash", {"command": "curl x"}, ev)[0] == "deny"
    assert tools.risk_of("mcp__draft_commerciale__save_quote_draft") == "reversible"


@pytest.mark.skipif(not (TS_REPO / "billing.ts").exists(), reason="repo del portale non disponibile su questa macchina")
def test_constants_have_not_drifted_from_the_portal_source():
    billing = (TS_REPO / "billing.ts").read_text(encoding="utf-8")
    cat = (TS_REPO / "catalog.ts").read_text(encoding="utf-8")

    def num(name):
        return float(re.search(name + r"\s*=\s*([\d_.]+)", billing).group(1).replace("_", ""))

    assert num("STAMP_DUTY_CENTS") == co.STAMP_DUTY_CENTS and num("STAMP_DUTY_THRESHOLD_CENTS") == co.STAMP_DUTY_THRESHOLD_CENTS
    assert num("DEPOSIT_RATE") == co.DEPOSIT_RATE and num("INVOICE_WITHIN_DAYS") == co.INVOICE_WITHIN_DAYS
    m = re.search(r"PROJECT_TYPE_BASE_PRICE_CENTS[^{]*\{([^}]*)\}", cat)
    prices = {k: int(v.replace("_", "")) for k, v in re.findall(r"(\w+):\s*([\d_]+)", m.group(1))}
    assert prices == co.BASE_PRICE_CENTS
    parts = re.search(r"PROJECT_TYPE_SUGGESTED_PARTS[^{]*\{(.*?)\n\};", cat, re.S).group(1)
    assert {k: re.findall(r"'([^']+)'", v) for k, v in re.findall(r"(\w+):\s*\[([^\]]*)\]", parts)} == co.SUGGESTED_PARTS
