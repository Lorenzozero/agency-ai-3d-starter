import json

from hlab import push_vita


class FakeDb:
    def __init__(self):
        self.sql = []

    def query(self, sql, params=()):
        self.sql.append(sql)
        if "FROM clients" in sql and "JOIN" not in sql:
            return [{"name": "Officina Rapida"}, {"name": "Bottega"}]
        if "FROM projects" in sql:
            return [{"name": "Sito QK", "client": "Officina Rapida", "status": "in_sviluppo", "is_online": False, "deadline": "2026-11-15"},
                    {"name": "Sito G", "client": "Bottega", "status": "in_sviluppo", "is_online": True, "deadline": None}]
        if "FROM leads" in sql:
            return [{"status": "nuovo", "n": 17}, {"status": "qualificato", "n": 10}, {"status": "convertito", "n": 3}, {"status": "perso", "n": 9}]
        if "FROM tasks" in sql:
            return [{"n": 2}]
        return []


def test_snapshot_has_only_safe_fields():
    db = FakeDb()
    doc = push_vita.build_snapshot(db)
    assert doc["leads"] == {"nuovi": 17, "qualificati": 10, "convertiti": 3}
    assert doc["projects"][0] == {"name": "Sito QK", "client": "Officina Rapida", "status": "in_sviluppo", "online": False, "due": "2026-11-15"}
    assert doc["projects"][1]["due"] is None
    assert [c["name"] for c in doc["clients"]] == ["Officina Rapida", "Bottega"]
    assert all(s.lstrip().lower().startswith("select") for s in db.sql), "solo SELECT"
    flat = str(doc).lower()
    for forbidden in ("email", "phone", "vat", "password", "contact"):
        assert forbidden not in flat


def test_no_url_or_key_sends_nothing(monkeypatch, capsys):
    monkeypatch.delenv("HLAB_VITA_PUSH_URL", raising=False)
    monkeypatch.delenv("HLAB_VITA_PUSH_KEY", raising=False)
    called = []
    monkeypatch.setattr(push_vita, "send", lambda *a, **k: called.append(a))
    assert push_vita.main([], db=FakeDb()) == 2
    assert called == []


def test_http_url_is_refused(monkeypatch):
    monkeypatch.setenv("HLAB_VITA_PUSH_URL", "http://esempio/v1/push/crm")
    monkeypatch.setenv("HLAB_VITA_PUSH_KEY", "k")
    assert push_vita.main([], db=FakeDb()) == 2


def test_sends_with_key_and_never_prints_it(monkeypatch, capsys):
    monkeypatch.setenv("HLAB_VITA_PUSH_URL", "https://esempio/v1/push/crm")
    monkeypatch.setenv("HLAB_VITA_PUSH_KEY", "segretissima")
    sent = {}
    monkeypatch.setattr(push_vita, "send", lambda url, key, doc, timeout=15: sent.update(url=url, key=key, n=len(doc["projects"])) or 200)
    assert push_vita.main([], db=FakeDb()) == 0
    assert sent == {"url": "https://esempio/v1/push/crm", "key": "segretissima", "n": 2}
    assert "segretissima" not in capsys.readouterr().out


