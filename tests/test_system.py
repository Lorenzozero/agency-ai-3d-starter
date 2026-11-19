"""Mappa dei servizi, sentinella e modifiche guardate ai pool."""
import json
import time
from pathlib import Path

import httpx
import pytest
import yaml

from hlab import config, router, sentinel, services
from hlab.tools import system_tools

SHIPPED = Path(__file__).resolve().parents[1] / "models.yaml"


@pytest.fixture(autouse=True)
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "LAB_HOME", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "models.yaml").write_text(SHIPPED.read_text(encoding="utf-8"), encoding="utf-8")
    for k in ("OPENROUTER_API_KEY", "GEMINI_FREE_API_KEY", "NVIDIA_API_KEY", "GROQ_API_KEY", "CF_API_TOKEN", "GITHUB_MODELS_TOKEN"):
        monkeypatch.setenv(k, "chiave-di-prova-" + k)
    monkeypatch.setattr(services, "_cache", {})
    return tmp_path


def status_with(*items):
    groups = [{"id": "x", "label": "X", "items": list(items)}]
    return {"updated": int(time.time()), "counts": {}, "overall": "ok", "groups": groups}


# ------------------------------------------------------------------ services
def test_percentages_and_levels_only_come_from_real_numbers():
    assert services.pct(45, 50) == 90.0 and services.pct(None, 50) is None and services.pct(5, 0) is None
    assert [services.level(p) for p in (None, 10, 75, 94.9, 95, 120)] == ["ok", "ok", "warn", "warn", "down", "down"]
    assert services.item("a", "A", "g", "p", used=96, limit=100)["status"] == "down"
    assert services.item("a", "A", "g", "p", status="off", used=96, limit=100)["status"] == "off"                  # uno stato deciso a mano non si cambia


def test_a_failing_probe_becomes_down_without_leaking_the_error_text():
    def boom():
        raise RuntimeError("chiave segreta sk-or-v1-xxx nel messaggio")
    r = services.safe(boom, "x", "X", "g", "p")[0]
    assert r["status"] == "down" and "sk-or" not in json.dumps(r) and "RuntimeError" in r["detail"] and r["measured"] is False


def test_documented_items_are_never_presented_as_measured():
    assert all(d["measured"] is False and d["status"] == "info" for d in services.DOCUMENTED)
    assert "Always Free" in next(d for d in services.DOCUMENTED if d["id"] == "oracle")["plan"]


def test_router_stats_count_only_today_and_split_ok_from_errors(lab):
    now = time.time()
    rows = [{"ts": now, "model": "a", "tier": "groq", "ok": True, "est_tokens": 1000, "cost": 0}, {"ts": now, "model": "a", "tier": "groq", "ok": False, "est_tokens": 500, "cost": 0},
            {"ts": now - 3 * 86400, "model": "b", "tier": "groq", "ok": True, "est_tokens": 9, "cost": 0}, {"ts": now, "model": "p", "tier": "paid", "ok": True, "est_tokens": 1, "cost": 0.02}]
    (lab / "data" / "router.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\nrotta{\n", encoding="utf-8")
    s = services.tier_stats(services.read_router_rows(), services.day_of(now))
    assert s["groq"]["n"] == 2 and s["groq"]["ok"] == 1 and s["groq"]["fail"] == 1 and s["groq"]["tokens"] == 1000 and round(s["paid"]["usd"], 2) == 0.02
    h = services.history(now)
    assert sum(d["n"] for d in h["days"]) == 4 and len(h["days"]) == 2


def test_router_probe_flags_paid_spend_while_paid_is_disabled(lab, monkeypatch):
    (lab / "data" / "router.jsonl").write_text(json.dumps({"ts": time.time(), "model": "p", "tier": "paid", "ok": True, "est_tokens": 1, "cost": 0.5}) + "\n", encoding="utf-8")
    kids = services.probe_router()[0]["children"]
    guard = next(k for k in kids if k["id"] == "router-paid-guard")
    assert guard["status"] == "down" and "ATTENZIONE" in guard["detail"]
    alerts = sentinel.find_alerts(status_with(guard))
    assert alerts[0]["key"] == "paid-spend" and alerts[0]["level"] == "crit"


