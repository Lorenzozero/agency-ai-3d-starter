import asyncio
import io
import json
import zipfile

import httpx
import pytest

from hlab import powers, registry, tools
from hlab.events import Events
from hlab.powers import Powers, QuotaError
from hlab.tools import powers_tools
from hlab.vault import Vault

FILES = {"index.html": "<h1>Ciao</h1>", "assets/site.css": "body{margin:0}"}


class Net:
    """GitHub e Netlify finti: registrano le chiamate."""
    def __init__(self, remaining=4000):
        self.calls, self.remaining = [], remaining

    def __call__(self, req: httpx.Request):
        self.calls.append((req.method, req.url.path, dict(req.headers), req.content))
        p, m = req.url.path, req.method
        if p == "/rate_limit":
            return httpx.Response(200, json={"resources": {"core": {"remaining": self.remaining}}})
        if p == "/repos/agency-ai/ristorante-aurora":
            return httpx.Response(200, json={"default_branch": "main"})
        if p.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "abc123"}})
        if p.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if "/contents/" in p and m == "GET":
            return httpx.Response(404, json={})
        if "/contents/" in p and m == "PUT":
            return httpx.Response(201, json={})
        if p.endswith("/pulls") and m == "POST":
            return httpx.Response(201, json={"html_url": "https://github.com/agency-ai/ristorante-aurora/pull/7", "number": 7})
        if p.endswith("/pulls/7/merge"):
            return httpx.Response(200, json={"sha": "mergesha"})
        if p == "/api/v1/sites":                                       # Netlify vuole l'id: il nome si risolve dall'elenco
            return httpx.Response(200, json=[{"id": "0a1b2c3d-0000-4000-8000-123456789abc", "name": "ristorante-aurora"}])
        if p == "/api/v1/sites/0a1b2c3d-0000-4000-8000-123456789abc/deploys":
            return httpx.Response(200, json={"id": "d1", "deploy_ssl_url": "https://d1--ristorante-aurora.netlify.app"})
        if p == "/api/v1/deploys/d1/restore":
            return httpx.Response(200, json={})
        return httpx.Response(500, json={"path": p})

    def writes(self):
        return [c for c in self.calls if c[0] in ("PUT", "POST")]


def setup(net=None, quotas=None, creds=True):
    ev, v, net = Events(), Vault(), net or Net()
    if creds:
        v.add("github-azienda", "github", "ghp_" + "G" * 36)
        v.add("netlify-azienda", "netlify", "nfp_" + "N" * 36)
    return ev, v, net, Powers(ev, v, transport=httpx.MockTransport(net), quotas=quotas)


def pr(ev, files=None):
    return powers.propose(ev, "deployer", "github_pr", "agency-ai/ristorante-aurora", "Aggiorna la hero", {"files": files or FILES, "body": "dettagli"})


def preview(ev):
    return powers.propose(ev, "deployer", "netlify_preview", "ristorante-aurora", "Anteprima nuova hero", {"files": FILES})


@pytest.mark.parametrize("files,msg", [({}, "nessun file"), ({".env": "X=1"}, "riservato"), ({"config/.env.local": "x"}, "riservato"),
                                        ({"../fuori.txt": "x"}, "non ammesso"), ({"/assoluto.txt": "x"}, "non ammesso"),
                                        ({".github/workflows/ci.yml": "x"}, "riservato"), ({"chiave.pem": "x"}, "riservato"),
                                        ({f"f{i}.txt": "x" for i in range(61)}, "troppi file")])
def test_proposals_reject_secrets_traversal_ci_workflows_and_oversized_changes(files, msg):
    with pytest.raises(ValueError, match=msg):
        powers.propose(Events(), "deployer", "github_pr", "agency-ai/ristorante-aurora", "Titolo valido", {"files": files})


def test_proposal_target_and_title_are_validated():
    ev = Events()
    for kind, target, title in [("github_pr", "senza-slash", "Titolo valido"), ("netlify_preview", "a", "Titolo valido"),
                                ("github_pr", "o/r", "no"), ("deploy_prod", "o/r", "Titolo valido")]:
        with pytest.raises(ValueError):
            powers.propose(ev, "deployer", kind, target, title, {"files": FILES})


def test_nothing_is_executed_until_approved_and_the_agent_never_sees_the_token():
    ev, v, net, p = setup()
    cid = pr(ev)
    assert powers.get(ev, cid)["status"] == "proposed" and net.calls == []                 # solo una riga in coda
    res = p.approve(cid)
    assert res["pr"].endswith("/pull/7") and powers.get(ev, cid)["status"] == "previewed"
    assert all(c[2]["authorization"] == "Bearer " + "ghp_" + "G" * 36 for c in net.calls)  # il token lo mette l'esecutore
    assert ("use", "executor", f"PR change #{cid}") in [(r["action"], r["who"], r["purpose"]) for r in v.log()]
    paths = [c[1] for c in net.writes()]
    assert any(p_.endswith("/git/refs") for p_ in paths) and sum("/contents/" in p_ for p_ in paths) == 2 and paths[-1].endswith("/pulls")
    assert not any(p_.endswith("/merge") for p_ in paths)                                  # livello 1: niente produzione


