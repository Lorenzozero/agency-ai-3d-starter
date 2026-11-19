import asyncio
import json
import time

import pytest

from hlab import config, powers, registry, sitefactory, tools
from hlab.events import Events
from hlab.powers import Powers
from hlab.tools import powers_tools, site_tools
from hlab.vault import Vault

def sec(t, role, **kw):
    return {"type": t, "role": role, **kw}


SPEC = {"repo_name": "pizzeria-mario", "brand": {"name": "Da Mario"}, "sections": [
    sec("hero_shader", "attenzione", title="Pizza vera", subtitle="x"), sec("text_reveal", "problema", text="p"), sec("split", "soluzione", title="s"),
    sec("offer", "offerta", title="o", points=["a"]), sec("stats", "usp", title="u"), sec("split", "prova", title="p"),
    sec("services", "extra", title="Menu", extra_why="il ristorante vende pizze e serve il menu"),
    sec("cta", "cta", title="c"), sec("faq", "faq", title="f", items=[{"q": "a?", "a": "b"}] * 3)]}
OK_REPORT = {"marca": "Da Mario", "repo": "pizzeria-mario", "sezioni": ["hero_shader", "cta"], "ruoli": list(sitefactory.SPINE), "errori_js": [], "immagini_rotte": 0, "richieste_fallite": 0, "immagini": {"totale": 4, "fornitori": ["pexels"], "segnaposto": 0}}


@pytest.fixture(autouse=True)
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def fake(report=None, calls=None):
    def runner(args, timeout):
        (calls if calls is not None else []).append(args)
        if args[0] == "build":
            out = __import__("pathlib").Path(args[2])
            (out / "report.json").write_text(json.dumps(report or OK_REPORT))
            return report or OK_REPORT
        if args[0] == "pull":
            out = __import__("pathlib").Path(args[2])
            out.mkdir(parents=True, exist_ok=True)
            old = {"repo_name": args[1], "brand": {"name": "Bistrot Centrale"}, "sections": [{"type": "hero_shader", "title": "Vecchio"}, {"type": "text_reveal", "text": "t"}, {"type": "cta", "title": "c"}]}
            (out / "spec.json").write_text(json.dumps(old))
            (out / "origin.json").write_text(json.dumps({"repo": args[1], "doc": {}}))
            return {"repo": args[1], "marca": "Bistrot Centrale", "sezioni": 3, "immagini_riusabili": 0}
        if args[0] == "list":
            return {"siti": [{"repo": "bistrot-centrale", "url": "https://x.github.io/bistrot-centrale/", "marca": "Bistrot Centrale", "ha_scheletro": False}]}
        if args[0] == "publish":
            return {"url": "https://lorenzozero.github.io/pizzeria-mario/", "repo": "pizzeria-mario", "file": 9}
        return {"prompt": "manuale"}
    return runner


def test_slug_and_spec_are_validated_before_anything_is_written(lab):
    for bad in ("", "ab", "../etc", "a/b-c", "a b c", "x" * 61):
        with pytest.raises(ValueError):
            sitefactory.workdir(bad)
    with pytest.raises(ValueError):
        sitefactory.save_spec("pizzeria-mario", "non json")
    with pytest.raises(ValueError):
        sitefactory.save_spec("pizzeria-mario", {"brand": {}, "sections": []})
    with pytest.raises(ValueError):
        sitefactory.save_spec("pizzeria-mario", dict(SPEC, brand={"name": "x" * 130_000}))
    assert not (lab / "sites").exists()
    r = sitefactory.save_spec("pizzeria-mario", json.dumps(SPEC))
    assert r["slug"] == "pizzeria-mario" and r["sezioni"] == 9 and r["marca"] == "Da Mario" and r["ruoli"][0] == "attenzione" and sitefactory.load_spec("pizzeria-mario")["repo_name"] == "pizzeria-mario"


