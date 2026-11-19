import httpx
import pytest

from hlab import crm_write
from hlab.events import Events
from hlab.vault import Vault

PID = "0a1b2c3d-0000-4000-8000-123456789abc"


class Api:
    """API dell'admin simulata: cookie XSRF sul primo GET, login con CSRF, PATCH solo da sessione valida."""

    def __init__(self, login_ok=True):
        self.calls, self.login_ok = [], login_ok

    def __call__(self, req: httpx.Request):
        p, m = req.url.path, req.method
        self.calls.append((m, p, req.content))
        if p == "/api/auth/me":
            return httpx.Response(401, headers={"set-cookie": "XSRF-TOKEN=tok-" + "x" * 24 + "; Path=/"})
        if m != "GET" and req.headers.get("x-xsrf-token") != "tok-" + "x" * 24:
            return httpx.Response(403, json={"error": {"code": "csrf"}})
        if p == "/api/auth/login":
            return httpx.Response(200 if self.login_ok else 401, json={"user": {"role": "staff"}}, headers={"set-cookie": "cf_at=sessione; Path=/"})
        if p == "/api/auth/logout":
            return httpx.Response(204)
        if p == f"/api/projects/{PID}" and m == "PATCH":
            return httpx.Response(200, json={"id": PID})
        return httpx.Response(404, json={})


def setup(entity="project", field="deadline", value="2026-12-01", accept=True, cred=True):
    ev = Events()
    v = Vault()
    if cred:
        v.add("crm-staff", "other", "hermes@agency.test:" + "p" * 20)
    pid = ev.add_proposal("project-manager", entity, PID, field, value, "il cliente ha chiesto di spostare la consegna")
    if accept:
        ev.decide_proposal(pid, True)
    return ev, v, pid


def run(ev, v, pid, api, base="https://admin.test/api"):
    return crm_write.apply(ev, pid, vault=v, transport=httpx.MockTransport(api), base_url=base)


def test_accepted_proposal_is_applied_through_the_api_with_csrf_and_logout():
    ev, v, pid = setup()
    api = Api()
    res = run(ev, v, pid, api)
    assert res["api"] == f"PATCH /projects/{PID}" and res["stato_http"] == 200
    assert [(m, p) for m, p, _ in api.calls] == [("GET", "/api/auth/me"), ("POST", "/api/auth/login"), ("PATCH", f"/api/projects/{PID}"), ("POST", "/api/auth/logout")]
    assert b'"deadline":"2026-12-01"' in api.calls[2][2].replace(b" ", b"")
    assert ev.get_proposal(pid)["status"] == "applied" and v.list()[0]["uses"] == 1


def test_only_accepted_proposals_are_applied_and_only_once():
    ev, v, pid = setup(accept=False)
    with pytest.raises(ValueError, match="hlab accept"):
        run(ev, v, pid, Api())
    ev.decide_proposal(pid, True)
    run(ev, v, pid, Api())
    with pytest.raises(ValueError, match="applied"):
        run(ev, v, pid, Api())                                                       # una proposta non si applica due volte


def test_field_names_and_types_are_translated_for_the_api():
    assert crm_write.body("payment_deadline", "2026-11-30") == {"paymentDeadline": "2026-11-30"}
    assert crm_write.body("is_online", "si") == {"isOnline": True} and crm_write.body("is_online", "false") == {"isOnline": False}
    with pytest.raises(ValueError):
        crm_write.body("is_online", "forse")
    assert crm_write.route("lead", PID) == f"/leads/{PID}"
    with pytest.raises(ValueError, match="non ancora applicabile"):
        crm_write.route("task", PID)


def test_failures_leave_the_proposal_accepted_and_never_touch_the_crm(monkeypatch):
    monkeypatch.setattr(crm_write.powers, "load_quotas", lambda *a, **k: {})            # indipendente dal quotas.yaml dell'ambiente
    ev, v, pid = setup()
    api = Api(login_ok=False)
    with pytest.raises(httpx.HTTPStatusError):
        run(ev, v, pid, api)
    assert all(m != "PATCH" for m, _, _ in api.calls) and ev.get_proposal(pid)["status"] == "accepted"
    v.delete("crm-staff")                                                            # senza credenziale: errore chiaro, nessuna chiamata
    with pytest.raises(KeyError, match="crm-staff"):
        run(ev, v, pid, Api())
    with pytest.raises(ValueError, match="base_url"):
        crm_write.apply(ev, pid, vault=v, transport=httpx.MockTransport(Api()), base_url="")