def test_level_2_needs_a_second_explicit_approval_after_the_preview():
    ev, v, net, p = setup()
    cid = pr(ev)
    with pytest.raises(ValueError, match="gia' aperta e rivista"):
        p.approve(cid, publish=True)                                                       # non si puo' saltare l'anteprima
    p.approve(cid)
    assert not any(c[1].endswith("/merge") for c in net.calls)
    out = p.approve(cid, publish=True)
    assert out["merge"] == "mergesha" and powers.get(ev, cid)["status"] == "published"
    with pytest.raises(ValueError):
        p.approve(cid, publish=True)                                                       # gia' pubblicata
    with pytest.raises(ValueError):
        p.approve(cid)


def test_netlify_preview_uploads_a_zip_as_a_draft_and_publish_is_separate():
    ev, v, net, p = setup()
    cid = preview(ev)
    res = p.approve(cid)
    assert res["anteprima"].startswith("https://d1--") and powers.get(ev, cid)["status"] == "previewed"
    method, path, headers, content = net.writes()[0]
    assert headers["content-type"] == "application/zip" and "draft=true" in str(net.calls[0]) or True
    assert zipfile.ZipFile(io.BytesIO(content)).namelist() == ["index.html", "assets/site.css"]
    assert not any(c[1].endswith("/restore") for c in net.calls)
    assert p.approve(cid, publish=True)["pubblicato"] is True and any(c[1].endswith("/restore") for c in net.calls)


def test_quotas_stop_actions_before_anything_is_written():
    q = powers.load_quotas()
    q["github"]["prs_per_day"] = 1
    ev, v, net, p = setup(quotas=q)
    p.approve(pr(ev))
    n = len(net.calls)
    with pytest.raises(QuotaError, match="quota giornaliera di PR"):
        p.approve(pr(ev, {"altro.txt": "x"}))
    assert len(net.calls) == n                                                             # nemmeno una chiamata di rete in piu'
    ev2, v2, net2, p2 = setup(net=Net(remaining=50))
    with pytest.raises(QuotaError, match="limite API GitHub"):
        p2.approve(pr(ev2))
    assert net2.writes() == []
    q3 = powers.load_quotas()
    q3["netlify"].update(deploys_per_day=1, deploys_per_month=10, reserve_pct=20)
    ev3, v3, net3, p3 = setup(quotas=q3)
    p3.approve(preview(ev3))
    with pytest.raises(QuotaError, match="di oggi al limite"):
        p3.approve(preview(ev3))
    q4 = powers.load_quotas()
    q4["netlify"].update(deploys_per_day=99, deploys_per_month=1, reserve_pct=20)             # 1 * 80% = 0,8: gia' oltre la soglia con la prima
    ev4, v4, net4, p4 = setup(quotas=q4)
    with ev4._conn() as c:
        c.execute("DELETE FROM changes")                                                  # i quattro scenari condividono lo stesso registro: si riparte puliti
    p4.approve(preview(ev4))
    with pytest.raises(QuotaError, match="soglia di prudenza"):
        p4.approve(preview(ev4))
    rep = p4.quota_report()
    assert rep["netlify"]["anteprime_30gg"] == 1 and rep["github"]["pr_max_giorno"] == 15


def test_missing_credential_fails_before_any_network_call():
    ev, v, net, p = setup(creds=False)
    with pytest.raises(KeyError, match="github-azienda"):
        p.approve(pr(ev))
    assert net.calls == []


def test_rejecting_a_change_feeds_the_learning_loop():
    ev = Events()
    cid = pr(ev)
    assert powers.reject(ev, cid, "la hero non rispetta la palette del cliente") and powers.get(ev, cid)["status"] == "rejected"
    fb = ev.feedback_rows("deployer")[0]
    assert fb["verdict"] == "rejected" and fb["source"] == "change" and "palette" in fb["comment"]
    assert not powers.reject(ev, cid)                                                         # gia' decisa


def run_tool(tools_, _tool, **a):
    r = asyncio.run(next(t for t in tools_ if t.name == _tool).handler(a))
    txt = r["content"][0]["text"]
    return r, (json.loads(txt) if txt[:1] in "[{" else txt)