def test_build_needs_a_spec_respects_the_daily_cap_and_gives_a_verdict():
    assert "manca lo spec" in sitefactory.build("pizzeria-mario", fake())["errore"]
    sitefactory.save_spec("pizzeria-mario", SPEC)
    calls = []
    rep = sitefactory.build("pizzeria-mario", fake(calls=calls))
    assert rep["verdetto"].startswith("pronto") and calls[0][0] == "build" and sitefactory.builds_today() == 1
    bad = sitefactory.build("pizzeria-mario", fake({**OK_REPORT, "errori_js": ["TypeError"]}))
    assert bad["verdetto"].startswith("bloccato") and sitefactory.report("pizzeria-mario")["verdetto"].startswith("bloccato")
    warn = sitefactory.verdict({**OK_REPORT, "immagini": {"totale": 4, "fornitori": [], "segnaposto": 2}})
    assert warn.startswith("da rivedere")
    for _ in range(sitefactory.BUILDS_PER_DAY):
        sitefactory.build("pizzeria-mario", fake())
    assert "tetto" in sitefactory.build("pizzeria-mario", fake())["errore"]
    assert sitefactory.builds_today(time.time() + 2 * 86400) == 0                       # il giorno dopo si riparte


def test_cli_failures_become_errors_not_exceptions(monkeypatch):
    monkeypatch.setenv("HLAB_SITE_CLI", "/non/esiste.py")
    monkeypatch.setenv("HLAB_SITE_PY", "/non/esiste/python")
    assert "errore" in sitefactory.run_cli(["guide"], 5)


def test_site_tools_split_by_role_and_risk():
    assert "site_build" not in site_tools.DESIGN and "site_build" not in site_tools.COPY and "site_spec_save" not in site_tools.COPY
    cw = registry.load_all()["copywriter"]
    assert "mcp__sites__site_copy_save" in tools.tool_names(cw) and "mcp__sites__site_build" not in tools.tool_names(cw) and "mcp__sites__site_spec_save" not in tools.tool_names(cw)
    assert all(tools.risk_of(f"mcp__sites__{n}") in ("read", "reversible") for n in site_tools.NAMES)
    assert tools.risk_of("mcp__sites__qualcosa_di_nuovo") == "external"
    specs = registry.load_all()
    d, c = specs["direttore-creativo"], specs["costruttore-siti"]
    assert "mcp__sites__site_build" not in tools.tool_names(d) and "mcp__sites__site_spec_save" in tools.tool_names(d)
    assert "mcp__sites__site_build" in tools.tool_names(c) and "mcp__sites__site_spec_save" in tools.tool_names(c)   # il costruttore puo' correggere lo spec se e' bloccato
    assert not [n for n in tools.tool_names(specs["deployer"]) if "__sites__" in n] and "mcp__pow_deployer__propose_site_launch" in tools.tool_names(specs["deployer"])
    ts = {t.name: t for t in site_tools.make_tools(fake())}
    r = asyncio.run(ts["site_spec_save"].handler({"slug": "pizzeria-mario", "spec": json.dumps(SPEC)}))
    assert not r.get("is_error")
    assert asyncio.run(ts["site_spec_save"].handler({"slug": "../x", "spec": "{}"})).get("is_error")


def test_publish_is_proposed_by_the_agent_but_only_goes_online_after_approval():
    ev, v = Events(), Vault()
    spec = registry.load_all()["deployer"]
    t = {x.name: x for x in powers_tools.make_tools(spec, ev, v)}
    propose = lambda: asyncio.run(t["propose_site_launch"].handler({"slug": "pizzeria-mario", "title": "Sito Da Mario"}))   # noqa: E731
    assert propose().get("is_error")                                                       # non costruito
    sitefactory.save_spec("pizzeria-mario", SPEC)
    sitefactory.build("pizzeria-mario", fake({**OK_REPORT, "errori_js": ["boom"]}))
    assert propose().get("is_error")                                                       # bloccato
    sitefactory.build("pizzeria-mario", fake())
    r = propose()
    assert not r.get("is_error") and "in attesa" in r["content"][0]["text"]
    ch = powers.listing(ev)[0]
    assert ch["kind"] == "site_publish" and ch["status"] == "proposed"
    calls = []
    orig = sitefactory.run_cli
    sitefactory.run_cli = fake(calls=calls)
    try:
        assert not [c for c in calls if c[0] == "publish"]                                  # la proposta non ha pubblicato
        res = Powers(ev, v).approve(ch["id"])
    finally:
        sitefactory.run_cli = orig
    assert res["anteprima"].startswith("https://") and powers.get(ev, ch["id"])["status"] == "published"
    with pytest.raises(ValueError):
        Powers(ev, v).approve(ch["id"])                                                     # una sola volta


