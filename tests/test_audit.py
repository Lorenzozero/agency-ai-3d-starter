import asyncio

import httpx
import pytest

from hlab import audit

GOOD = """<!doctype html><html lang="it"><head><title>Agency AI: siti web per piccole imprese</title>
<meta name="description" content="Realizziamo siti web veloci e curati per piccole imprese italiane, con assistenza dopo il lancio e prezzi chiari fin dall'inizio.">
<meta name="viewport" content="width=device-width, initial-scale=1"><link rel="canonical" href="https://sito.test/">
<meta property="og:title" content="CF"><script type="application/ld+json">{"@type": "Organization"}</script>
<link rel="stylesheet" href="/a.css"><script src="/app.js" defer></script></head>
<body><h1>Siti web che funzionano</h1><p>%s</p><a href="/chi-siamo">Chi siamo</a><a href="/rotto">Rotto</a>
<img src="/a.png" alt="logo" width="10" height="10"></body></html>""" % ("parola " * 200)


def site(routes, default=404):
    """Un finto sito: {percorso: (stato, corpo, intestazioni)}."""
    def handler(req: httpx.Request):
        key = req.url.path
        if key in routes:
            status, body, headers = routes[key]
            return httpx.Response(status, content=body if isinstance(body, bytes) else body.encode(), headers=headers)
        return httpx.Response(default, content=b"non trovato")
    return httpx.MockTransport(handler)


HTML = {"content-type": "text/html; charset=utf-8"}
TLS_OK = lambda host: {"versione": "TLSv1.3", "cifrario": "TLS_AES_256_GCM_SHA384", "http2": True, "giorni": 60, "emittente": "Let's Encrypt"}  # noqa: E731


def codes(res):
    return {i["codice"] for i in res["problemi"]}


def test_page_parser_reads_what_seo_and_speed_need():
    p = audit.parse(GOOD)
    assert p.title.startswith("Agency AI") and p.lang == "it" and p.canonical == "https://sito.test/"
    assert p.meta["viewport"].startswith("width=") and p.meta["og:title"] == "CF" and len(p.jsonld) == 1
    assert p.h["h1"] == ["Siti web che funzionano"] and len(p.imgs) == 1 and p.words >= 200 and p.links == ["/chi-siamo", "/rotto"]
    assert p.scripts[0]["head"] is True and "defer" in p.scripts[0]
    assert audit.parse("<html><title>rotto<div><<<").title.startswith("rotto")                    # HTML rotto: niente eccezioni


def test_seo_of_a_good_page_is_high_and_finds_broken_internal_links_and_missing_files():
    t = site({"/": (200, GOOD, HTML), "/chi-siamo": (200, GOOD, HTML), "/rotto": (404, "no", HTML)})
    res = asyncio.run(audit.seo("https://sito.test/", t))
    assert {"link_rotti", "robots", "sitemap"} <= codes(res) and "title_mancante" not in codes(res) and "h1_mancante" not in codes(res)
    broken = next(i for i in res["problemi"] if i["codice"] == "link_rotti")
    assert "/rotto -> 404" in broken["messaggio"] and broken["gravita"] == "alta"
    assert res["problemi"][0]["gravita"] == "alta" and res["voto"] in "ABCDF" and res["pagine_controllate"] == 1


def test_seo_flags_noindex_blocked_robots_missing_basics_and_duplicates_across_the_sitemap():
    bad = '<html><head><title>x</title><meta name="robots" content="noindex"></head><body><p>poco</p><img src="a.png"></body></html>'
    sm = "<urlset><url><loc>https://sito.test/uno</loc></url><url><loc>https://sito.test/due</loc></url></urlset>"
    dup = GOOD.replace("Siti web che funzionano", "Altro")
    t = site({"/": (200, bad, HTML), "/robots.txt": (200, "User-agent: *\nDisallow: /\nSitemap: https://sito.test/sitemap.xml", {"content-type": "text/plain"}),
              "/sitemap.xml": (200, sm, {"content-type": "application/xml"}), "/uno": (200, dup, HTML), "/due": (200, dup, HTML)})
    res = asyncio.run(audit.seo("https://sito.test/", t))
    assert {"noindex", "robots_blocca_tutto", "title_lunghezza", "description_mancante", "h1_mancante", "viewport", "lang", "alt", "poco_testo"} <= codes(res)
    assert {"titolo_duplicato", "description_duplicata"} <= codes(res) and res["pagine_controllate"] == 3 and res["punteggio"] < 20 and res["voto"] == "F"