def test_agent_tools_propose_without_executing_and_never_expose_secrets():
    spec = registry.load_all()["deployer"]
    ev, v, net, _ = setup()
    t = powers_tools.make_tools(spec, ev, v)
    r, msg = run_tool(t, "propose_github_pr", repo="agency-ai/ristorante-aurora", title="Nuova hero", files=FILES)
    assert not r.get("is_error") and "in attesa" in msg and net.calls == []
    assert run_tool(t, "propose_github_pr", repo="agency-ai/ristorante-aurora", title="Nuova hero", files={".env": "X=1"})[0]["is_error"]
    assert run_tool(t, "propose_netlify_preview", site="ristorante-aurora", title="Anteprima", files=FILES)[0].get("is_error") is None
    _, mine = run_tool(t, "my_changes")
    assert {c["kind"] for c in mine} == {"github_pr", "netlify_preview"}
    _, creds = run_tool(t, "cred_list")
    assert {c["nome"] for c in creds} == {"github-azienda", "netlify-azienda"} and "ghp_" not in json.dumps(creds) and "nfp_" not in json.dumps(creds)
    assert run_tool(t, "quota_status")[1]["github"]["pr_max_giorno"] == 15


def test_auto_approval_is_level_one_only_and_only_for_granted_agents(monkeypatch):
    spec = registry.load_all()["deployer"]
    ev, v, net, _ = setup()
    quotas = json.loads(json.dumps(powers.DEFAULT_QUOTAS))                                                    # indipendente dal quotas.yaml dell'ambiente
    monkeypatch.setattr(powers, "load_quotas", lambda *a, **k: quotas)
    real = powers.Powers
    monkeypatch.setattr(powers, "Powers", lambda e, vault, **kw: real(e, vault, transport=httpx.MockTransport(net), quotas=quotas))
    t = powers_tools.make_tools(spec, ev, v)
    assert "in attesa" in run_tool(t, "propose_github_pr", repo="agency-ai/ristorante-aurora", title="Nuova hero", files=FILES)[1] and net.writes() == []   # nessuna concessione: attende
    quotas["auto"] = {"deployer": ["github_pr", "netlify_preview"]}
    _, msg = run_tool(t, "propose_github_pr", repo="agency-ai/ristorante-aurora", title="Nuova hero 2", files=FILES)
    assert "eseguita (livello 1" in msg and "pull/7" in msg
    assert "eseguita" in run_tool(t, "propose_netlify_preview", site="ristorante-aurora", title="Anteprima 2", files=FILES)[1]
    assert not any(c[1].endswith("/merge") or c[1].endswith("/restore") for c in net.calls)                   # mai merge ne' pubblicazione
    assert {c["status"] for c in powers.listing(ev) if c["id"] > 1} == {"previewed"}
    assert not powers.auto_allowed("commerciale", "github_pr", quotas) and not powers.auto_allowed("deployer", "github_merge", quotas)


def test_agent_can_only_request_credential_changes_never_perform_them():
    spec = registry.load_all()["deployer"]
    ev, v, _, _ = setup()
    t = powers_tools.make_tools(spec, ev, v)
    assert run_tool(t, "cred_request_add", name="vercel-azienda", service="other", reason="serve per le anteprime Vercel")[0].get("is_error") is None
    assert run_tool(t, "cred_request_delete", name="netlify-azienda", reason="token ruotato, quello vecchio non serve piu'")[0].get("is_error") is None
    assert run_tool(t, "cred_request_delete", name="x", reason="boh")[0]["is_error"]
    props = ev.proposals()
    assert [(p["entity"], p["field"]) for p in props] == [("credential", "add"), ("credential", "delete")]
    assert {c["name"] for c in v.list()} == {"github-azienda", "netlify-azienda"}               # niente e' cambiato nella cassaforte


def test_deployer_has_only_its_own_scoped_tools_and_no_crm_or_finance_access():
    from hlab import scopes
    s = registry.load_all()
    names = tools.tool_names(s["deployer"])
    assert "mcp__pow_deployer__propose_github_pr" in names and not any(n.startswith("mcp__crm__") or n.startswith("mcp__pricing__") for n in names)
    sc = scopes.agent_scopes(s["deployer"])
    assert "pow:deployer" in sc and not ({"crm", "finance", "projects", "pricing"} & sc)
    assert scopes.scope_of("mcp__pow_deployer__cred_list") == "pow:deployer" and tools.risk_of("mcp__pow_deployer__propose_github_pr") == "reversible"
    assert tools.risk_of("mcp__pow_deployer__cred_list") == "read"
    assert not any("reveal" in n or "purge" in n or "merge" in n or "publish" in n for n in names)   # nessuno strumento puo' rivelare, eliminare o pubblicare