def test_funnel_skeleton_is_mandatory_ordered_and_extras_must_be_justified():
    ok = json.loads(json.dumps(SPEC))
    assert sitefactory.check_funnel(ok) == []
    no_faq = dict(ok, sections=[x for x in ok["sections"] if x["role"] != "faq"])
    assert "faq" in sitefactory.check_funnel(no_faq)[0]
    swapped = dict(ok, sections=[ok["sections"][i] for i in (0, 2, 1, 3, 4, 5, 6, 7, 8)])
    assert any("ordine" in e for e in sitefactory.check_funnel(swapped))
    bad_type = json.loads(json.dumps(SPEC)); bad_type["sections"][3]["type"] = "split"
    assert any("offerta" in e and "offer" in e for e in sitefactory.check_funnel(bad_type))
    no_why = json.loads(json.dumps(SPEC)); no_why["sections"][6].pop("extra_why")
    assert any("extra_why" in e for e in sitefactory.check_funnel(no_why))
    many = json.loads(json.dumps(SPEC)); many["sections"][6:6] = [sec("services", "extra", extra_why="serve davvero qui")] * 4
    assert any("troppe" in e for e in sitefactory.check_funnel(many))
    late = json.loads(json.dumps(SPEC)); late["sections"].insert(8, sec("services", "extra", extra_why="dopo la cta no"))
    assert any("tra 'attenzione' e 'cta'" in e for e in sitefactory.check_funnel(late))
    norole = json.loads(json.dumps(SPEC)); norole["sections"][1].pop("role")
    assert any("senza role" in e for e in sitefactory.check_funnel(norole))
    with pytest.raises(ValueError, match="scheletro"):
        sitefactory.save_spec("pizzeria-mario", no_faq)


def test_build_is_blocked_when_a_skeleton_section_is_missing_from_the_built_page():
    sitefactory.save_spec("pizzeria-mario", SPEC)
    rep = sitefactory.build("pizzeria-mario", fake({**OK_REPORT, "ruoli": ["attenzione", "problema", "cta", "faq"]}))
    assert rep["verdetto"].startswith("bloccato") and "soluzione" in rep["verdetto"]


def test_copywriter_rewrites_only_text_and_never_structure():
    sitefactory.save_spec("pizzeria-mario", SPEC)
    copy = {"attenzione": {"title": "La *pizza* di Mario", "type": "cta", "image": "x", "cta": {"label": "Prenota ora", "href": "http://evil"}},
            "offerta": {"points": ["Impasto 48 ore", "Forno a legna"], "price": "da 7 euro"},
            "extra-1": {"text": "Il menu"}, "faq": {"items": [{"q": "Si prenota?", "a": "Si."}] * 5},
            "usp": {"items": [{"title": "nuova voce"}]}, "meta": {"seo_description": "Pizzeria a Bologna", "nav_cta_label": "Prenota"}, "inesistente": {"title": "x"}}
    r = sitefactory.merge_copy("pizzeria-mario", json.dumps(copy))
    sp = sitefactory.load_spec("pizzeria-mario")
    hero = sp["sections"][0]
    assert hero["title"] == "La *pizza* di Mario" and hero["type"] == "hero_shader" and "image" not in hero and hero["cta"] == {"href": "#contatti", "label": "Prenota ora"}
    assert sp["sections"][3]["price"] == "da 7 euro" and sp["sections"][6]["text"] == "Il menu" and len(sp["sections"][8]["items"]) == 5
    assert sp["seo"]["description"] == "Pizzeria a Bologna" and sp["nav_cta"]["label"] == "Prenota"
    assert "inesistente" in r["ignorati"] and "hero.type" not in r["applicati"] and any("image" in i for i in r["ignorati"])
    assert not sitefactory.check_funnel(sp)                                                      # dopo il copy lo scheletro e' intatto
    with pytest.raises(ValueError):
        sitefactory.merge_copy("altro-sito", {"a": {}})
    with pytest.raises(ValueError):
        sitefactory.merge_copy("pizzeria-mario", "non json")


