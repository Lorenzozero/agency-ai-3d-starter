import asyncio
import json

import pytest
from starlette.testclient import TestClient

from hlab import api, api_sync, powers
from hlab.docstore import Store
from hlab.events import Events

TOKEN = "t" * 40
AUTH = {"authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "docs.db")


def ts(doc, field):
    return doc[field]["__ts"]


def test_store_set_merge_update_and_special_values(store):
    store.set("tasks/a", {"tarea": "uno", "n": 1, "tags": ["x"], "at": {"__op": "serverTimestamp"}})
    assert isinstance(ts(store.get("tasks/a"), "at"), int)
    store.set("tasks/a", {"estado": "pendiente"}, merge=True)
    assert store.get("tasks/a")["tarea"] == "uno" and store.get("tasks/a")["estado"] == "pendiente"
    store.set("tasks/a", {"solo": True})                                                       # senza merge sostituisce tutto
    assert store.get("tasks/a") == {"solo": True}
    store.update("tasks/a", {"n": {"__op": "increment", "n": 3}, "likes": {"__op": "arrayUnion", "v": ["u1", "u2"]}})
    store.update("tasks/a", {"n": {"__op": "increment", "n": 2}, "likes": {"__op": "arrayUnion", "v": ["u2", "u3"]}})
    d = store.get("tasks/a")
    assert d["n"] == 5 and d["likes"] == ["u1", "u2", "u3"]
    store.update("tasks/a", {"likes": {"__op": "arrayRemove", "v": ["u2"]}, "hours.3": {"__op": "increment", "n": 60}})
    d = store.get("tasks/a")
    assert d["likes"] == ["u1", "u3"] and d["hours"] == {"3": 60}                              # percorso con punto: campo annidato
    store.update("tasks/a", {"solo": {"__op": "delete"}})
    assert "solo" not in store.get("tasks/a")
    with pytest.raises(KeyError):
        store.update("tasks/nope", {"x": 1})
    with pytest.raises(ValueError):
        store.set("tasks", {"x": 1})                                                           # un percorso di collezione non e' un documento


def test_store_queries_like_firestore(store):
    for i, (estado, day, at) in enumerate([("pendiente", "2026-11-01", 30), ("en_progreso", "2026-11-02", 10), ("pendiente", "2026-11-03", 20)]):
        store.set(f"tasks/t{i}", {"estado": estado, "date": day, "createdAt": {"__ts": at}})
    ids = lambda rows: [r["id"] for r in rows]                                                 # noqa: E731
    assert ids(store.query("tasks", [["estado", "==", "pendiente"]])) == ["t0", "t2"] or set(ids(store.query("tasks", [["estado", "==", "pendiente"]]))) == {"t0", "t2"}
    assert ids(store.query("tasks", order=[["createdAt", "desc"]])) == ["t0", "t2", "t1"]
    assert ids(store.query("tasks", order=[["createdAt", "asc"]], limit=2)) == ["t1", "t2"]
    assert set(ids(store.query("tasks", [["date", ">=", "2026-11-02"], ["date", "<=", "2026-11-03"]]))) == {"t1", "t2"}
    assert ids(store.query("tasks", [["estado", "in", ["en_progreso"]]])) == ["t1"]
    store.set("agentChats/c1/mensajes/m1", {"text": "ciao", "createdAt": {"__ts": 1}})
    assert ids(store.query("agentChats/c1/mensajes")) == ["m1"] and store.query("agentChats/c2/mensajes") == []   # sottocollezioni separate
    store.delete("tasks/t0")
    assert store.get("tasks/t0") is None and len(store.query("tasks")) == 2


def test_store_notifies_listeners_and_runs_hooks(store):
    seen = []
    store.hooks.append(lambda path, before, after: seen.append((path, before, after)))

    async def go():
        q = store.subscribe("tasks")
        store.set("tasks/a", {"estado": "pendiente"})
        await asyncio.wait_for(q.get(), 1)
        store.set("tasks/a", {"estado": "completada"}, merge=True)
        await asyncio.wait_for(q.get(), 1)
        store.set("users/u", {"x": 1})                                                         # altra collezione: nessun avviso
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(q.get(), 0.2)
    asyncio.run(go())
    assert seen[1][0] == "tasks/a" and seen[1][1]["estado"] == "pendiente" and seen[1][2]["estado"] == "completada"


def test_api_requires_the_token_and_exposes_the_store(store, tmp_path):
    c = TestClient(api.make_app(store, TOKEN, dist=tmp_path / "nodist"))
    assert c.get("/api/health").json() == {"ok": True}
    for path in ("/api/db/get", "/api/db/query", "/api/db/write", "/api/db/listen"):
        assert c.post(path, json={}).status_code == 401 and c.post(path, json={}, headers={"authorization": "Bearer sbagliato"}).status_code == 401
    assert c.post("/api/login", json={"token": "sbagliato"}).status_code == 401
    assert c.post("/api/login", json={"token": TOKEN}).json()["user"]["uid"] == "lorenzo"
    assert c.post("/api/db/write", json={"op": "set", "path": "tasks/a", "data": {"estado": "pendiente", "at": {"__op": "serverTimestamp"}}}, headers=AUTH).json() == {"ok": True}
    new = c.post("/api/db/write", json={"op": "add", "coll": "tasks", "data": {"estado": "pendiente"}}, headers=AUTH).json()["id"]
    docs = c.post("/api/db/query", json={"coll": "tasks", "where": [["estado", "==", "pendiente"]]}, headers=AUTH).json()["docs"]
    assert {d["id"] for d in docs} == {"a", new}
    c.post("/api/db/write", json={"op": "batch", "ops": [{"op": "delete", "path": "tasks/a"}, {"op": "set", "path": "tasks/b", "data": {"x": 1}, "merge": True}]}, headers=AUTH)
    assert c.post("/api/db/get", json={"path": "tasks/a"}, headers=AUTH).json() == {"data": None}
    assert c.post("/api/db/write", json={"op": "boh"}, headers=AUTH).status_code == 400
    assert c.post("/api/db/write", json={"op": "update", "path": "tasks/zzz", "data": {"x": 1}}, headers=AUTH).status_code == 400
    with c.stream("POST", "/api/db/listen", json={"kind": "query", "coll": "tasks"}, headers=AUTH) as r:
        first = next(r.iter_lines())
    assert first.startswith("data: ") and {d["id"] for d in json.loads(first[6:])["docs"]} == {new, "b"}


def test_static_app_is_served_without_escaping_its_folder(store, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>app</h1>")
    (tmp_path / "segreto.txt").write_text("no")
    c = TestClient(api.make_app(store, TOKEN, dist=dist))
    assert "<h1>app</h1>" in c.get("/").text and "<h1>app</h1>" in c.get("/kanban").text           # rotte dell'app: ricade su index.html
    assert "segreto" not in c.get("/..%2fsegreto.txt").text and "no" != c.get("/../segreto.txt").text
    assert TestClient(api.make_app(store, TOKEN, dist=tmp_path / "manca")).get("/").status_code == 404


def test_sync_maps_hlab_state_onto_the_four_kanban_columns(store):
    ev = Events()
    p1 = ev.add_proposal("account-crm", "lead", "0a1b2c3d-0000-4000-8000-123456789abc", "status", "contattato", "il cliente ha risposto")
    p2 = ev.add_proposal("account-crm", "user", "herems@agent.com", "is_active", "false", "account creato per errore")
    p3 = ev.add_proposal("project-manager", "project", "0a1b2c3d-0000-4000-8000-123456789abd", "deadline", "2026-12-01", "il cliente ha chiesto uno slittamento")
    ev.decide_proposal(p3, True)
    cid = powers.propose(ev, "deployer", "netlify_preview", "vetreria-alfa", "Anteprima hero", {"files": {"index.html": "x"}})
    api_sync.sync_once(store, ev, services={"hermes": True})
    t = lambda k: store.get(f"tasks/{k}")                                                      # noqa: E731
    assert t(f"p{p1}")["estado"] == "en_espera" and t(f"p{p3}")["estado"] == "en_progreso" and t(f"c{cid}")["estado"] == "en_espera"
    assert t(f"p{p2}")["prioridad"] == "urgente" and t(f"p{p1}")["asignado"] == "account-crm" and t(f"p{p1}")["hlab"] == {"kind": "proposal", "id": p1}
    ev.decide_proposal(p1, False)
    ev.mark_proposal(p3, "applied")
    powers._set(ev, cid, "previewed", {"anteprima": "https://d1--vetreria-alfa.netlify.app"})
    api_sync.sync_once(store, ev, services={})
    assert t(f"p{p1}")["estado"] == "completada" and t(f"p{p1}")["feedback"] == "Annullata" and t(f"p{p3}")["estado"] == "completada"
    assert t(f"c{cid}")["estado"] == "completada" and t(f"c{cid}")["linkTerminada"].startswith("https://d1--vetreria-alfa")
    coo = store.get("agents/coo")
    assert coo["kind"] == "coordinador" and coo["active"] and store.get("agents/ops-uptime")["name"] == "Ops Uptime"
    hb = store.get("config/agentWorker")
    assert hb["online"] is True and isinstance(hb["lastSeen"]["__ts"], int)
    assert store.get("users/lorenzo")["role"] == "admin"
    n = len(store.query("tasks"))
    api_sync.sync_once(store, ev, services={})
    assert len(store.query("tasks")) == n                                                      # ripetibile senza duplicati


def test_moving_a_card_in_the_app_confirms_or_cancels_the_hlab_decision(store):
    import time
    ev = Events()
    p1 = ev.add_proposal("account-crm", "lead", "0a1b2c3d-0000-4000-8000-123456789abc", "status", "contattato", "il cliente ha risposto")
    p2 = ev.add_proposal("account-crm", "lead", "0a1b2c3d-0000-4000-8000-123456789abd", "status", "perso", "non risponde da mesi")
    api_sync.sync_once(store, ev, services={})
    calls = []
    api_sync.attach_hooks(store, decide_fn=lambda k, v, i: calls.append((k, v, i)) or f"fatto {k} {v} {i}", resync=lambda: None)
    store.update(f"tasks/p{p1}", {"estado": "completada"})                                   # trascinata su Completada = Conferma
    store.update(f"tasks/p{p2}", {"estado": "en_revision"})                                  # trascinata su Correcciones = Annulla
    for _ in range(50):
        if len(calls) == 2:
            break
        time.sleep(0.05)
    assert sorted(calls) == sorted([("hp", "ok", p1), ("hp", "no", p2)])
    time.sleep(0.2)
    assert store.get(f"tasks/p{p1}")["feedback"] == f"fatto hp ok {p1}"
    calls.clear()
    store.update(f"tasks/p{p1}", {"estado": "completada", "totalTimeSeconds": 5})            # modifica successiva: nessuna nuova decisione
    store.update(f"tasks/p{p1}", {"estado": "en_espera"})
    store.update(f"tasks/p{p1}", {"estado": "pendiente"})                                    # altri spostamenti: ignorati
    time.sleep(0.3)
    assert calls == []


def test_resolve_host_waits_for_tailscale_and_fails_clearly(monkeypatch):
    import subprocess
    assert api.resolve_host("127.0.0.1") == "127.0.0.1"
    answers = iter(["", "100.101.102.103\n"])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=next(answers), stderr=""))
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert api.resolve_host("tailscale", wait_s=9) == "100.101.102.103"                      # al secondo tentativo il collegamento e' pronto

    def boom(*a, **k):
        raise FileNotFoundError("tailscale")
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="Tailscale non e' collegato"):
        api.resolve_host("tailscale", wait_s=6)