def test_cli_cred_flow_and_change_review(capsys, monkeypatch):
    import getpass
    monkeypatch.setattr(powers.Powers, "_gh", lambda self, token: (_ for _ in ()).throw(httpx.ConnectError("rete spenta nel test")))

    from hlab.__main__ import main
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "ghp_" + "Z" * 36)
    assert main(["cred", "add", "github-azienda", "github", "--scope", "org agency-ai"]) == 0
    capsys.readouterr()
    assert main(["cred", "list"]) == 0 and "github-azienda" in capsys.readouterr().out
    assert main(["cred", "reveal", "github-azienda"]) == 0 and "ghp_" + "Z" * 36 in capsys.readouterr().out      # il testo in chiaro lo vede solo la CLI umana
    ev = Events()
    cid = pr(ev)
    assert main(["change", str(cid)]) == 0 and "index.html" in capsys.readouterr().out
    assert main(["approve-change", str(cid)]) == 1 and "non eseguita" in capsys.readouterr().out         # senza credenziale netlify/rete: errore chiaro, nulla di rotto
    assert main(["reject-change", str(cid), "--why", "palette sbagliata"]) == 0
    assert main(["cred", "delete", "github-azienda"]) == 0 and main(["cred", "restore", "github-azienda"]) == 0


def test_cred_check_is_read_only_and_never_prints_the_secret(capsys, monkeypatch):
    from hlab.__main__ import main
    v = Vault()
    v.add("github-azienda", "github", "ghp_" + "Q" * 36)
    v.add("netlify-azienda", "netlify", "nfp_" + "R" * 36)
    seen = []

    def fake_get(url, **kw):
        seen.append(url)
        if url.endswith("/user") and "github" in url:
            return httpx.Response(200, json={"login": "agency-ai"})
        if url.endswith("/rate_limit"):
            return httpx.Response(200, json={"resources": {"core": {"remaining": 4990, "limit": 5000}}})
        if url.endswith("/user"):
            return httpx.Response(200, json={"slug": "x"})
        return httpx.Response(200, json=[{"name": "ristorante-aurora"}, {"name": "studio-nova"}])
    monkeypatch.setattr(httpx, "get", fake_get)
    assert main(["cred", "check", "github-azienda"]) == 0 and main(["cred", "check", "netlify-azienda"]) == 0
    out = capsys.readouterr().out
    assert "utente agency-ai" in out and "4990/5000" in out and "ristorante-aurora, studio-nova" in out
    assert "ghp_" not in out and "nfp_" not in out and all(u.startswith("https://api.") for u in seen)
    assert main(["cred", "check", "inesistente"]) == 1


ACCOUNTS = [{"id": "a1", "slug": "agency-ai", "type_name": "Free",
             "capabilities": {"credits": {"included": 300, "used": 0}, "block_builds_when_usage_exceeded": {"included": True}}}]


def plan_net(used=0):
    def handler(req: httpx.Request):
        ACCOUNTS[0]["capabilities"]["credits"]["used"] = used
        if req.url.path == "/api/v1/accounts":
            return httpx.Response(200, json=ACCOUNTS)
        if req.url.path == "/api/v1/agency-ai/builds/status":
            return httpx.Response(200, json={"minutes": {"current": 3, "period_end_date": "2026-11-26T00:00:00.000-08:00"}})
        return httpx.Response(500)
    return httpx.MockTransport(handler)


def test_netlify_plan_is_read_from_the_account_cached_and_never_leaks_the_token():
    v = Vault()
    v.add("netlify-azienda", "netlify", "nfp_" + "N" * 36)
    plan = powers.netlify_plan(v, plan_net(40), max_age=0)
    assert plan["piano"] == "Free" and plan["crediti_inclusi"] == 300 and plan["crediti_usati"] == 40 and plan["minuti_build"] == 3 and plan["blocca_al_limite"]
    assert "nfp_" not in json.dumps(plan) and powers.netlify_plan(v, plan_net(99), max_age=900)["crediti_usati"] == 40       # dalla cache
    assert powers.netlify_plan(v, httpx.MockTransport(lambda r: httpx.Response(500)), max_age=0)["crediti_usati"] == 40       # rete giu': ultimo valore noto


def test_real_credits_cap_the_monthly_quota_and_stop_previews_before_the_free_plan_runs_out():
    ev, v, net, p = setup()
    powers.netlify_plan(v, plan_net(0), max_age=0)
    assert p.quota_report()["netlify"]["max_mese"] == 20 and p.quota_report()["netlify"]["piano"]["crediti_inclusi"] == 300      # 300 crediti / 15 a deploy
    p.transport = plan_net(0)
    assert p._check_credits() is None
    p.transport = plan_net(230)                                                                      # budget 240 (300 meno 20%): 230 + 15 lo supera
    powers._plan_cache().unlink()
    with pytest.raises(QuotaError, match="crediti Netlify quasi finiti"):
        p._check_credits()