def test_existing_site_is_modified_in_place_never_recreated():
    assert sitefactory.list_published(fake())["siti"][0]["repo"] == "bistrot-centrale"
    r = sitefactory.open_site("bistrot-centrale", fake())
    assert r["repo"] == "bistrot-centrale" and sitefactory.is_edit("bistrot-centrale") and sitefactory.load_spec("bistrot-centrale")["brand"]["name"] == "Bistrot Centrale"
    wrong = json.loads(json.dumps(SPEC)); wrong["repo_name"] = "un-altro-sito"
    # un sito pubblicato senza scheletro: la modifica che lo adegua deve avere i ruoli
    res = sitefactory.save_spec("bistrot-centrale", wrong)
    assert res["modifica"] is True and res["repo"] == "bistrot-centrale" and sitefactory.load_spec("bistrot-centrale")["repo_name"] == "bistrot-centrale"      # il nome del repo non si cambia
    incomplete = {"brand": {"name": "Bistrot Centrale"}, "sections": [{"type": "hero_shader", "role": "attenzione", "title": "x"}, {"type": "cta", "role": "cta", "title": "c"}]}
    with pytest.raises(ValueError, match="ritocco"):
        sitefactory.save_spec("bistrot-centrale", incomplete)                                                     # adeguare = scheletro completo
    r2 = sitefactory.save_spec("bistrot-centrale", {"brand": {"name": "Bistrot Centrale"}, "sections": [{"type": "hero_shader", "title": "Nuovo titolo"}, {"type": "cta", "title": "c"}]}, ritocco=True)
    assert "avviso" in r2 and r2["repo"] == "bistrot-centrale"
    rep = sitefactory.build("bistrot-centrale", fake({**OK_REPORT, "ruoli": ["attenzione", "cta"], "modifica": True}))
    assert rep["verdetto"].startswith("pronto")                                                          # un ritocco non e' bloccato dalla mancanza dello scheletro
    sitefactory.save_spec("bistrot-centrale", SPEC)                                                               # salvataggio normale: il flag ritocco sparisce
    assert sitefactory.build("bistrot-centrale", fake({**OK_REPORT, "ruoli": ["attenzione", "cta"]}))["verdetto"].startswith("bloccato")
    with pytest.raises(ValueError, match="solo per un sito gia' pubblicato"):
        sitefactory.save_spec("sito-nuovo-xx", SPEC, ritocco=True)                                        # il ritocco non esiste per i siti nuovi
    sitefactory.save_spec("lavoro-nuovo", SPEC)
    with pytest.raises(ValueError, match="gia' un lavoro nuovo"):
        sitefactory.open_site("lavoro-nuovo", fake())                                                     # non si sovrascrive un lavoro nuovo con un sito pubblicato
    assert [x["modifica_di_sito_pubblicato"] for x in sitefactory.listing() if x["slug"] == "bistrot-centrale"] == [True]


def test_copywriter_can_address_old_sites_without_roles_by_position():
    sitefactory.open_site("bistrot-centrale", fake())
    r = sitefactory.merge_copy("bistrot-centrale", {"sezione-1": {"title": "Il tuo *aperitivo* come si deve"}, "sezione-9": {"title": "x"}})
    assert sitefactory.load_spec("bistrot-centrale")["sections"][0]["title"].startswith("Il tuo") and "sezione-9" in r["ignorati"]