def test_security_finds_missing_headers_weak_cookies_and_real_exposures_but_not_a_soft_404():
    html = {**HTML, "set-cookie": "sid=abc; Path=/", "server": "nginx/1.18.0"}
    t = site({"/": (200, GOOD, html), "/.env": (200, "DB_PASSWORD=segreto\nAPI_KEY=xyz", {"content-type": "text/plain"}), "/.git/HEAD": (200, "ref: refs/heads/main\n", {})})
    res = asyncio.run(audit.security("https://sito.test/", t, tls=TLS_OK))
    c = codes(res)
    assert {"hsts", "csp", "nosniff", "clickjacking", "cookie", "versione_esposta", "esposto_.env", "esposto_.git_HEAD"} <= c
    assert res["voto"] == "F" and set(res["esposizioni"]) == {"/.env", "/.git/HEAD"} and res["problemi"][0]["gravita"] == "alta"
    assert "passiv" in res["nota"].lower()
    spa = site({"/": (200, GOOD, HTML), "/.env": (200, GOOD, HTML), "/.git/HEAD": (200, GOOD, HTML), "/.DS_Store": (200, GOOD, HTML)})   # un'app che risponde 200 a tutto
    res2 = asyncio.run(audit.security("https://sito.test/", spa, tls=TLS_OK))
    assert res2["esposizioni"] == [] and not [i for i in res2["problemi"] if i["codice"].startswith("esposto")]


def test_security_rewards_a_well_configured_site_and_checks_tls_and_cors():
    good = {**HTML, "strict-transport-security": "max-age=31536000", "content-security-policy": "default-src 'self'; frame-ancestors 'none'", "x-content-type-options": "nosniff",
            "referrer-policy": "no-referrer", "permissions-policy": "camera=()", "set-cookie": "sid=1; Secure; HttpOnly; SameSite=Lax"}
    def handler(req):
        if req.url.scheme == "http":
            return httpx.Response(301, headers={"location": "https://sito.test/"})
        return httpx.Response(200, content=GOOD.encode(), headers=good) if req.url.path == "/" else httpx.Response(404)
    res = asyncio.run(audit.security("https://sito.test/", httpx.MockTransport(handler), tls=TLS_OK))
    assert res["voto"] == "A" and res["punteggio"] >= 90 and "redirect_https" not in codes(res) and res["controlli"]["http_verso_https"].startswith("301")
    old = lambda host: {"versione": "TLSv1", "cifrario": "x", "http2": False, "giorni": 5, "emittente": "x"}  # noqa: E731
    res2 = asyncio.run(audit.security("https://sito.test/", httpx.MockTransport(handler), tls=old))
    assert {"tls_vecchio", "certificato_in_scadenza"} <= codes(res2)
    bad = lambda host: {"errore_certificato": "certificate has expired"}  # noqa: E731
    assert "certificato" in codes(asyncio.run(audit.security("https://sito.test/", httpx.MockTransport(handler), tls=bad)))

    def cors(req):
        h = dict(good)
        if req.headers.get("origin"):
            h.update({"access-control-allow-origin": req.headers["origin"], "access-control-allow-credentials": "true"})
        return httpx.Response(200, content=GOOD.encode(), headers=h)
    assert "cors" in codes(asyncio.run(audit.security("https://sito.test/", httpx.MockTransport(cors), tls=TLS_OK)))


def test_security_on_http_only_and_unreachable_sites():
    t = site({"/": (200, GOOD, HTML)})
    res = asyncio.run(audit.security("http://sito.test/", t, tls=TLS_OK))
    assert "no_https" in codes(res) and "hsts" not in codes(res)                                   # senza https l'HSTS non si chiede
    def boom(req):
        raise httpx.ConnectError("rifiutata")
    res = asyncio.run(audit.security("https://giu.test/", httpx.MockTransport(boom), tls=TLS_OK))
    assert "non raggiungibile" in res["errore"] and "problemi" not in res


