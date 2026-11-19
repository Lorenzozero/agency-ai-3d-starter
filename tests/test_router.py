import json
import time

import httpx
import pytest
import yaml
from starlette.testclient import TestClient

from hlab import router


def make(handler, pools=None, allow_paid=True, **limits):
    cfg = router.load_config()
    cfg["allow_paid"] = allow_paid
    cfg["pools"] = pools or {"strong": [{"model": "a:free"}, {"model": "b:free"}, {"model": "c:free"}], "light": [{"model": "l1:free"}, {"model": "l2:free"}]}
    cfg["max_wait_s"] = 0
    cfg["limits"] = {"free_per_minute": 0, "free_per_day": 0, **limits}
    app = router.make_app(cfg, "SEGRETO", transport=httpx.MockTransport(handler))
    return TestClient(app), app.state.hl


def ok_handler(calls):
    def h(req: httpx.Request):
        body = json.loads(req.content)
        calls.append((body["model"], dict(req.headers), body))
        return httpx.Response(200, json={"type": "message", "model": body["model"], "content": [{"type": "text", "text": "ok"}]})
    return h


MSG = {"model": "hlab-strong", "max_tokens": 10, "messages": [{"role": "user", "content": "ciao"}]}


def test_models_rotate_and_the_request_is_rewritten():
    calls = []
    c, _ = make(ok_handler(calls))
    used = [c.post("/v1/messages", json=MSG).headers["x-hlab-model"] for _ in range(4)]
    assert used == ["a:free", "b:free", "c:free", "a:free"]               # rotazione tra i modelli del pool
    model, headers, body = calls[0]
    assert headers["authorization"] == "Bearer SEGRETO" and "x-api-key" not in headers and body["model"] == "a:free"


def test_light_and_strong_pools_are_independent():
    calls = []
    c, _ = make(ok_handler(calls))
    assert c.post("/v1/messages", json={**MSG, "model": "hlab-light"}).headers["x-hlab-model"] == "l1:free"
    assert c.post("/v1/messages", json={**MSG, "model": "hlab-light[1m]"}).headers["x-hlab-model"] == "l2:free"   # suffisso di contesto ignorato
    assert c.post("/v1/messages", json=MSG).headers["x-hlab-model"] == "a:free"