def test_publishing_a_modification_updates_the_same_site_and_new_tools_are_assigned_to_the_right_agents():
    from hlab import tools, registry
    sp = registry.load_all()
    assert "mcp__sites__site_open" in tools.tool_names(sp["direttore-creativo"]) and "mcp__sites__site_open" not in tools.tool_names(sp["copywriter"])
    assert "mcp__sites__site_existing_list" in tools.tool_names(sp["copywriter"]) and "mcp__sites__site_open" not in tools.tool_names(sp["costruttore-siti"])
    assert tools.risk_of("mcp__sites__site_open") == "reversible" and tools.risk_of("mcp__sites__site_existing_list") == "read"
    assert "site_existing_list" in sitefactory.guide(fake())["nuovo_sito_o_modifica"]
    ev, v = Events(), Vault()
    sitefactory.open_site("bistrot-centrale", fake())
    sitefactory.save_spec("bistrot-centrale", SPEC)
    sitefactory.build("bistrot-centrale", fake())
    cid = powers.propose(ev, "deployer", "site_publish", "bistrot-centrale", "Modifica sito Bistrot Centrale: scheletro", {})
    assert json.loads(sqlite_payload(ev, cid))["modifica"] is True


def sqlite_payload(ev, cid):
    with ev._conn() as c:
        return c.execute("SELECT payload FROM changes WHERE id=?", (cid,)).fetchone()[0]


def test_fake_accents_are_rejected_in_texts_but_not_in_english_prompts():
    ok = json.loads(json.dumps(SPEC)); ok["sections"][0]["image"] = "barista's hands, dogs' bowl"; ok["sections"][1]["text"] = "Dell'acqua e l'olio, perché sì."
    assert sitefactory.fake_accents(ok) == []
    bad = json.loads(json.dumps(SPEC)); bad["sections"][1]["text"] = "Il caffe' e' pronto"
    assert sitefactory.fake_accents(bad)
    with pytest.raises(ValueError, match="accentate"):
        sitefactory.save_spec("pizzeria-mario", bad)
    sitefactory.save_spec("pizzeria-mario", SPEC)
    with pytest.raises(ValueError, match="accentate"):
        sitefactory.merge_copy("pizzeria-mario", {"problema": {"text": "Una citta' intera"}})
    assert "citta" not in json.dumps(sitefactory.load_spec("pizzeria-mario"))                              # il copy rifiutato non e' stato salvato


def test_publish_result_says_whether_github_pages_really_finished():
    ev, v = Events(), Vault()
    sitefactory.save_spec("pizzeria-mario", SPEC)
    sitefactory.build("pizzeria-mario", fake())
    orig = sitefactory.run_cli
    sitefactory.run_cli = lambda args, timeout: {"url": "https://x.github.io/pizzeria-mario/", "repo": "pizzeria-mario", "file": 9, "pages": "building"}
    try:
        cid = powers.propose(ev, "deployer", "site_publish", "pizzeria-mario", "Sito Da Mario", {})
        res = Powers(ev, v).approve(cid)
    finally:
        sitefactory.run_cli = orig
    assert res["pages"] == "building" and res["pubblicato"] is False and powers.get(ev, cid)["status"] == "published"


def test_every_declared_site_tool_is_really_registered_on_the_server():
    built = {t.name for t in site_tools.make_tools(fake())}
    assert built == set(site_tools.NAMES), (built ^ set(site_tools.NAMES))           # un nome nelle liste senza lo strumento registrato = l'agente non lo vedrebbe mai