def test_performance_measures_the_network_and_reads_lighthouse_but_survives_its_failure():
    big = GOOD + "x" * 30_000
    psi_json = {"lighthouseResult": {"categories": {"performance": {"score": 0.52}, "seo": {"score": 0.9}},
                                     "audits": {"largest-contentful-paint": {"displayValue": "4.8 s"}, "cumulative-layout-shift": {"displayValue": "0.31"},
                                                "unused-javascript": {"title": "Riduci il JavaScript inutilizzato", "details": {"type": "opportunity", "overallSavingsMs": 1200}}}},
                "loadingExperience": {"overall_category": "SLOW"}}

    def handler(req):
        if req.url.host == "pagespeedonline.googleapis.com":
            return httpx.Response(200, json=psi_json)
        return httpx.Response(200, content=big.encode(), headers=HTML)
    res = asyncio.run(audit.perf("https://sito.test/", transport=httpx.MockTransport(handler), tls=lambda h: {"http2": False}))
    assert {"compressione", "http2"} <= codes(res) and res["misure"]["byte"] > 30_000 and res["misure"]["ttfb_ms"] >= 0
    lh = res["lighthouse"]
    assert lh["punteggi"]["performance"] == 52 and lh["metriche"]["LCP"] == "4.8 s" and lh["esperienza_reale_utenti"] == "SLOW" and "JavaScript inutilizzato" in lh["occasioni"][0]
    limited = lambda req: httpx.Response(429) if req.url.host == "pagespeedonline.googleapis.com" else httpx.Response(200, content=GOOD.encode(), headers=HTML)    # noqa: E731
    res2 = asyncio.run(audit.perf("https://sito.test/", transport=httpx.MockTransport(limited), tls=lambda h: {"http2": True}))
    assert "quota gratuita" in res2["lighthouse"]["errore"] and "PAGESPEED_API_KEY" in res2["lighthouse"]["errore"] and res2["misure"]["byte"] > 0         # PageSpeed ko: restano le misure proprie


def test_slow_server_and_render_blocking_scripts_are_reported_by_severity():
    m = {"ttfb_ms": 2500, "totale_ms": 3000, "byte": 400_000, "redirect": 3, "codifica": "gzip"}
    page = audit.parse("<html><head><script src='a.js'></script><script src='b.js'></script></head><body>" + "<img src='x.png'>" * 8 + "</body></html>")
    got = {i["codice"]: i["gravita"] for i in audit.perf_issues(m, page, {"http2": False})}
    assert got["ttfb"] == "alta" and got["redirect"] == "media" and got["script_bloccanti"] == "media" and got["immagini_lazy"] == "media" and got["peso_html"] == "media"
    assert audit.score([]) == 100 and audit.score([{"gravita": "alta"}] * 10) == 0 and audit.grade(95) == "A" and audit.grade(30) == "F"


def test_only_registered_sites_can_be_analysed_and_staging_addresses_count():
    class Db:
        def query(self, sql, *a):
            if "staging_url" in sql:
                return [{"name": "Officina Rapida", "staging_url": "https://qk-staging.example.app", "target_url": ""},
                        {"name": "Pizzeria", "staging_url": "", "target_url": "https://pizzeria.example"},
                        {"name": "Rotto", "staging_url": "ftp://x", "target_url": "javascript:alert(1)"}]
            return []
    got = audit.audit_targets(Db(), {"uptime": {"urls": {"Sito extra": "https://extra.example"}}})
    assert got["Officina Rapida (staging)"] == "https://qk-staging.example.app" and got["Pizzeria"] == "https://pizzeria.example" and got["Sito extra"] == "https://extra.example"
    assert "Rotto" not in got and "Rotto (staging)" not in got                                       # solo http/https


def test_tools_refuse_unknown_addresses_and_the_three_agents_have_exactly_the_audit_scope():
    pytest.importorskip("claude_agent_sdk")
    from hlab import config, policy, registry, scopes, tools
    from hlab.events import Events
    from hlab.tools import audit_tools
    ts = {t.name: t for t in audit_tools.make_tools(lambda: {"Officina Rapida": "https://qk.example"})}
    assert set(ts) == set(audit_tools.NAMES)
    r = asyncio.run(ts["security_check"].handler({"name": "https://sito-a-caso.it"}))
    assert r.get("is_error") and "non e' un sito registrato" in r["content"][0]["text"] and "Officina Rapida" in r["content"][0]["text"]
    specs = registry.load_all()
    assert registry.validate(specs) == []
    for name in ("performance", "seo", "sicurezza"):
        spec = specs[name]
        assert "mcp__audit__security_check" in tools.tool_names(spec) and "audit" in scopes.agent_scopes(spec) and spec.autonomy == 2 and spec.builtin == []
    assert "audit" not in scopes.agent_scopes(specs["ops-uptime"]) and "mcp__audit__perf_check" not in tools.tool_names(specs["ops-uptime"])
    ev = Events(config.DATA_DIR / "e.db")
    assert policy.decide(specs["seo"], "mcp__audit__seo_check", {"name": "Officina Rapida"}, ev)[0] == "allow"
    assert policy.decide(specs["ops-uptime"], "mcp__audit__seo_check", {"name": "Officina Rapida"}, ev)[0] == "deny"
    assert tools.risk_of("mcp__audit__perf_check") == "read" and tools.risk_of("mcp__audit__cancella") == "external"


