"""Piu' fornitori: esaurire le quote gratuite di ciascuno prima di pagare."""
import json
import time

import httpx
import pytest
import yaml
from starlette.testclient import TestClient

from hlab import router

MULTI = {"strong": [{"model": "f1:free"}, {"model": "gpt-oss-x", "provider": "groq"}, {"model": "@cf/llama", "provider": "cloudflare"}, {"model": "p1"}]}
PROVIDERS = {"groq": {"base_url": "https://api.groq.test/v1", "key_env": "GROQ_API_KEY", "free": True, "max_input_tokens": 6500, "max_tokens": 2048},
             "cloudflare": {"base_url": "https://api.cf.test/v1", "key_env": "CF_API_TOKEN", "free": True, "max_input_tokens": 20000}}
TOOL_MSG = {"model": "hlab-strong", "max_tokens": 100, "messages": [{"role": "user", "content": "cerca"}],
            "tools": [{"name": "memory_search", "description": "cerca", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}}]}


def multi(handler, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    monkeypatch.setenv("CF_API_TOKEN", "ck")
    cfg = router.load_config()
    cfg.update(providers=PROVIDERS, pools=MULTI, tier_order=["openrouter", "groq", "cloudflare", "paid"], allow_paid=True, max_wait_s=0, limits={"free_per_minute": 0, "free_per_day": 0, "paid_usd_per_day": 0})
    app = router.make_app(cfg, "ork", transport=httpx.MockTransport(handler))
    return TestClient(app), app.state.hl


def provider_handler(seen, closed=()):
    def h(req):
        host, body = req.url.host, json.loads(req.content)
        seen.append((host, body.get("model")))
        if host.startswith("api.groq") and "groq" in closed:
            return httpx.Response(429, json={"error": {"message": "Rate limit reached for model: requests per day (RPD): Limit 1000"}})
        if host.startswith("api.cf") and "cloudflare" in closed:
            return httpx.Response(429, json={"errors": [{"code": 4006, "message": "you have used up your daily free allocation of 10,000 neurons"}]})
        if host.startswith("openrouter") and "openrouter" in closed and body["model"].endswith(":free"):
            return httpx.Response(429, json={"error": {"message": "free-models-per-day"}})
        if host.startswith("openrouter"):
            return httpx.Response(200, json={"content": [{"type": "text", "text": "pagato"}], "usage": {"cost": 0.001}})
        call = {"id": "call_1", "type": "function", "function": {"name": "memory_search", "arguments": json.dumps({"query": "acconto"})}}
        return httpx.Response(200, json={"id": "x", "choices": [{"message": {"content": None, "tool_calls": [call]}, "finish_reason": "tool_calls"}],
                                         "usage": {"prompt_tokens": 40, "completion_tokens": 9}})
    return h


def test_each_free_provider_is_used_in_order_and_closes_on_its_own_daily_limit(monkeypatch):
    seen = []
    c, st = multi(provider_handler(seen, closed=("openrouter",)), monkeypatch)
    r = c.post("/v1/messages", json=TOOL_MSG)
    assert r.headers["x-hlab-tier"] == "groq" and r.headers["x-hlab-model"] == "groq/gpt-oss-x"
    msg = r.json()                                     # risposta OpenAI tradotta in formato Anthropic, con la chiamata allo strumento
    assert msg["stop_reason"] == "tool_use" and msg["content"][0] == {"type": "tool_use", "id": "call_1", "name": "memory_search", "input": {"query": "acconto"}}
    assert st.closed["openrouter"] > time.time()
    c2, st2 = multi(provider_handler(seen, closed=("openrouter", "groq")), monkeypatch)
    assert c2.post("/v1/messages", json=TOOL_MSG).headers["x-hlab-tier"] == "cloudflare" and st2.closed["groq"] > time.time()
    c3, st3 = multi(provider_handler(seen, closed=("openrouter", "groq", "cloudflare")), monkeypatch)
    assert c3.post("/v1/messages", json=TOOL_MSG).headers["x-hlab-tier"] == "paid"        # si paga solo a quota gratuita finita su tutti
    assert st3.closed["cloudflare"] > time.time()
    n = len(seen)
    assert c3.post("/v1/messages", json=TOOL_MSG).headers["x-hlab-tier"] == "paid" and all(h.startswith("openrouter") for h, _ in seen[n:])
    health = c3.get("/health").json()
    assert health["tiers"]["groq"]["closed_s"] > 60 and set(health["providers"]) == {"groq", "cloudflare"}


def test_big_requests_skip_providers_with_a_small_context_window(monkeypatch):
    seen = []
    c, _ = multi(provider_handler(seen, closed=("openrouter",)), monkeypatch)
    medium = {**TOOL_MSG, "messages": [{"role": "user", "content": "x" * 40_000}]}          # ~10.000 token: Groq (6.500) no, Cloudflare (20.000) si'
    assert c.post("/v1/messages", json=medium).headers["x-hlab-tier"] == "cloudflare"
    huge = {**TOOL_MSG, "messages": [{"role": "user", "content": "x" * 120_000}]}            # ~30.000 token: solo il livello a pagamento
    assert c.post("/v1/messages", json=huge).headers["x-hlab-tier"] == "paid"


def test_provider_without_a_key_is_ignored_and_streaming_is_synthesised(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("CF_API_TOKEN", "ck")
    cfg = router.load_config()
    cfg.update(providers=PROVIDERS, pools=MULTI, max_wait_s=0)
    seen = []
    app = router.make_app(cfg, "ork", transport=httpx.MockTransport(provider_handler(seen, closed=("openrouter",))))
    r = TestClient(app).post("/v1/messages", json={**TOOL_MSG, "stream": True})
    assert not any(h.startswith("api.groq") for h, _ in seen)                              # Groq senza chiave: saltato
    assert r.headers["content-type"].startswith("text/event-stream") and r.headers["x-hlab-tier"] == "cloudflare"
    t = r.text
    assert t.index("message_start") < t.index("content_block_start") < t.index("input_json_delta") < t.index("message_stop")
    assert '"stop_reason": "tool_use"' in t


def test_unknown_provider_is_rejected_and_shipped_config_is_complete(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump({"pools": {"x": [{"model": "m", "provider": "inventato"}]}}))
    with pytest.raises(ValueError, match="sconosciuto"):
        router.load_config(p)
    cfg = router.load_config()
    assert cfg["tier_order"].index("groq") < cfg["tier_order"].index("paid") and {"groq", "cloudflare"} <= set(cfg["providers"])
    for pool in ("strong", "light"):
        tiers = {router.tier_of({**m, "provider": m.get("provider", "openrouter")}, cfg["providers"]) for m in cfg["pools"][pool]}
        assert {"openrouter", "groq", "cloudflare", "paid"} <= tiers, pool