def test_factory_job_runs_the_four_agents_in_order_and_asks_for_the_final_approval(monkeypatch):
    import types
    from hlab import workflows
    sent, order = [], []
    monkeypatch.setattr(workflows, "_tell", lambda text, buttons=None: sent.append((text, buttons)))
    sitefactory.write_job("pizzeria-mario", {"brief": "sito per una pizzeria", "mode": "nuovo", "context": "MATERIALE: tel 0511234567; client:1 forno a legna"})
    assert "client:1" in sitefactory.read_context("pizzeria-mario")
    ev = Events()

    async def fake_run(prompt, on_text=None, budget=None):
        who = next(a for a in ("direttore-creativo", "art-director", "copywriter", "costruttore-siti", "deployer") if a in prompt)
        order.append(who)
        if who == "direttore-creativo":
            sitefactory.save_spec("pizzeria-mario", SPEC)
        elif who == "art-director":
            sitefactory.merge_art("pizzeria-mario", {"art_direction": "warm tungsten light, 35mm grain, terracotta and cream palette", "immagini": {"sezione-1": {"image": "wood-fired pizza oven glowing, close-up, warm tungsten light, shallow depth of field, 35mm grain"}}, "video": {"prompt": "Close-up of a wood-fired oven, slow push-in, glowing embers, 6s, ambient crackle"}})
        elif who == "copywriter":
            sitefactory.merge_copy("pizzeria-mario", {"attenzione": {"title": "La *pizza* vera"}})
        elif who == "costruttore-siti":
            sitefactory.build("pizzeria-mario", fake())
        else:
            powers.propose(ev, "deployer", "site_publish", "pizzeria-mario", "Sito pizzeria-mario: prima versione", {})
        return {"subtype": "success", "cost_usd": 0.0}
    monkeypatch.setattr("hlab.runner.run", fake_run)

    async def step(name, fn):
        r = fn()
        return await r if hasattr(r, "__await__") else r
    out = asyncio.run(workflows.site_factory(types.SimpleNamespace(step=step), {"slug": "pizzeria-mario"}))
    assert order == ["direttore-creativo", "art-director", "copywriter", "costruttore-siti", "deployer"] and out["esito"] == "in_attesa_di_approvazione"
    text, buttons = sent[-1]
    assert "pronto da pubblicare" in text and [b[1] for b in buttons] == [f"hc:ok:{out['change']}", f"hc:no:{out['change']}"]
    assert any("Direttore creativo ha finito: 9 sezioni" in t for t, _ in sent)
    assert any("Video hero" in t and "slow push-in" in t and "pizzeria-mario" in t for t, _ in sent), sent                  # il prompt video arriva su Telegram con le istruzioni
    order.clear(); sent.clear()
    sitefactory.remove("pizzeria-mario")
    sitefactory.write_job("pizzeria-mario", {"brief": "x", "mode": "nuovo"})

    async def lazy_run(prompt, on_text=None, budget=None):
        order.append("x")
        return {"subtype": "success"}
    monkeypatch.setattr("hlab.runner.run", lazy_run)
    out2 = asyncio.run(workflows.site_factory(types.SimpleNamespace(step=step), {"slug": "pizzeria-mario"}))
    assert out2["esito"] == "fermo" and order == ["x"] and any("non e' riuscito" in t for t, _ in sent)


def test_spec_without_a_brand_name_is_rejected():
    nameless = json.loads(json.dumps(SPEC)); nameless["brand"] = {"name": " "}
    assert any("nome della marca" in e for e in sitefactory.check_funnel(nameless))
    with pytest.raises(ValueError, match="nome della marca"):
        sitefactory.save_spec("pizzeria-mario", nameless)