def test_too_many_wrong_tokens_lock_the_address_and_headers_are_set(store, tmp_path, monkeypatch):
    monkeypatch.setenv("HLAB_TRUST_CF_IP", "1")
    c = TestClient(api.make_app(store, TOKEN, dist=tmp_path / "nodist"))
    h = {"cf-connecting-ip": "203.0.113.7"}
    for _ in range(api.FAIL_MAX):
        assert c.post("/api/login", json={"token": "no"}, headers=h).status_code == 401
    assert c.post("/api/login", json={"token": TOKEN}, headers=h).status_code == 429                 # bloccato anche col token giusto
    assert c.post("/api/db/query", json={"coll": "tasks"}, headers={**AUTH, **h}).status_code == 429
    other = {"cf-connecting-ip": "198.51.100.9"}
    assert c.post("/api/login", json={"token": TOKEN}, headers=other).status_code == 200              # un altro indirizzo non e' toccato
    r = c.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY" and r.headers["referrer-policy"] == "no-referrer"
    for _ in range(api.FAIL_MAX):                                                                    # le richieste senza token contano come tentativi
        c.post("/api/db/get", json={"path": "a/b"}, headers={"cf-connecting-ip": "192.0.2.5"})
    assert c.post("/api/db/get", json={"path": "a/b"}, headers={**AUTH, "cf-connecting-ip": "192.0.2.5"}).status_code == 429