def test_collect_groups_everything_and_writes_the_shared_file(monkeypatch, lab):
    fake = {p: (lambda p=p: [services.item(p, p, "modelli", "gratuito", used=10, limit=100, unit="u")], p, "modelli", "gratuito") for p in services.PROBES}
    monkeypatch.setattr(services, "PROBES", fake)
    st = services.collect(force=True)
    assert st["counts"]["ok"] >= len(fake) and any(g["id"] == "modelli" for g in st["groups"])
    assert services.load_status()["updated"] == st["updated"]
    assert services.load_status(max_age_s=-1) is None                                                            # troppo vecchio: non si usa


# ------------------------------------------------------------------ sentinella: avvisi
def test_alerts_are_said_once_reminded_late_and_closed_when_resolved():
    down = [{"key": "k:down", "level": "crit", "text": "🔴 X giu'"}]
    msgs, st = sentinel.diff_alerts(down, {}, 1000)
    assert msgs == ["🔴 X giu'"]
    msgs, st = sentinel.diff_alerts(down, st, 1500)                                                              # stesso problema: zitta
    assert msgs == []
    msgs, st = sentinel.diff_alerts(down, st, 1000 + sentinel.REMIND_S + 5)                                      # dopo ore: promemoria
    assert msgs and msgs[0].startswith("⏰")
    msgs, st = sentinel.diff_alerts([], st, 99999)
    assert msgs == ["✅ Risolto: X giu'"] and st["alerts"] == {}


def test_at_night_only_critical_alerts_are_sent_and_warnings_wait_for_the_morning():
    both = [{"key": "a", "level": "warn", "text": "🟠 lento"}, {"key": "b", "level": "crit", "text": "🔴 giu'"}]
    msgs, st = sentinel.diff_alerts(both, {}, 1000, quiet=True)
    assert msgs == ["🔴 giu'"] and st["alerts"]["a"]["last_said"] == 0
    msgs, st = sentinel.diff_alerts(both, st, 2000, quiet=False)
    assert msgs == ["🟠 lento"]


def test_free_quota_near_the_limit_never_sends_a_message_but_a_full_disk_does():
    groq = services.item("g", "Groq", "modelli", "gratuito", used=96, limit=100, unit="richieste")             # 96%: stato «giu'» ma e' un limite previsto di un piano gratuito
    gemini = services.item("h", "Gemini", "modelli", "gratuito", measured=False)
    disk = services.item("server-disk", "Server: disco", "infrastruttura", "45 GB", used=44, limit=45, unit="GB")
    out = sentinel.find_alerts(status_with(groq, gemini, disk))
    assert [a["key"] for a in out] == ["server-disk:down"] and out[0]["persist"] == 3


# ------------------------------------------------------------------ sentinella: riparazioni
def units(**states):
    return status_with(*[services.item(f"unit-{n}", n, "infrastruttura", "systemd", status=s) for n, s in states.items()])


def test_heal_restarts_an_allowed_service_only_after_two_failed_checks_and_at_most_twice_an_hour():
    calls = []
    st = {}
    m, st = sentinel.heal(units(**{"hlab-router": "down"}), st, 1000, restart=lambda u: calls.append(u) or True)
    assert calls == [] and st["fails"]["hlab-router"] == 1                                                        # un solo controllo fallito non basta
    m, st = sentinel.heal(units(**{"hlab-router": "down"}), st, 1600, restart=lambda u: calls.append(u) or True)
    assert calls == ["hlab-router"] and "riavviato" in m[0]
    for t in (2200, 2800, 3400, 4000):
        m, st = sentinel.heal(units(**{"hlab-router": "down"}), st, t, restart=lambda u: calls.append(u) or True)
    assert calls.count("hlab-router") == 2                                                                        # tetto: 2 l'ora
    other = []
    st2 = {"fails": {"tailscaled": 5}}
    sentinel.heal(units(tailscaled="down"), st2, 1000, restart=lambda u: other.append(u) or True)
    assert other == []                                                                                            # fuori dall'elenco fisso: mai