def test_art_director_changes_only_image_prompts_and_the_style_line():
    spec = json.loads(json.dumps(SPEC))
    spec["sections"][0]["image"] = "old prompt for the hero image with a very long text"
    spec["sections"][6]["items"] = [{"title": "Menu", "image": "old gallery prompt number one in english text"}]
    sitefactory.save_spec("pizzeria-mario", spec)
    art = {"art_direction": "soft window daylight, 35mm grain, sage green and sand", "bibbia": {"luce": "soft window daylight"},
           "immagini": {"attenzione": {"image": "steam rising from a hand-thrown ceramic cup, 50mm f/2, soft window daylight, sage and sand palette, 35mm grain"},
                        "extra-1": {"items": [{"image": "top-down wooden table with fresh bread, soft daylight, muted sage and sand palette"}, {"image": "una seconda voce che non esiste ma e' abbastanza lunga da passare il controllo"}]},
                        "problema": {"image": "un prompt per una sezione senza immagini ma abbastanza lungo da essere valido"},
                        "inesistente": {"image": "x" * 40}, "usp": {"image": "wooden workbench with tools, muted #1f5c63 accent, soft daylight, 35mm grain"}, "faq": {"image": "ftp://non-un-prompt https://example.com/x.jpg abbastanza lungo da superare il minimo"}},
           "video": {"hero_query": "pouring espresso", "prompt": "Close-up of espresso pouring, slow push-in, soft window daylight, 6s, ambient sound"},
           "shotlist": ["1. Close-up crema che scende", "2. Wide del bancone"], "titolo": "ignorato"}
    r = sitefactory.merge_art("pizzeria-mario", json.dumps(art))
    sp = sitefactory.load_spec("pizzeria-mario")
    assert sp["brand"]["art_direction"].startswith("soft window daylight") and sp["sections"][0]["image"].startswith("steam rising")
    assert sp["sections"][6]["items"][0]["image"].startswith("top-down wooden") and len(sp["sections"][6]["items"]) == 1                # nessuna voce nuova, il testo non si tocca
    assert sp["sections"][6]["items"][0]["title"] == "Menu" and sp["sections"][0]["type"] == "hero_shader" and sp["sections"][1]["text"] == "p"
    assert set(r["applicati"]) == {"art_direction", "attenzione.image", "extra-1.items[0].image"} and any("inesistente" in i for i in r["ignorati"])
    assert any("usp.image" in i for i in r["ignorati"])                                                      # un codice HEX nel prompt viene rifiutato
    assert any("problema.image" in i for i in r["ignorati"]) and any("video.hero_query" in i for i in r["ignorati"]) and any("faq.image" in i for i in r["ignorati"])
    md = (sitefactory.workdir("pizzeria-mario") / "art.md").read_text(encoding="utf-8")
    assert "Prompt video" in md and "Shot list" in md and "luce" in md
    assert not sitefactory.check_funnel(sp)
    with pytest.raises(ValueError):
        sitefactory.merge_art("pizzeria-mario", "non json")


def _pixabay(hits, calls):
    import httpx

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, json={"totalHits": len(hits), "hits": hits})
    return httpx.MockTransport(handler)


def _hit(i, dur=15, w=1920, h=1080, mb=3, tags="coffee, espresso, pouring"):
    v = {"url": f"https://cdn.pixabay.com/v/{i}.mp4", "width": w, "height": h, "size": mb * 1_000_000, "thumbnail": "t.jpg"}
    return {"id": i, "duration": dur, "tags": tags, "user": "autore", "pageURL": f"https://pixabay.com/videos/{i}/", "videos": {"medium": v, "small": {**v, "width": 1280 if w >= h else 720, "height": 720 if w >= h else 1280}}}


def test_stock_videos_ranks_suitable_hero_clips_and_caches_for_24_hours(monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "k")
    calls = []
    hits = [_hit(1, dur=3), _hit(2, tags="dog"), _hit(3, w=1080, h=1920), _hit(4, mb=30), _hit(5)]
    t = _pixabay(hits, calls)
    r = sitefactory.stock_videos("espresso pouring", transport=t)
    ids = [v["id"] for v in r["video"]]
    assert ids[0] == 5 and 4 not in ids                                  # il migliore: orizzontale, 6-40 s, pertinente; il clip da 30 MB e' escluso
    assert next(v for v in r["video"] if v["id"] == 3)["adatto_hero"] is False and next(v for v in r["video"] if v["id"] == 1)["adatto_hero"] is False
    assert "marchi" in r["regole"] and len(calls) == 1 and "key=k" in calls[0] and "video_type=film" in calls[0]
    sitefactory.stock_videos("Espresso Pouring", transport=t)             # stessa ricerca = nessuna nuova richiesta (obbligo dell'API)
    assert len(calls) == 1