def test_unsupported_fields_are_stripped_only_for_non_anthropic_models():
    calls = []
    body = {**MSG, "thinking": {"type": "enabled"}, "system": [{"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}}]}
    c, _ = make(ok_handler(calls), pools={"strong": [{"model": "a:free"}], "paid": [{"model": "anthropic/claude-x"}]})
    c.post("/v1/messages", json=body, headers={"anthropic-beta": "prompt-caching-2024"})
    sent = calls[-1][2]
    assert "thinking" not in sent and "cache_control" not in json.dumps(sent) and "anthropic-beta" not in calls[-1][1]
    c.post("/v1/messages", json={**body, "model": "hlab-paid"})
    assert "thinking" in calls[-1][2] and "cache_control" in json.dumps(calls[-1][2])


def test_429_moves_to_next_model_and_puts_the_first_in_cooldown():
    calls = []

    def h(req):
        m = json.loads(req.content)["model"]
        calls.append(m)
        return httpx.Response(429, headers={"retry-after": "7"}, json={"error": "rate"}) if m == "a:free" else \
            httpx.Response(200, json={"content": [], "model": m})

    c, st = make(h)
    r = c.post("/v1/messages", json=MSG)
    assert r.headers["x-hlab-model"] == "b:free" and r.headers["x-hlab-attempts"] == "2"
    assert 5 < st.cool["a:free"] - time.time() <= 7.1                    # rispetta Retry-After
    later = [c.post("/v1/messages", json=MSG).headers["x-hlab-model"] for _ in range(3)]
    assert "a:free" not in later and calls.count("a:free") == 1           # in pausa: non viene piu' provato


@pytest.mark.parametrize("status,minimum", [(500, 25), (503, 25), (404, 3000), (402, 3000)])
def test_server_errors_and_vanished_models_get_cooldowns(status, minimum):
    def h(req):
        m = json.loads(req.content)["model"]
        return httpx.Response(status, json={}) if m == "a:free" else httpx.Response(200, json={"content": []})

    c, st = make(h)
    assert c.post("/v1/messages", json=MSG).status_code == 200
    assert st.cool["a:free"] - time.time() >= minimum


def test_network_timeouts_fall_through_to_the_next_model():
    def h(req):
        if json.loads(req.content)["model"] == "a:free":
            raise httpx.ConnectTimeout("lento")
        return httpx.Response(200, json={"content": []})

    c, _ = make(h)
    assert c.post("/v1/messages", json=MSG).headers["x-hlab-model"] == "b:free"


def test_all_models_failing_returns_an_anthropic_style_overloaded_error():
    c, _ = make(lambda req: httpx.Response(429, json={}))
    r = c.post("/v1/messages", json=MSG)
    assert r.status_code == 529 and r.json()["type"] == "error" and r.json()["error"]["type"] == "overloaded_error"
    assert int(r.headers["retry-after"]) >= 1


def test_streaming_is_passed_through_byte_for_byte():
    sse = b"event: message_start\ndata: {\"type\":\"message_start\"}\n\nevent: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"
    c, _ = make(lambda req: httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"}))
    r = c.post("/v1/messages", json={**MSG, "stream": True})
    assert r.content == sse and r.headers["x-hlab-model"] == "a:free" and r.headers["content-type"].startswith("text/event-stream")


def test_streaming_errors_before_the_body_also_fall_back():
    def h(req):
        if json.loads(req.content)["model"] == "a:free":
            return httpx.Response(429, content=b"nope")
        return httpx.Response(200, content=b"data: ok\n\n", headers={"content-type": "text/event-stream"})

    c, _ = make(h)
    r = c.post("/v1/messages", json={**MSG, "stream": True})
    assert r.content == b"data: ok\n\n" and r.headers["x-hlab-model"] == "b:free"


def test_non_pool_models_pass_through_unchanged_and_errors_are_returned():
    calls = []
    c, _ = make(ok_handler(calls))
    c.post("/v1/messages", json={**MSG, "model": "openai/gpt-x"})
    assert calls[0][0] == "openai/gpt-x"
    c2, _ = make(lambda req: httpx.Response(400, json={"error": "bad"}))
    assert c2.post("/v1/messages", json={**MSG, "model": "openai/gpt-x"}).status_code == 400


def test_local_limiter_protects_the_free_quota_but_not_paid_pools():
    calls = []
    c, _ = make(ok_handler(calls), pools={"strong": [{"model": "a:free"}], "paid": [{"model": "openai/paid"}]}, free_per_minute=2)
    assert [c.post("/v1/messages", json=MSG).status_code for _ in range(3)] == [200, 200, 429]
    r = c.post("/v1/messages", json=MSG)
    assert r.json()["error"]["type"] == "rate_limit_error" and int(r.headers["retry-after"]) >= 1
    assert all(c.post("/v1/messages", json={**MSG, "model": "hlab-paid"}).status_code == 200 for _ in range(5))   # a pagamento: nessun tetto locale


def test_daily_cap_and_health_and_count_tokens():
    c, st = make(ok_handler([]), free_per_day=1)
    assert c.post("/v1/messages", json=MSG).status_code == 200
    assert c.post("/v1/messages", json=MSG).status_code == 429
    h = c.get("/health").json()
    assert h["ok"] and h["today"]["n"] == 1 and h["pools"]["strong"][0]["model"] == "a:free" and h["stats"]["a:free"]["ok"] == 1
    assert c.post("/v1/messages/count_tokens", json=MSG).json()["input_tokens"] > 0


def test_config_loading_and_validation(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump({"listen": {"port": 9000}, "pools": {"x": [{"model": "m:free"}]}}))
    cfg = router.load_config(p)
    assert cfg["listen"] == {"host": "127.0.0.1", "port": 9000} and cfg["cooldown_s"] == 60       # unisce ai default
    p.write_text(yaml.safe_dump({"pools": {"x": []}}))
    with pytest.raises(ValueError):
        router.load_config(p)
    p.write_text(yaml.safe_dump({"pools": {"x": [{"nome": "m"}]}}))
    with pytest.raises(ValueError):
        router.load_config(p)


def test_shipped_models_yaml_is_valid_and_has_both_pools():
    cfg = router.load_config()
    assert {"strong", "light"} <= set(cfg["pools"]) and len(cfg["pools"]["light"]) >= 3


def test_backend_env_points_the_sdk_at_the_router():
    from hlab import runner
    env = runner.backend_env("router")
    assert env["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:") and env["ANTHROPIC_API_KEY"] == ""
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "hlab-strong" and env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "hlab-light"
    assert "ANTHROPIC_BASE_URL" not in runner.backend_env("anthropic")
    o = runner.build_options(crm_db=object(), kind="router")
    assert o.env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "hlab-strong"
    assert o.agents["account-crm"].model == "haiku" and o.model == "sonnet"          # leggeri sul pool gratuito, il COO sul pool forte
    assert o.max_budget_usd == runner.config.ROUTER_RUN_BUDGET_USD > runner.config.RUN_BUDGET_USD   # costo stimato col prezzo Claude: guardia piu' larga
    assert runner.build_options(crm_db=object(), kind="anthropic").max_budget_usd == runner.config.RUN_BUDGET_USD


# ---- livelli gratuito / a pagamento, limite giornaliero, tetto di spesa ----
TIERED = {"strong": [{"model": "f1:free"}, {"model": "f2:free"}, {"model": "p1"}, {"model": "p2"}]}


def cost_handler(calls, cost=0.01):
    def h(req):
        body = json.loads(req.content)
        calls.append(body["model"])
        if body["model"].endswith(":free") and h.free_daily:
            return httpx.Response(429, json={"error": {"message": "Rate limit exceeded: free-models-per-day. Add credits"}})
        return httpx.Response(200, json={"content": [], "model": body["model"], "usage": {"input_tokens": 5, "output_tokens": 5, "cost": cost}})
    h.free_daily = False
    return h


def test_free_models_come_first_then_paid_each_tier_rotating():
    c, st = make(cost_handler([]), pools=TIERED)
    lab = lambda **kw: [e["label"] for e in st.order("strong", **kw)]
    assert lab() == ["f1:free", "f2:free", "p1", "p2"]
    assert lab() == ["f2:free", "f1:free", "p2", "p1"]                           # ogni livello ruota per conto suo
    assert lab(free_ok=False) == ["p1", "p2"] and lab(paid_ok=False) == ["f1:free", "f2:free"]


def test_daily_limit_429_closes_the_free_tier_and_moves_straight_to_paid():
    calls = []
    h = cost_handler(calls)
    h.free_daily = True
    c, st = make(h, pools=TIERED)
    r = c.post("/v1/messages", json=MSG)
    assert r.status_code == 200 and r.headers["x-hlab-model"] in ("p1", "p2")
    assert calls.count("f1:free") + calls.count("f2:free") == 1                  # un solo tentativo gratuito: il limite chiude tutto il livello
    assert st.free_closed_until > time.time() + 60
    n = len(calls)
    for _ in range(3):
        assert c.post("/v1/messages", json=MSG).status_code == 200
    assert all(not m.endswith(":free") for m in calls[n:])                       # poi i gratuiti non si provano piu'
    assert c.get("/health").json()["free"]["open"] is False


def test_local_free_cap_falls_back_to_paid_instead_of_failing():
    calls = []
    c, _ = make(cost_handler(calls), pools=TIERED, free_per_minute=1)
    assert c.post("/v1/messages", json=MSG).headers["x-hlab-model"].endswith(":free")
    assert c.post("/v1/messages", json=MSG).headers["x-hlab-model"] in ("p1", "p2")          # tetto gratuito pieno: va sul pagamento, niente 429


def test_daily_spend_cap_stops_paid_models_using_the_real_cost_from_responses():
    c, st = make(cost_handler([], cost=0.4), pools={"strong": [{"model": "p1"}]}, paid_usd_per_day=0.5)
    assert c.post("/v1/messages", json=MSG).status_code == 200                   # 0,4
    assert c.post("/v1/messages", json=MSG).status_code == 200                   # 0,8: ora e' sopra il tetto
    r = c.post("/v1/messages", json=MSG)
    assert r.status_code == 429 and "tetto di spesa" in r.json()["error"]["message"] and abs(st.spend["usd"] - 0.8) < 1e-9
    assert c.get("/health").json()["spend"]["cap"] == 0.5


def test_cost_is_read_from_streamed_responses_too():
    sse = b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":9,"cost":0.0123}}\n\n'
    c, st = make(lambda req: httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"}), pools={"strong": [{"model": "p1"}]})
    c.post("/v1/messages", json={**MSG, "stream": True})
    assert abs(st.spend["usd"] - 0.0123) < 1e-9


def test_daily_counters_survive_a_restart(tmp_path):
    log = tmp_path / "router.jsonl"
    now = round(time.time())
    rows = [{"ts": now, "model": "f1:free", "ok": True, "status": 200, "ms": 5, "cost": 0.0, "daily": False}] * 3 + \
           [{"ts": now, "model": "p1", "ok": True, "status": 200, "ms": 5, "cost": 0.25, "daily": False},
            {"ts": now, "model": "f2:free", "ok": False, "status": 429, "ms": 5, "cost": 0.0, "daily": True},
            {"ts": now - 3 * 86400, "model": "p1", "ok": True, "status": 200, "ms": 5, "cost": 9.0, "daily": False}]      # ieri: ignorato
    log.write_text("\n".join(json.dumps(r) for r in rows))
    cfg = router.load_config()
    cfg["pools"] = TIERED
    st = router.State(cfg, "k", log_path=log)
    assert st.day["n"] == 3 and abs(st.spend["usd"] - 0.25) < 1e-9 and st.free_closed_until > time.time()


def test_pool_without_paid_models_still_reports_the_daily_limit_clearly():
    h = cost_handler([])
    h.free_daily = True
    c, _ = make(h, pools={"strong": [{"model": "f1:free"}]})
    assert c.post("/v1/messages", json=MSG).status_code == 529                   # primo tentativo: nessuno disponibile
    r = c.post("/v1/messages", json=MSG)
    assert r.status_code == 429 and "limite giornaliero" in r.json()["error"]["message"] and int(r.headers["retry-after"]) > 60


def test_shipped_pools_have_a_free_and_a_paid_tier():
    cfg = router.load_config()
    for pool in ("strong", "light"):
        tiers = {router.is_free(m["model"]) for m in cfg["pools"][pool]}
        assert tiers == {True, False}, pool
    assert cfg["limits"]["paid_usd_per_day"] > 0


def test_allow_paid_false_never_opens_the_paid_tier_nor_named_paid_models():
    calls = []
    c, _ = make(ok_handler(calls), pools={"strong": [{"model": "a:free"}, {"model": "p1"}]}, allow_paid=False)
    assert c.post("/v1/messages", json={**MSG, "model": "hlab-strong"}).status_code == 200 and [x[0] for x in calls] == ["a:free"]
    r = c.post("/v1/messages", json={**MSG, "model": "anthropic/claude-x"})                       # un modello a pagamento richiesto per nome
    assert r.status_code == 403 and "non e' gratuito" in r.json()["error"]["message"] and [x[0] for x in calls] == ["a:free"]


def test_models_yaml_is_hot_reloaded_and_a_broken_file_never_replaces_the_config(tmp_path, monkeypatch):
    import time as _t
    p = tmp_path / "models.yaml"
    p.write_text("pools:\n  strong:\n    - {model: 'a:free'}\n", encoding="utf-8")
    cfg = router.load_config(p)
    st = router.State(cfg, "k", httpx.MockTransport(lambda r: httpx.Response(200, json={})), cfg_path=p)
    assert [m["model"] for m in st.cfg["pools"]["strong"]] == ["a:free"]
    p.write_text("pools:\n  strong:\n    - {model: 'b:free'}\n", encoding="utf-8")
    import os as _os
    _os.utime(p, (_t.time() + 5, _t.time() + 5))
    assert st.maybe_reload(every_s=0) and [m["model"] for m in st.cfg["pools"]["strong"]] == ["b:free"]
    p.write_text("pools: [rotto", encoding="utf-8")
    _os.utime(p, (_t.time() + 10, _t.time() + 10))
    assert not st.maybe_reload(every_s=0) and [m["model"] for m in st.cfg["pools"]["strong"]] == ["b:free"]       # la config buona resta in uso