def test_cycle_sends_one_message_and_survives_telegram_being_down(lab):
    guard = services.item("router-paid-guard", "Protezione", "router", "regola", status="down", detail="ATTENZIONE: 0.5000 $ spesi oggi")
    bad = status_with(guard)
    bad["counts"] = {"ok": 0, "warn": 0, "down": 1, "off": 0, "info": 0}
    said = []
    r = sentinel.cycle(now=5000, send=lambda t: said.append(t) or 1, collect=lambda: bad)
    assert r["alerts"] == 1 and said and "SOLDI" in said[0]                                                      # i soldi spesi: subito, al primo controllo

    def broken(t):
        raise RuntimeError("telegram giu'")
    sentinel.cycle(now=9000, send=broken, collect=lambda: bad)                                                    # non solleva
    dry = sentinel.cycle(now=9500, send=lambda t: said.append("NO"), collect=lambda: bad, dry=True)
    assert "NO" not in said and dry["inviati"] == 0


def crm(status, **kw):
    s = status_with(services.item("crm", "CRM", "dati", "esterno", status=status, **kw))
    s["counts"] = {"ok": 0, "warn": 0, "down": 1 if status == "down" else 0, "off": 0, "info": 0}
    return s


def test_a_problem_must_last_three_checks_before_it_is_worth_a_message(lab):
    said = []
    send = lambda t: said.append(t) or 1                                                                           # noqa: E731
    sentinel.cycle(now=1000, send=send, collect=lambda: crm("down", detail="non risponde"))
    sentinel.cycle(now=1600, send=send, collect=lambda: crm("down", detail="non risponde"))
    assert said == []                                                                                              # due controlli: ancora silenzio
    sentinel.cycle(now=2200, send=send, collect=lambda: crm("down", detail="non risponde"))
    assert len(said) == 1 and "CRM" in said[0]                                                                     # al terzo: un messaggio solo
    sentinel.cycle(now=2800, send=send, collect=lambda: crm("down", detail="non risponde"))
    assert len(said) == 1                                                                                          # poi zitta
    sentinel.cycle(now=3400, send=send, collect=lambda: crm("ok"))
    assert len(said) == 2 and "Risolto" in said[1]


def test_a_problem_that_fixes_itself_before_the_threshold_leaves_no_trace_at_all(lab):
    said = []
    send = lambda t: said.append(t) or 1                                                                           # noqa: E731
    for t0, st in ((1000, "down"), (1600, "down"), (2200, "ok"), (2800, "down"), (3400, "ok")):                    # il caso Tavily: giu', giu', a posto, giu', a posto
        sentinel.cycle(now=t0, send=send, collect=lambda st=st: crm(st))
    assert said == []                                                                                              # nemmeno un «risolto»


def test_a_provider_timeout_is_a_warning_that_never_reaches_telegram(monkeypatch):
    calls = []

    def slow():
        calls.append(1)
        raise httpx.ReadTimeout("lento")
    monkeypatch.setattr(services.time, "sleep", lambda s: None)
    r = services.safe(slow, "tavily", "Tavily", "ricerca", "gratuito")[0]
    assert len(calls) == 2 and r["status"] == "warn" and r["transient"] and "riprover" in r["detail"]           # si riprova una volta, poi e' solo un avviso nella pagina
    assert sentinel.find_alerts(status_with(r)) == []
    r2 = services.safe(lambda: (_ for _ in ()).throw(RuntimeError("x")), "a", "A", "g", "p")[0]
    assert r2["status"] == "down" and not r2.get("transient")                                                     # un errore vero e' un'altra cosa


def test_only_the_serious_things_can_ever_become_a_message():
    items = [services.item("openrouter", "OpenRouter", "modelli", "g", status="warn", detail="x"), services.item("router-groq", "Groq", "router", "g", status="warn", detail="chiuso per limite"),
             services.item("router-nvidia", "NVIDIA", "router", "g", status="warn", detail="60% di errori"), services.item("gemini", "Gemini", "modelli", "g", status="down", detail="risposta 403"),
             services.item("unit-hermes", "hermes", "infrastruttura", "systemd", status="down", detail="inactive"), services.item("backup", "Backup", "infrastruttura", "x", status="warn", detail="vecchio")]
    keys = {a["key"]: a["persist"] for a in sentinel.find_alerts(status_with(*items))}
    assert keys == {"gemini:down": 3, "unit-hermes:down": 2, "backup:warn": 1}                                    # limiti, livelli chiusi e percentuali di errore: mai