def test_stock_videos_refuses_without_key_or_with_a_useless_query(monkeypatch):
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    assert "PIXABAY_API_KEY" in sitefactory.stock_videos("coffee")["errore"]
    monkeypatch.setenv("PIXABAY_API_KEY", "k")
    assert "almeno 3" in sitefactory.stock_videos("  !! ")["errore"]


def test_art_director_hero_id_only_applies_to_a_hero_video():
    spec = json.loads(json.dumps(SPEC))
    sitefactory.save_spec("pizzeria-mario", spec)
    r = sitefactory.merge_art("pizzeria-mario", json.dumps({"video": {"hero_id": "262184"}}))
    assert any("hero_id" in i for i in r["ignorati"]) and "video_id" not in sitefactory.load_spec("pizzeria-mario")["sections"][0]    # hero_shader: scelta del direttore
    spec["sections"][0] = sec("hero_video", "attenzione", title="Pizza vera", subtitle="x", video_query="pizza oven")
    sitefactory.save_spec("pizzeria-mario", spec)
    r = sitefactory.merge_art("pizzeria-mario", json.dumps({"video": {"hero_id": "id 262184; drop table"}}))
    assert "video.hero_id" in r["applicati"] and sitefactory.load_spec("pizzeria-mario")["sections"][0]["video_id"] == "262184"        # solo cifre


def test_stock_video_tool_is_registered_for_the_art_director():
    assert "site_stock_video" in site_tools.NAMES and "site_stock_video" in site_tools.ART


def test_published_sites_join_the_uptime_monitor_automatically(monkeypatch):
    from hlab import uptime
    sitefactory.save_spec("pizzeria-mario", json.loads(json.dumps(SPEC)))
    sitefactory.build("pizzeria-mario", runner=fake())
    sitefactory.publish("pizzeria-mario", runner=lambda a, t: {"url": "https://hermessscoo.github.io/pizzeria-mario/"} if a[0] == "publish" else fake()(a, t))
    t = uptime.targets(db=type("D", (), {"query": lambda self, q: []})(), quotas={"uptime": {}})
    assert t == {"Da Mario": "https://hermessscoo.github.io/pizzeria-mario/"}
    assert not sitefactory.register_site("x", "javascript:alert(1)")                       # solo http(s)


def test_every_build_writes_its_costs_to_the_site_ledger_without_ever_blocking(monkeypatch, tmp_path):
    sitefactory.save_spec("pizzeria-mario", json.loads(json.dumps(SPEC)))
    usage = iter([1.0, 1.101])                                                              # contatore Gemini del bot prima e dopo: una immagine hero da 2K
    monkeypatch.setattr(sitefactory, "gemini_usd", lambda: next(usage))
    rep = {**OK_REPORT, "immagini": {"totale": 4, "fornitori": ["gemini", "cloudflare"], "segnaposto": 0}}
    sitefactory.build("pizzeria-mario", runner=fake(rep))
    rows = [json.loads(l) for l in (config.DATA_DIR / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["site"], r["kind"], r["usd"]) for r in rows] == [("Da Mario", "immagini", 0.101), ("Da Mario", "modelli", 0.0)]
    assert "gemini, cloudflare" in rows[0]["note"]
    monkeypatch.setattr(sitefactory, "gemini_usd", lambda: 0.0)
    from hlab import ledger
    monkeypatch.setattr(ledger, "add", lambda *a, **k: (_ for _ in ()).throw(OSError("disco pieno")))
    assert "errore" not in sitefactory.build("pizzeria-mario", runner=fake(rep))                # un registro rotto non ferma la costruzione