# ---- indurimento dell'API pubblica ----

def make_client(store, tmp_path, with_dist=True):
    dist = tmp_path / "dist"
    if with_dist:
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
        (dist / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    return TestClient(api.make_app(store, TOKEN, dist=dist))


def test_every_response_carries_the_security_headers_and_api_data_is_never_cached(store, tmp_path):
    c = make_client(store, tmp_path)
    r = c.post("/api/db/query", json={"coll": "tasks"}, headers=AUTH)
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "connect-src 'self'" in csp and "object-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["strict-transport-security"].startswith("max-age=") and r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store" and "camera=()" in r.headers["permissions-policy"]


def test_index_is_always_revalidated_hashed_assets_are_cached_and_a_missing_asset_is_a_404(store, tmp_path):
    c = make_client(store, tmp_path)
    assert c.get("/").headers["cache-control"] == "no-cache"
    assert c.get("/pipeline").headers["cache-control"] == "no-cache" and "app" in c.get("/pipeline").text      # rotta dell'app: sempre l'index
    a = c.get("/assets/app-abc123.js")
    assert a.status_code == 200 and "immutable" in a.headers["cache-control"]
    assert c.get("/assets/vecchio-9999.js").status_code == 404                                                  # niente HTML travestito da script
    assert c.get("/%2e%2e/%2e%2e/etc/passwd").status_code in (200, 404) and "root:" not in c.get("/%2e%2e/%2e%2e/etc/passwd").text


def test_malformed_or_oversized_requests_are_client_errors_not_server_errors(store, tmp_path):
    c = make_client(store, tmp_path)
    assert c.post("/api/login", content=b"{non json", headers={"content-type": "application/json"}).status_code == 400
    assert c.post("/api/login", json=["non", "un", "oggetto"]).status_code == 400
    assert c.post("/api/login", json={"token": "x" * 5000}).status_code == 413                                  # corpo oltre il limite del login
    assert c.post("/api/db/query", content=b"[1,2]", headers={**AUTH, "content-type": "application/json"}).status_code == 400
    big = c.post("/api/db/write", content=b"x" * 10, headers={**AUTH, "content-length": str(api.MAX_BODY + 1), "content-type": "application/json"})
    assert big.status_code in (413, 400)
    assert c.post("/api/login", json={"token": TOKEN}).status_code == 200                                       # il login vero funziona ancora


def test_lockout_is_logged_once_and_the_failure_table_does_not_grow_forever(store, tmp_path, capsys):
    c = make_client(store, tmp_path)
    for _ in range(api.FAIL_MAX):
        c.post("/api/login", json={"token": "sbagliato"})
    assert c.post("/api/login", json={"token": TOKEN}).status_code == 429
    out = capsys.readouterr().out
    assert out.count("indirizzo bloccato") == 1 and "token errati" in out


def test_the_lockout_is_per_visitor_behind_cloudflare_or_a_forwarding_proxy(store, tmp_path, monkeypatch):
    monkeypatch.setenv("HLAB_TRUST_CF_IP", "1")
    c = make_client(store, tmp_path)
    for _ in range(api.FAIL_MAX):                                              # un estraneo dietro il proxy sbaglia il token 10 volte
        c.post("/api/login", json={"token": "no"}, headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"})
    assert c.post("/api/login", json={"token": TOKEN}, headers={"x-forwarded-for": "203.0.113.9"}).status_code == 429      # lui e' bloccato
    assert c.post("/api/login", json={"token": TOKEN}, headers={"x-forwarded-for": "198.51.100.7"}).status_code == 200      # tu no
    assert c.post("/api/login", json={"token": TOKEN}, headers={"cf-connecting-ip": "198.51.100.8", "x-forwarded-for": "203.0.113.9"}).status_code == 200   # Cloudflare ha la precedenza


def test_a_forged_cf_connecting_ip_cannot_dodge_the_lockout_unless_cloudflare_is_trusted(store, tmp_path, monkeypatch):
    monkeypatch.delenv("HLAB_TRUST_CF_IP", raising=False)
    c = make_client(store, tmp_path)
    codes = [c.post("/api/login", json={"token": "no"}, headers={"cf-connecting-ip": f"10.0.0.{i}"}).status_code for i in range(api.FAIL_MAX + 2)]
    assert codes[-1] == 429 and 401 in codes                          # l'header inventato non cambia l'indirizzo: dopo 10 errori si e' bloccati


def test_a_null_byte_in_the_path_is_a_clean_404_not_a_500(store, tmp_path):
    dist = tmp_path / "dist"; dist.mkdir(); (dist / "index.html").write_text("<html>app</html>")
    c = TestClient(api.make_app(store, TOKEN, dist=dist))
    assert c.get("/index.html%00.txt").status_code == 404
    assert c.get("/").status_code == 200