# ------------------------------------------------------------------ documentazione e ricerca
def test_docs_are_limited_to_the_fixed_list_of_providers():
    ok = ["https://openrouter.ai/docs", "https://console.groq.com/docs/rate-limits", "https://ai.google.dev/gemini-api/docs"]
    bad = ["http://openrouter.ai/docs", "https://evilopenrouter.ai/x", "https://openrouter.ai.evil.com/x", "https://example.com", "ftp://openrouter.ai", "https://user@evil.com/openrouter.ai", "file:///etc/passwd"]
    assert all(sentinel.doc_allowed(u) for u in ok) and not any(sentinel.doc_allowed(u) for u in bad)


def test_docs_fetch_never_follows_a_redirect_out_of_the_list():
    def handler(req):
        if req.url.host == "openrouter.ai":
            return httpx.Response(302, headers={"location": "https://evil.example/steal"})
        return httpx.Response(200, text="non dovrei arrivare qui")
    with pytest.raises(ValueError, match="non ammesso"):
        sentinel.docs_fetch("https://openrouter.ai/docs", transport=httpx.MockTransport(handler))
    r = sentinel.docs_fetch("https://docs.github.com/x", transport=httpx.MockTransport(lambda q: httpx.Response(200, headers={"content-type": "text/html"}, text="<script>x()</script><h1>Limiti</h1><p>5000 richieste</p>")))
    assert r["testo"] == "Limiti 5000 richieste"