def test_only_encodings_we_can_decode_are_requested_and_a_404_home_is_called_by_its_name():
    seen = []

    def handler(req):
        seen.append(req.headers.get("accept-encoding"))
        return httpx.Response(404, content=b"The deployment could not be found", headers={"content-type": "text/plain"})
    for fn in (audit.security, audit.seo, audit.perf):
        kw = {"tls": TLS_OK} if fn is not audit.seo else {}
        res = asyncio.run(fn("https://sito.test/", httpx.MockTransport(handler), **kw) if fn is audit.seo else fn("https://sito.test/", transport=httpx.MockTransport(handler), **kw))
        assert res["problemi"][0]["codice"] == "pagina_non_disponibile" and "404" in res["problemi"][0]["messaggio"]
    assert seen and all(("br" in e) == bool(__import__("importlib").util.find_spec("brotli")) for e in seen)
    p = audit.status_issue(403)
    assert p["codice"] == "protetto" and audit.status_issue(200) is None and audit.status_issue(503)["gravita"] == "alta"


def test_a_broken_sitemap_address_in_robots_is_named_and_the_default_sitemap_is_still_used():
    sm = "<urlset><url><loc>https://sito.test/uno</loc></url></urlset>"

    def handler(req):
        if req.url.host == "produzione.example":
            raise httpx.ConnectError("dominio non raggiungibile")
        routes = {"/": (200, GOOD, HTML), "/robots.txt": (200, "User-agent: *\nAllow: /\nSitemap: https://produzione.example/sitemap.xml", {"content-type": "text/plain"}),
                  "/sitemap.xml": (200, sm, {"content-type": "application/xml"}), "/uno": (200, GOOD, HTML)}
        st, body, h = routes.get(req.url.path, (404, "no", HTML))
        return httpx.Response(st, content=body.encode(), headers=h)
    res = asyncio.run(audit.seo("https://sito.test/", httpx.MockTransport(handler)))
    msg = next(i for i in res["problemi"] if i["codice"] == "sitemap_robots")["messaggio"]
    assert "produzione.example/sitemap.xml" in msg and "sitemap" not in codes(res) and res["pagine_controllate"] == 2     # la sitemap vera e' stata usata


def test_security_flags_third_party_scripts_without_sri_but_not_own_or_protected_ones():
    page = ('<html lang="it"><head><title>t</title><script src="https://cdn.altro.test/lib.js"></script>'
            '<script src="https://cdn.sicuro.test/b.js" integrity="sha384-abc" crossorigin></script><script src="/mio.js"></script></head><body><h1>x</h1></body></html>')
    res = asyncio.run(audit.security("https://sito.test/", site({"/": (200, page, HTML)}), tls=TLS_OK))
    sri = next(i for i in res["problemi"] if i["codice"] == "script_senza_sri")
    assert "cdn.altro.test" in sri["messaggio"] and "cdn.sicuro.test" not in sri["messaggio"] and sri["messaggio"].startswith("1 script")


def test_full_audit_summarises_the_three_areas_and_orders_priorities():
    t = site({"/": (200, GOOD, HTML), "/chi-siamo": (200, GOOD, HTML), "/rotto": (404, "no", HTML)})
    res = asyncio.run(audit.full("https://sito.test/", transport=t, psi_transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert set(res["riepilogo"]) == {"sicurezza", "seo", "prestazioni"} and res["priorita"]
    order = [{"alta": 0, "media": 1, "bassa": 2}[i["gravita"]] for i in res["priorita"]]
    assert order == sorted(order) and {"prestazioni", "seo", "sicurezza"} <= set(res)


def test_site_profile_reads_contacts_social_colors_fonts_and_tech():
    page = ('<html lang="it"><head><title>Da Mario</title><meta name="description" content="Pizzeria"><meta name="theme-color" content="#aa0000">'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Lora:wght@400&display=swap"><link rel="icon" href="/f.png">'
            '<meta property="og:image" content="/og.jpg"><style>a{color:#112233}b{color:#112233}</style></head>'
            '<body><a href="tel:+39 333 1234567">chiama</a><a href="mailto:info@mario.test">info@mario.test</a>'
            '<a href="https://www.instagram.com/mario?x=1">ig</a><img src="/wp-content/a.png"></body></html>')
    res = asyncio.run(audit.profile("https://sito.test/", site({"/": (200, page, HTML)})))
    assert res["email"] == ["info@mario.test"] and res["telefoni"] == ["+393331234567"] and res["social"] == ["https://www.instagram.com/mario"]
    assert res["colori"][0] == "#aa0000" and "#112233" in res["colori"] and res["caratteri"] == ["Lora"] and "WordPress" in res["tecnologia"]
    assert res["logo_o_immagine"] == "https://sito.test/og.jpg" and res["lingua"] == "it"
    gone = asyncio.run(audit.profile("https://sito.test/", site({})))
    assert gone["stato_http"] == 404
