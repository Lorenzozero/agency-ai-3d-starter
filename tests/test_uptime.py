import asyncio
import json

import httpx

from hlab import registry, tools, uptime
from hlab.tools import ops_tools

NOW = 1_800_000_000.0
SITE = {"uptime": {"urls": {"Officina Rapida": "https://officina.test"}, "netlify": False}}      # nei test niente rete ne' cassaforte


class Web:
    def __init__(self, status=200, boom=False):
        self.status, self.boom = status, boom

    def __call__(self, req):
        if self.boom:
            raise httpx.ConnectError("rifiutata")
        return httpx.Response(self.status, text="ok")


class NoDb:
    def query(self, sql, params=()):
        raise RuntimeError("CRM irraggiungibile")


def go(web, now, ssl_days=60, db=None, quotas=SITE):
    return asyncio.run(uptime.run(transport=httpx.MockTransport(web), db=db or NoDb(), quotas=quotas, ssl_fn=lambda u: ssl_days, now=now))


def test_a_site_is_down_only_after_two_failed_checks_and_recovery_is_told_once():
    assert go(Web(), NOW)[0] == []
    a, st = go(Web(status=503), NOW + 900)
    assert a == [] and st["Officina Rapida"]["fails"] == 1 and not st["Officina Rapida"].get("down")      # un solo intoppo non e' un guasto
    a, st = go(Web(boom=True), NOW + 1800)
    assert len(a) == 1 and a[0][0] is True and "giù" in a[0][1] and "2 controlli" in a[0][1] and st["Officina Rapida"]["down"]
    assert go(Web(boom=True), NOW + 2700)[0] == []                                              # gia' detto: niente ripetizioni
    a, st = go(Web(), NOW + 3600)
    assert len(a) == 1 and "tornato online" in a[0][1] and not st["Officina Rapida"]["down"]
    assert go(Web(), NOW + 4500)[0] == []


def test_expiring_certificate_is_told_once_a_week_and_slow_sites_once_a_day():
    a, _ = go(Web(), NOW, ssl_days=10)
    assert len(a) == 1 and "10 giorni" in a[0][1] and a[0][0] is False
    assert go(Web(), NOW + 900, ssl_days=10)[0] == []
    a, _ = go(Web(), NOW + 7 * 86400, ssl_days=3)
    assert len(a) == 1 and a[0][0] is True                                                      # a 3 giorni e' urgente
    assert go(Web(), NOW + 8 * 86400, ssl_days=80)[0] == []


def test_targets_merge_crm_online_projects_with_extra_urls_and_survive_a_broken_crm():
    class Db:
        def query(self, sql, params=()):
            assert sql.lstrip().upper().startswith("SELECT")
            return [{"name": "Atelier", "target_url": "https://atelier.test"}, {"name": "Rotto", "target_url": "non-un-url"}]
    assert uptime.targets(Db(), SITE) == {"Officina Rapida": "https://officina.test", "Atelier": "https://atelier.test"}
    assert uptime.targets(NoDb(), SITE) == {"Officina Rapida": "https://officina.test"}


def test_removed_sites_leave_the_state_and_summary_reads_in_italian():
    _, st = go(Web(boom=True), NOW)
    assert "Officina Rapida" in st
    _, st = go(Web(), NOW + 900, quotas={"uptime": {"urls": {}, "netlify": False}})
    assert st == {} and uptime.summary(st) == "Nessun sito monitorato."
    _, st = go(Web(), NOW)
    assert "🟢 Officina Rapida: HTTP 200" in uptime.summary(st) and "certificato 60 gg" in uptime.summary(st)


def run_tool(t, tool_name, **a):
    r = asyncio.run(next(x for x in t if x.name == tool_name).handler(a))
    txt = r["content"][0]["text"]
    return r, (json.loads(txt) if txt[:1] in "[{" else txt)


def test_ops_agent_is_read_only_and_cannot_check_arbitrary_addresses(monkeypatch):
    spec = registry.load_all()["ops-uptime"]
    names = tools.tool_names(spec)
    assert "mcp__ops__site_health" in names and not any(n.startswith(("mcp__pow_", "mcp__prop_", "mcp__draft_")) for n in names)
    assert all(tools.risk_of(n) in ("read", "delegate") for n in names if n.startswith("mcp__ops__"))
    go(Web(), NOW)
    t = ops_tools.make_tools()
    _, health = run_tool(t, "site_health")
    assert health["siti"]["Officina Rapida"]["ok"] is True and "🟢" in health["riepilogo"]
    r, msg = run_tool(t, "check_site", name="http://interno.local/admin")
    assert r["is_error"] and "non e' monitorato" in msg                                           # niente URL a scelta: niente richieste verso la rete interna

    async def fake_check(url, *a, **k):
        return {"ok": True, "status": 200, "ms": 5, "error": "", "ssl_days": 40}
    monkeypatch.setattr(uptime, "check", fake_check)
    assert run_tool(t, "check_site", name="quick key")[1]["status"] == 200                        # nome senza distinzione di maiuscole


def test_netlify_sites_are_listed_read_only_and_a_failure_never_blocks_the_other_sources():
    seen = []

    def api(req):
        seen.append((req.method, req.url.path, req.headers["authorization"][:6]))
        return httpx.Response(200, json=[{"name": "vetreria-alfa", "ssl_url": "https://vetreria-alfa.netlify.app"}, {"name": "senza-url"},
                                         {"name": "crossbay", "url": "http://crossbay.netlify.app", "ssl_url": ""}])
    from hlab.vault import Vault
    Vault().add("netlify-azienda", "netlify", "nfp_" + "N" * 36)
    assert uptime.netlify_sites(httpx.MockTransport(api)) == {"vetreria-alfa": "https://vetreria-alfa.netlify.app", "crossbay": "http://crossbay.netlify.app"}
    assert seen == [("GET", "/api/v1/sites", "Bearer")]
    assert uptime.targets(NoDb(), {"uptime": {"urls": {}}}, netlify_fn=lambda: {"s": "https://s.test"}) == {}                  # spento di default: gli staging darebbero falsi allarmi
    on = {"uptime": {"urls": {"Extra": "https://extra.test"}, "netlify": True}}
    assert uptime.targets(NoDb(), on, netlify_fn=lambda: {"vetreria-alfa": "https://s.test", "Extra": "https://ignorato.test"}) == {"Extra": "https://extra.test", "vetreria-alfa": "https://s.test"}
    assert uptime.netlify_sites(httpx.MockTransport(lambda r: httpx.Response(500))) == {}


def test_password_protected_staging_is_reachable_not_down():
    for status in (401, 403):
        a, st = go(Web(status=status), NOW + status)
        assert a == [] and st["Officina Rapida"]["ok"] and not st["Officina Rapida"].get("down")
        assert "protetto" in uptime.summary(st)
    a, st = go(Web(status=404), NOW + 5000)
    a2, st = go(Web(status=404), NOW + 5900)
    assert a2 and "giù" in a2[0][1]                                                                  # un 404 vero resta un guasto