def test_dry_run_prints_without_sending(monkeypatch, capsys):
    monkeypatch.setattr(push_vita, "send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("non deve inviare")))
    assert push_vita.main(["--dry"], db=FakeDb()) == 0
    assert '"nuovi": 17' in capsys.readouterr().out


def test_costs_today_and_month_from_router_log(tmp_path):
    import datetime as dt
    now = dt.datetime(2026, 11, 6, 12, tzinfo=dt.timezone.utc).timestamp()
    f = tmp_path / "router.jsonl"
    rows = [{"ts": now - 3600, "cost": 0.01}, {"ts": now - 86400 * 2, "cost": 0.2}, {"ts": now - 86400 * 40, "cost": 9.0}]
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\nrotta{\n")
    assert push_vita.read_costs(f, now) == {"today_usd": 0.01, "month_usd": 0.21}
    assert push_vita.read_costs(tmp_path / "manca.jsonl", now) is None


def test_factory_and_pending_are_read_only(tmp_path):
    import sqlite3
    j = tmp_path / "jobs.db"
    c = sqlite3.connect(j)
    c.execute("CREATE TABLE jobs(id INTEGER PRIMARY KEY, kind TEXT, status TEXT)")
    c.executemany("INSERT INTO jobs(kind,status) VALUES(?,?)", [("site_build", "running"), ("heal", "done"), ("quote", "queued")])
    c.commit(); c.close()
    assert push_vita.read_factory(str(j)) == {"running": [{"site": "quote", "stage": "queued"}, {"site": "site_build", "stage": "running"}]}
    assert push_vita.read_factory(str(tmp_path / "no.db")) is None
    e = tmp_path / "events.db"
    c = sqlite3.connect(e)
    c.execute("CREATE TABLE proposals(id INTEGER PRIMARY KEY, status TEXT)")
    c.execute("CREATE TABLE drafts(id INTEGER PRIMARY KEY, status TEXT)")
    c.executemany("INSERT INTO proposals(status) VALUES(?)", [("pending",), ("accepted",)])
    c.execute("INSERT INTO drafts(status) VALUES('draft')")
    c.commit(); c.close()
    assert push_vita.read_pending(str(e)) == 2
    assert push_vita.read_pending(str(tmp_path / "no.db")) is None


class FinDb:
    def query(self, sql, params=()):
        if "quote_items" in sql:
            return [{"id": "p1", "name": "Officina Rapida", "currency": "USD", "quote_items": [{"priceCents": 18000, "monthly": False}, {"priceCents": 8500, "monthly": False}, {"priceCents": 2000, "monthly": True}]},
                    {"id": "p2", "name": "Bottega", "currency": "EUR", "quote_items": []}]
        if "FROM payments" in sql:
            return [{"pid": "p1", "status": "pagato", "currency": "USD", "amount_cents": 13250, "paid_at": "2026-10-20T10:00:00"},
                    {"pid": "p1", "status": "in_attesa", "currency": "USD", "amount_cents": 13250, "paid_at": None},
                    {"pid": "p2", "status": "pagato", "currency": "EUR", "amount_cents": 40000, "paid_at": "2026-11-02T09:00:00"},
                    {"pid": "p2", "status": "fallito", "currency": "EUR", "amount_cents": 999, "paid_at": None}]
        if "FROM invoices" in sql:
            return [{"status": "emessa", "currency": "EUR", "n": 1, "cents": 40000}]
        return []


def test_finance_per_project_and_currency_never_converted():
    f = push_vita.build_finance(FinDb())
    qk = next(p for p in f["projects"] if p["project"] == "Officina Rapida")
    assert (qk["quote_cents"], qk["monthly_cents"], qk["paid_cents"], qk["pending_cents"], qk["outstanding_cents"]) == (26500, 2000, 13250, 13250, 13250)
    assert f["paid_by_currency"] == {"USD": 13250, "EUR": 40000}, "valute separate, mai sommate"
    assert f["pending_by_currency"] == {"USD": 13250}, "il pagamento fallito non conta come in attesa"
    assert f["outstanding_by_currency"] == {"USD": 13250, "EUR": 0}
    assert f["monthly_paid"] == {"2026-10": {"USD": 13250}, "2026-11": {"EUR": 40000}}
    assert f["invoices"][0]["n"] == 1


def test_finance_has_no_contact_data():
    flat = str(push_vita.build_finance(FinDb())).lower()
    for forbidden in ("email", "phone", "vat", "password", "iban"):
        assert forbidden not in flat


def test_ledger_add_and_read_by_site(tmp_path):
    from hlab import ledger
    p = tmp_path / "ledger.jsonl"
    ledger.add("Officina Rapida", "tripo", 0.35, "modello 3D", p=p, now=1000)
    ledger.add("Officina Rapida", "netlify", 0.15, p=p, now=1001)
    ledger.add("Bottega", "tripo", 0.5, p=p, now=1002)
    r = push_vita.read_ledger(p)
    assert [(s["site"], s["usd"]) for s in r["sites"]] == [("Bottega", 0.5), ("Officina Rapida", 0.5)]
    assert r["sites"][1]["kinds"] == {"tripo": 0.35, "netlify": 0.15}
    for bad in (("", "x", 1), ("a", "", 1), ("a", "x", -1), ("a", "x", 99999)):
        try:
            ledger.add(*bad, p=p)
            assert False, bad
        except ValueError:
            pass
    assert push_vita.read_ledger(tmp_path / "manca.jsonl") is None


def test_signature_is_hmac_bound_to_time_name_and_body():
    import hashlib
    import hmac
    sig = push_vita.sign("k", 1000, "crm", b'{"a":1}')
    assert sig == hmac.new(b"k", b'1000.crm.{"a":1}', hashlib.sha256).hexdigest()
    assert sig != push_vita.sign("k", 1001, "crm", b'{"a":1}')
    assert sig != push_vita.sign("k", 1000, "altro", b'{"a":1}')
    assert sig != push_vita.sign("k", 1000, "crm", b'{"a":2}')
    assert sig != push_vita.sign("altra", 1000, "crm", b'{"a":1}')


def test_send_never_puts_the_key_on_the_wire(monkeypatch):
    seen = {}

    class R:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout=0):
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["body"] = req.data
        return R()

    monkeypatch.setattr(push_vita.urllib.request, "urlopen", fake_open)
    assert push_vita.send("https://esempio/v1/push/crm", "segretissima", {"x": 1}) == 200
    h = seen["headers"]
    assert "x-vita-key" not in h and "segretissima" not in str(h) and "segretissima" not in str(seen["body"])
    assert h["x-vita-ts"].isdigit() and len(h["x-vita-sig"]) == 64


def test_finance_survives_missing_invoice_privilege():
    class NoInv(FinDb):
        def query(self, sql, params=()):
            if "FROM invoices" in sql:
                raise PermissionError("InsufficientPrivilege")
            return super().query(sql, params)
    f = push_vita.build_finance(NoInv())
    assert f["invoices"] == [] and f["paid_by_currency"] == {"USD": 13250, "EUR": 40000}


def _load_hermes_ledger(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    monkeypatch.setenv("HLAB_LEDGER", str(tmp_path / "ledger.jsonl"))
    spec = importlib.util.spec_from_file_location("hermes_ledger", Path(__file__).resolve().parent.parent / "deploy" / "hermes_ledger.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_record_build_writes_llm_images_and_deploy_with_estimates(tmp_path, monkeypatch):
    hl = _load_hermes_ledger(tmp_path, monkeypatch)
    spec = {"repo_name": "quick-key", "images": [{"provider": "gemini"}, {"provider": "gemini"}, {"provider": "pexels"}, {"provider": "client"}, {"provider": "reuse"}, {}]}
    hl.record_build(spec, tok=100_000, title="Deploy completato")
    r = push_vita.read_ledger(tmp_path / "ledger.jsonl")
    s = r["sites"][0]
    assert s["site"] == "quick-key" and s["usd"] == 0.0, "niente è stato pagato davvero"
    assert abs(s["est_usd"] - (100_000 / 1e6 * 0.35 + 2 * 0.04)) < 1e-6, s
    assert s["units"]["tokens"] == 100_000 and s["units"]["images"] == 3 and s["units"]["deploys"] == 1, "client e reuse non contano"
    assert set(s["kinds"]) == {"llm", "immagini:gemini", "immagini:pexels", "deploy"}
    assert r["recent"][0]["site"] == "quick-key"


def test_record_build_never_raises_and_rates_can_be_overridden(tmp_path, monkeypatch):
    hl = _load_hermes_ledger(tmp_path, monkeypatch)
    hl.record_build(None, 5)                      # spec rotto: nessuna eccezione
    hl.record_build({}, 5)                        # senza nome del sito: nulla da scrivere
    assert not (tmp_path / "ledger.jsonl").exists()
    (tmp_path / "ledger_rates.json").write_text('{"llm_usd_per_mtok": 1.0, "image_usd": {"pexels": 0.5}}')
    hl.record_build({"repo_name": "x", "images": [{"provider": "pexels"}]}, tok=1_000_000)
    s = push_vita.read_ledger(tmp_path / "ledger.jsonl")["sites"][0]
    assert abs(s["est_usd"] - 1.5) < 1e-6


def test_factory_job_costs_are_attributed_by_time_window(tmp_path):
    import sqlite3
    j = tmp_path / "jobs.db"
    c = sqlite3.connect(j)
    c.execute("CREATE TABLE jobs(id INTEGER PRIMARY KEY, kind TEXT, payload TEXT, created REAL, updated REAL)")
    c.execute("INSERT INTO jobs(kind,payload,created,updated) VALUES('site_factory','{\"slug\":\"dentista\"}',1000,1100)")
    c.execute("INSERT INTO jobs(kind,payload,created,updated) VALUES('sentinel_review','{\"day\":\"x\"}',1000,1100)")
    c.commit(); c.close()
    log = tmp_path / "router.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in [{"ts": 1050, "cost": 0.02}, {"ts": 1090, "cost": 0.03}, {"ts": 2000, "cost": 9.0}, {"ts": 1060, "cost": 0.0}]) + "\n")
    r = push_vita.read_ledger(tmp_path / "manca.jsonl", str(j), log)
    assert [(s["site"], s["usd"], s["est_usd"]) for s in r["sites"]] == [("dentista", 0.05, 0.0)]
    assert push_vita.read_ledger(tmp_path / "manca.jsonl", str(tmp_path / "no.db"), tmp_path / "no.jsonl") is None