def test_research_has_its_own_monthly_cap(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-prova")
    t = httpx.MockTransport(lambda q: httpx.Response(200, json={"results": [{"title": "T", "url": "https://a", "content": "c" * 900}]}))
    for _ in range(sentinel.SEARCH_MAX_MONTH):
        sentinel.research("modelli gratis", transport=t)
    with pytest.raises(ValueError, match="tetto mensile"):
        sentinel.research("ancora", transport=t)


def test_tell_lorenzo_is_capped_per_day():
    sent = []
    for _ in range(6):
        sentinel.tell_lorenzo("ciao", send=lambda t: sent.append(t))
    with pytest.raises(ValueError, match="6 messaggi"):
        sentinel.tell_lorenzo("troppi", send=lambda t: sent.append(t))
    assert len(sent) == 6


# ------------------------------------------------------------------ pool: prove, modifiche, annullamento
def record_pass(provider, model, ts=None, passed=True):
    (config.DATA_DIR / "model_tests.json").write_text(json.dumps({**sentinel.load_tests(), f"{provider}/{model}": {"ts": ts or time.time(), "passed": passed, "provider": provider, "model": model}}), encoding="utf-8")


def pool_models(pool="strong"):
    return [(m.get("provider", "openrouter"), m["model"]) for m in router.load_config(config.LAB_HOME / "models.yaml")["pools"][pool]]


def test_a_model_enters_a_pool_only_after_a_recent_passed_test(monkeypatch):
    seen = []
    ok = lambda: seen.append("canary") or True                                                                    # noqa: E731
    with pytest.raises(ValueError, match="prova superata"):
        sentinel.pool_apply("strong", "nvidia", "x/nuovo", "add", "prova", canary=ok, send=lambda t: None)
    record_pass("nvidia", "x/nuovo", ts=time.time() - 90000)                                                       # prova vecchia di 25 ore
    with pytest.raises(ValueError, match="prova superata"):
        sentinel.pool_apply("strong", "nvidia", "x/nuovo", "add", "prova", canary=ok, send=lambda t: None)
    record_pass("nvidia", "x/nuovo")
    r = sentinel.pool_apply("strong", "nvidia", "x/nuovo", "add", "supera la prova", canary=ok, send=lambda t: None)
    assert r["esito"] == "applicata" and seen == ["canary"] and ("nvidia", "x/nuovo") in pool_models()
    assert (config.LAB_HOME / "models.yaml").read_text(encoding="utf-8") == SHIPPED.read_text(encoding="utf-8")   # models.yaml di Lorenzo intatto


def test_the_new_model_is_placed_with_its_own_provider_not_after_the_paid_tier():
    record_pass("nvidia", "x/nuovo")
    sentinel.pool_apply("strong", "nvidia", "x/nuovo", "add", "ok", canary=lambda: True, send=lambda t: None)
    models = pool_models()
    paid_first = next(i for i, (p, m) in enumerate(models) if router.tier_of({"model": m, "provider": p}, router.load_config(config.LAB_HOME / "models.yaml")["providers"]) == "paid")
    assert models.index(("nvidia", "x/nuovo")) < paid_first


def test_paid_models_are_refused_everywhere():
    record_pass("openrouter", "anthropic/claude-x")
    with pytest.raises(ValueError, match="solo modelli gratuiti"):
        sentinel.pool_apply("strong", "openrouter", "anthropic/claude-x", "add", "meglio", canary=lambda: True, send=lambda t: None)
    with pytest.raises(ValueError, match="solo modelli gratuiti"):
        sentinel.model_test("openrouter", "deepseek/deepseek-v4.1-flash")


def test_at_most_three_changes_per_day_and_a_pool_never_shrinks_below_three(monkeypatch):
    for i in range(sentinel.MAX_CHANGES_DAY):
        record_pass("nvidia", f"x/m{i}")
        sentinel.pool_apply("strong", "nvidia", f"x/m{i}", "add", "ok", canary=lambda: True, send=lambda t: None)
    record_pass("nvidia", "x/extra")
    with pytest.raises(ValueError, match="ultime 24 ore"):
        sentinel.pool_apply("strong", "nvidia", "x/extra", "add", "ok", canary=lambda: True, send=lambda t: None)
    cfg = router.load_config(config.LAB_HOME / "models.yaml")
    cfg["pools"]["tiny"] = [{"model": "a:free"}, {"model": "b:free"}, {"model": "c:free"}]
    monkeypatch.setattr(router, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(sentinel, "changes_today", lambda now=None: 0)
    with pytest.raises(ValueError, match="sotto 3"):
        sentinel.pool_apply("tiny", "openrouter", "a:free", "remove", "va male", canary=lambda: True, send=lambda t: None)


def test_a_failed_verification_restores_the_previous_state_by_itself():
    record_pass("nvidia", "x/nuovo")
    told = []
    r = sentinel.pool_apply("strong", "nvidia", "x/nuovo", "add", "ok", canary=lambda: False, send=told.append)
    assert r["esito"] == "annullata" and ("nvidia", "x/nuovo") not in pool_models() and not router.overlay_path().exists()
    assert any("annullato" in t for t in told) and sentinel.recent_log(5, ("rollback",))


def test_rollback_restores_the_overlay_before_the_last_change():
    record_pass("nvidia", "x/uno")
    record_pass("nvidia", "x/due")
    sentinel.pool_apply("strong", "nvidia", "x/uno", "add", "uno", canary=lambda: True, send=lambda t: None)
    sentinel.pool_apply("strong", "nvidia", "x/due", "add", "due", canary=lambda: True, send=lambda t: None)
    assert ("nvidia", "x/due") in pool_models()
    sentinel.rollback_last(send=lambda t: None)
    assert ("nvidia", "x/due") not in pool_models() and ("nvidia", "x/uno") in pool_models()


def test_removing_a_model_the_sentinel_added_just_drops_it_from_the_overlay():
    record_pass("nvidia", "x/uno")
    sentinel.pool_apply("strong", "nvidia", "x/uno", "add", "uno", canary=lambda: True, send=lambda t: None)
    sentinel.pool_apply("strong", "nvidia", "x/uno", "remove", "va male", canary=lambda: True, send=lambda t: None)
    assert ("nvidia", "x/uno") not in pool_models() and not (sentinel.read_overlay().get("remove") or {}).get("strong")


def test_model_test_requires_all_three_phases(monkeypatch):
    def fake_post(base, key, model, messages, extra=None, timeout=45, **kw):
        if kw.get("tool_choice"):
            return 200, {"choices": [{"message": {"tool_calls": [{"id": "c"}]}}]}, 0.4
        if any(m.get("role") == "tool" for m in messages):
            return 200, {"choices": [{"message": {"content": "Sono 2"}}]}, 0.5
        return 200, {"usage": {"prompt_tokens": 12000}, "choices": [{"message": {"content": "ok"}}]}, 1.2
    monkeypatch.setattr(sentinel, "_post", fake_post)
    r = sentinel.model_test("nvidia", "x/buono")
    assert r["passed"] and r["fasi"]["prompt_lungo"]["token_in"] == 12000 and sentinel.load_tests()["nvidia/x/buono"]["passed"]
    monkeypatch.setattr(sentinel, "_post", lambda *a, **k: (200, {"choices": [{"message": {"content": "niente strumento"}}]}, 0.3))
    assert not sentinel.model_test("nvidia", "x/cieco")["passed"]
    monkeypatch.setattr(sentinel, "_post", lambda *a, **k: (200, {"choices": [{"message": {"tool_calls": [{"id": "c"}], "content": "ok"}}]}, 40.0))
    assert not sentinel.model_test("nvidia", "x/lento")["passed"]                                                  # oltre 30 secondi: non entra


# ------------------------------------------------------------------ router: override e ricarica
def test_override_adds_and_removes_without_touching_the_base_file(lab):
    router.overlay_path().write_text(yaml.safe_dump({"add": {"strong": [{"model": "z/ok", "provider": "nvidia"}]}, "remove": {"strong": [{"model": "google/gemma-4-31b-it:free"}]}}), encoding="utf-8")
    c = router.load_config(lab / "models.yaml")
    ms = [(m.get("provider", "openrouter"), m["model"]) for m in c["pools"]["strong"]]
    assert ("nvidia", "z/ok") in ms and ("openrouter", "google/gemma-4-31b-it:free") not in ms
    router.overlay_path().write_text(yaml.safe_dump({"add": {"strong": [{"model": "z/x", "provider": "inesistente"}], "nopool": [{"model": "a"}]}}), encoding="utf-8")
    assert "z/x" not in str(router.load_config(lab / "models.yaml")["pools"]["strong"])                              # fornitori e pool sconosciuti si ignorano


def test_router_hot_reloads_when_only_the_override_changes(lab):
    cfg = router.load_config(lab / "models.yaml")
    st = router.State(cfg, "k", httpx.MockTransport(lambda r: httpx.Response(200, json={})), cfg_path=lab / "models.yaml")
    assert not st.maybe_reload(every_s=0)
    router.overlay_path().write_text(yaml.safe_dump({"add": {"light": [{"model": "z/ok", "provider": "nvidia"}]}}), encoding="utf-8")
    import os
    os.utime(router.overlay_path(), (time.time() + 5, time.time() + 5))
    assert st.maybe_reload(every_s=0) and any(m["model"] == "z/ok" for m in st.cfg["pools"]["light"])


# ------------------------------------------------------------------ strumenti
def test_system_tools_are_registered_with_the_right_risk():
    assert set(system_tools.NAMES) == set(system_tools.RISK) and len(system_tools.make_tools()) == len(system_tools.NAMES)
    assert system_tools.RISK["router_pool_apply"] == "reversible" and system_tools.RISK["system_status"] == "read"
    assert not [n for n, r in system_tools.RISK.items() if r == "external"]                                          # niente azioni verso l'esterno senza l'ok di Lorenzo


def test_compact_status_only_keeps_problems_when_asked():
    st = status_with(services.item("a", "A", "modelli", "p"), services.item("b", "B", "modelli", "p", status="down", detail="giu'"))
    assert [r["id"] for r in system_tools.compact(st, only_problems=True)["voci"]] == ["b"] and len(system_tools.compact(st)["voci"]) == 2


def test_good_ratio_items_stay_ok_at_one_hundred_percent_and_never_raise_a_quota_alert():
    it = services.item("units", "Servizi", "infrastruttura", "systemd", used=9, limit=9, unit="attivi", good_ratio=True)
    assert it["pct"] == 100.0 and it["status"] == "ok" and sentinel.find_alerts(status_with(it)) == []
    quota = services.item("g", "Groq", "modelli", "gratuito", used=9, limit=9, unit="richieste")
    assert quota["status"] == "down"                                                                  # lo stesso 100% su una quota e' invece esaurita


def test_cloudflare_probe_verifies_an_account_token_on_the_account_endpoint(monkeypatch):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, json={"result": {"status": "active"}})
    monkeypatch.setenv("CF_API_TOKEN", "t")
    monkeypatch.setenv("CF_ACCOUNT_ID", "acc123")
    real = httpx.Client
    monkeypatch.setattr(services, "http", lambda timeout=8.0: real(timeout=timeout, transport=httpx.MockTransport(handler)))
    r = services.probe_cloudflare()[0]
    assert r["status"] == "ok" and seen == ["https://api.cloudflare.com/client/v4/accounts/acc123/tokens/verify"]


def router_rows(model, ok, fail, hours_ago=1.0):
    now = time.time() - hours_ago * 3600
    rows = [{"ts": now, "model": model, "tier": "x", "ok": True, "est_tokens": 1, "cost": 0, "ms": 100}] * ok + [{"ts": now, "model": model, "tier": "x", "ok": False, "est_tokens": 1, "cost": 0, "ms": 100}] * fail
    p = config.DATA_DIR / "router.jsonl"
    old = p.read_text(encoding="utf-8") if p.exists() else ""
    p.write_text(old + "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_a_model_is_removed_only_while_it_is_failing_now_not_on_old_failures():
    pool = "strong"
    cf = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    router_rows("cloudflare/" + cf, ok=40, fail=60, hours_ago=100)                      # tanti errori, ma di quattro giorni fa
    with pytest.raises(ValueError, match="non sta fallendo adesso"):
        sentinel.pool_apply(pool, "cloudflare", cf, "remove", "41% di successi", canary=lambda: True, send=lambda t: None)
    router_rows("cloudflare/" + cf, ok=3, fail=13, hours_ago=4.5)                        # il caso vero: errori di 4 ore fa, difetto corretto, nessun traffico dopo
    with pytest.raises(ValueError, match="non sta fallendo adesso"):
        sentinel.pool_apply(pool, "cloudflare", cf, "remove", "19% di riuscite in 6 ore", canary=lambda: True, send=lambda t: None)
    router_rows("cloudflare/" + cf, ok=1, fail=29, hours_ago=1)                          # fallisce ancora ADESSO (l'ultima richiesta, di 1 ora fa, e' fallita)
    assert sentinel.failing_now("cloudflare", cf)["sta_fallendo"]
    r = sentinel.pool_apply(pool, "cloudflare", cf, "remove", "29 errori su 30 nell'ultima ora", canary=lambda: True, send=lambda t: None)
    assert r["esito"] == "applicata" and ("cloudflare", cf) not in pool_models(pool)


def test_a_model_whose_last_request_succeeded_is_never_removed():
    router_rows("cloudflare/x", ok=0, fail=20, hours_ago=3)
    router_rows("cloudflare/x", ok=1, fail=0, hours_ago=0.5)                             # l'ultima e' riuscita: il difetto potrebbe essere passato
    assert not sentinel.failing_now("cloudflare", "x")["sta_fallendo"]


def test_router_stats_expose_the_last_24_hours_separately():
    router_rows("m1", ok=3, fail=0, hours_ago=100)
    router_rows("m1", ok=1, fail=9, hours_ago=1)
    row = next(r for r in sentinel.model_report(7) if r["model"] == "m1")
    assert row["richieste"] == 13 and row["successo_pct"] == 31 and row["richieste_24h"] == 10 and row["successo_24h_pct"] == 10


def test_rate_limits_are_not_model_failures():
    model = "openai/gpt-oss-20b"
    now = time.time() - 3600
    rows = [{"ts": now, "model": "groq/" + model, "tier": "groq", "ok": True, "est_tokens": 1, "cost": 0, "ms": 100, "status": 200}] * 4
    rows += [{"ts": now, "model": "groq/" + model, "tier": "groq", "ok": False, "est_tokens": 1, "cost": 0, "ms": 100, "status": 429}] * 6
    (config.DATA_DIR / "router.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert sentinel.recent_stats("groq", model) == (4, 0)                                  # 6 su 10 erano 429: nessun errore vero
    with pytest.raises(ValueError, match="non sta fallendo adesso"):
        sentinel.pool_apply("light", "groq", model, "remove", "40% di successi", canary=lambda: True, send=lambda t: None)
    row = next(r for r in sentinel.model_report(7) if r["model"] == "groq/" + model)
    assert row["limiti_429_503_24h"] == 6 and row["successo_24h_pct"] == 100


def test_an_undone_change_does_not_use_up_the_daily_limit():
    for i in range(sentinel.MAX_CHANGES_DAY):
        record_pass("nvidia", f"x/m{i}")
        sentinel.pool_apply("strong", "nvidia", f"x/m{i}", "add", "ok", canary=lambda: True, send=lambda t: None)
    assert sentinel.changes_today() == sentinel.MAX_CHANGES_DAY
    sentinel.rollback_last(send=lambda t: None)
    assert sentinel.changes_today() == sentinel.MAX_CHANGES_DAY - 1                       # l'annullata non conta: se ne puo' fare un'altra
    record_pass("nvidia", "x/nuovo")
    assert sentinel.pool_apply("strong", "nvidia", "x/nuovo", "add", "ok", canary=lambda: True, send=lambda t: None)["esito"] == "applicata"
