import json

import httpx
import pytest

from hlab import decide, powers
from hlab.events import Events
from hlab.tools import proposals_tools
from hlab.vault import Vault

LEAD = "0a1b2c3d-0000-4000-8000-123456789abc"
USER = "5e5e5e5e-0000-4000-8000-5e5e5e5e5e5e"
ADMIN = "adadadad-0000-4000-8000-adadadadadad"
TOK = "tok-" + "x" * 24


class Api:
    """API admin simulata: login con cookie+CSRF, elenco utenti, PATCH su lead e utenti. Ricorda chi ha fatto login."""

    def __init__(self):
        self.calls, self.logins = [], []

    def __call__(self, req: httpx.Request):
        p, m = req.url.path, req.method
        self.calls.append((m, p))
        if p == "/api/auth/me":
            return httpx.Response(401, headers={"set-cookie": f"XSRF-TOKEN={TOK}; Path=/"})
        if m != "GET" and req.headers.get("x-xsrf-token") != TOK:
            return httpx.Response(403)
        if p == "/api/auth/login":
            self.logins.append(json.loads(req.content)["email"])
            return httpx.Response(200, json={"user": {}})
        if p == "/api/auth/logout":
            return httpx.Response(204)
        if p == "/api/users" and m == "GET":
            return httpx.Response(200, json=[{"id": USER, "email": "herems@agent.com", "role": "client"}, {"id": ADMIN, "email": "admin@x.it", "role": "admin"}])
        if (p == f"/api/users/{USER}" or p == f"/api/leads/{LEAD}") and m == "PATCH":
            return httpx.Response(200, json={})
        return httpx.Response(404)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(powers, "load_quotas", lambda *a, **k: {"crm": {"base_url": "https://admin.test/api"}})
    api, ev, v = Api(), Events(), Vault()
    v.add("crm-staff", "other", "staff@x.test:" + "s" * 20)
    v.add("crm-admin", "other", "admin@x.test:" + "a" * 20)
    return api, ev, v


def run(env, kind, verdict, ident):
    api, ev, v = env
    return decide.decide(kind, verdict, ident, ev=ev, vault=v, transport=httpx.MockTransport(api))


def test_confirm_button_accepts_applies_once_and_a_second_tap_does_nothing(env):
    api, ev, _ = env
    pid = ev.add_proposal("account-crm", "lead", LEAD, "status", "contattato", "il cliente ha risposto alla mail")
    assert "applicata" in run(env, "hp", "ok", pid) and ("PATCH", f"/api/leads/{LEAD}") in api.calls
    assert api.logins == ["staff@x.test"] and ev.get_proposal(pid)["status"] == "applied"
    n = len(api.calls)
    assert "già" in run(env, "hp", "ok", pid) and len(api.calls) == n                      # doppio tocco: nessuna nuova chiamata


def test_cancel_button_rejects_without_touching_the_crm_and_feeds_learning(env):
    api, ev, _ = env
    pid = ev.add_proposal("account-crm", "lead", LEAD, "status", "perso", "sembra non rispondere piu'")
    assert "annullata" in run(env, "hp", "no", pid) and api.calls == []
    assert ev.get_proposal(pid)["status"] == "rejected" and ev.feedback_rows("account-crm")[0]["verdict"] == "rejected"


def test_deactivating_a_user_uses_the_admin_credential_resolves_the_email_and_never_touches_admins(env):
    api, ev, _ = env
    pid = ev.add_proposal("account-crm", "user", "Herems@Agent.com", "is_active", "false", "account creato per errore, richiesto da Lorenzo in chat")
    assert "applicata" in run(env, "hp", "ok", pid)
    assert api.logins == ["admin@x.test"] and ("PATCH", f"/api/users/{USER}") in api.calls    # solo qui serve il ruolo admin
    api.calls.clear()
    pid2 = ev.add_proposal("account-crm", "user", "admin@x.it", "is_active", "false", "prova di spegnere l'amministratore")
    msg = run(env, "hp", "ok", pid2)
    assert "NON applicata" in msg and "amministratore" in msg and not any(m == "PATCH" for m, _ in api.calls)
    assert ev.get_proposal(pid2)["status"] == "accepted"                                       # resta recuperabile, ma non eseguita


def test_only_is_active_is_allowed_on_users_and_agents_can_propose_it_with_an_email():
    assert proposals_tools.ALLOWED["user"] == {"is_active"}
    assert proposals_tools.EMAIL_RE.match("herems@agent.com") and not proposals_tools.EMAIL_RE.match("non un'email")
    ev = Events()
    pid = ev.add_proposal("account-crm", "user", "x@y.it", "role", "admin", "tentativo di promozione")
    with pytest.raises(ValueError, match="solo attivare"):
        from hlab import crm_write
        ev.decide_proposal(pid, True)
        crm_write.apply(ev, pid, base_url="https://admin.test/api")


def test_change_buttons_approve_level_one_only_never_publish():
    ev, v, calls = Events(), Vault(), []
    v.add("netlify-azienda", "netlify", "nfp_" + "N" * 36)

    def net(req: httpx.Request):
        calls.append((req.method, req.url.path))
        if req.url.path == "/api/v1/sites":
            return httpx.Response(200, json=[{"id": "0a1b2c3d-0000-4000-8000-123456789abc", "name": "ristorante-aurora"}])
        if req.url.path.endswith("/deploys"):
            return httpx.Response(200, json={"id": "d1", "deploy_ssl_url": "https://d1--ristorante-aurora.netlify.app"})
        return httpx.Response(500)
    cid = powers.propose(ev, "deployer", "netlify_preview", "ristorante-aurora", "Anteprima nuova hero", {"files": {"index.html": "<h1>x</h1>"}})
    msg = decide.decide("hc", "ok", cid, ev=ev, vault=v, transport=httpx.MockTransport(net))
    assert "Eseguita" in msg and "restano a te" in msg and not any(p.endswith("/restore") for _, p in calls)
    assert powers.get(ev, cid)["status"] == "previewed" and "già" in decide.decide("hc", "ok", cid, ev=ev, vault=v, transport=httpx.MockTransport(net))
    rid = powers.propose(ev, "deployer", "github_pr", "o/r", "Modifica da rifiutare", {"files": {"docs/a.md": "x"}})
    assert "rifiutata" in decide.decide("hc", "no", rid, ev=ev, vault=v) and powers.get(ev, rid)["status"] == "rejected"
    assert decide.decide("zz", "ok", 1, ev=ev) == "Comando non valido." and "non trovata" in decide.decide("hp", "ok", 999, ev=ev)
