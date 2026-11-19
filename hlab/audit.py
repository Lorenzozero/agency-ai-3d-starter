"""Analisi tecnica dei siti dei clienti: prestazioni, SEO, sicurezza. Solo richieste GET/HEAD in lettura, 0 token (i risultati li interpreta l'agente).

Ispirate ai criteri dei progetti open source piu' usati (verificati su GitHub il 2026-11-05):
  prestazioni  Lighthouse (Apache-2.0, 31k stelle) tramite l'API gratuita PageSpeed Insights + misure proprie (tempo al primo byte, compressione, cache, HTTP/2);
               per analisi piu' profonde, a mano: sitespeed.io (MIT), Lighthouse CI, WebPageTest.
  SEO          controlli on-page come python-seo-analyzer e SEOnaut (MIT), link rotti come linkinator (MIT).
  sicurezza    intestazioni e cookie secondo l'OWASP Secure Headers Project (Apache-2.0), certificato e protocollo TLS, esposizioni note (.git, .env).
               Mozilla HTTP Observatory e' archiviato dal 2024: i suoi criteri sono rifatti qui.
NON esegue scanner attivi (nuclei, OWASP ZAP, nikto, wapiti): lanciarli su un sito richiede un'autorizzazione e sono lavoro di una persona.

Regole: solo siti GIA' registrati (progetti del CRM, siti in quotas.yaml); al massimo ~25 richieste per analisi; mai POST, mai prove di password o di iniezione.
"""
from __future__ import annotations

import asyncio
import re
import socket
import ssl
import time
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

UA = "hlab-audit/1 (controllo tecnico interno dell'agenzia)"
PSI = "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed"   # indirizzo ufficiale; senza chiave la quota giornaliera condivisa e' di norma esaurita


def _encodings() -> str:
    """Si chiede solo cio' che si sa decomprimere: con Brotli non leggibile l'HTML arriverebbe come dati illeggibili e ogni controllo darebbe falsi "manca tutto"."""
    try:
        import brotli  # noqa: F401
        return "gzip, br"
    except ImportError:
        return "gzip"
WEIGHT = {"alta": 15, "media": 7, "bassa": 3}


def issue(gravita: str, codice: str, messaggio: str, rimedio: str = "") -> dict:
    return {"gravita": gravita, "codice": codice, "messaggio": messaggio, **({"come_risolvere": rimedio} if rimedio else {})}


def status_issue(status: int) -> dict | None:
    """Una pagina principale che non risponde 200 falsa tutto il resto: l'analisi riguarderebbe la pagina d'errore. Va detto per primo."""
    if status in (401, 403):
        return issue("media", "protetto", f"La pagina risponde {status}: il sito chiede un accesso (staging con password?). Le misure riguardano la pagina di accesso, non il sito", "analizzare la versione pubblica, o togliere temporaneamente la protezione")
    if status >= 400:
        return issue("alta", "pagina_non_disponibile", f"La pagina principale risponde {status}: il sito non e' pubblicato o l'indirizzo e' sbagliato. L'analisi riguarda la pagina d'errore, non il sito",
                     "controllare che la pubblicazione esista e che l'indirizzo nel CRM sia giusto")
    return None


def score(issues: list[dict]) -> int:
    return max(0, 100 - sum(WEIGHT.get(i["gravita"], 0) for i in issues))


def grade(points: int) -> str:
    return "A" if points >= 90 else "B" if points >= 75 else "C" if points >= 60 else "D" if points >= 40 else "F"


def order(issues: list[dict]) -> list[dict]:
    return sorted(issues, key=lambda i: -WEIGHT.get(i["gravita"], 0))


def client(transport=None, follow=True, timeout=20, **headers) -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=follow, timeout=timeout, transport=transport, headers={"user-agent": UA, **headers})


# ---- lettura della pagina ---------------------------------------------------------------------------------
class Page(HTMLParser):
    """Estrae da un HTML cio' che serve a SEO e prestazioni (stdlib: nessuna dipendenza in piu')."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self._in_title, self.lang = "", False, ""
        self.meta: dict[str, str] = {}
        self.canonical, self.hreflang = "", []
        self.h: dict[str, list[str]] = {"h1": [], "h2": [], "h3": []}
        self._h, self.imgs, self.links, self.scripts, self.styles = None, [], [], [], []
        self.jsonld: list[str] = []
        self._ld, self._skip, self.words, self.in_head = False, 0, 0, False

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "head":
            self.in_head = True
        elif tag == "body":
            self.in_head = False
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or a.get("http-equiv") or "").lower()
            if key:
                self.meta[key] = a.get("content", "")
        elif tag == "link":
            rel = a.get("rel", "").lower()
            if "canonical" in rel:
                self.canonical = a.get("href", "")
            elif "alternate" in rel and a.get("hreflang"):
                self.hreflang.append(a["hreflang"])
            elif "stylesheet" in rel:
                self.styles.append(a.get("href", ""))
        elif tag in self.h:
            self._h = tag
            self.h[tag].append("")
        elif tag == "img":
            self.imgs.append(a)
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "script":
            if a.get("type", "").lower() == "application/ld+json":
                self._ld = True
                self.jsonld.append("")
            elif a.get("src"):
                self.scripts.append({**a, "head": self.in_head})
            self._skip += 1
        elif tag == "style":
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in self.h:
            self._h = None
        elif tag == "script":
            self._ld = False
            self._skip = max(0, self._skip - 1)
        elif tag == "style":
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._h and self.h[self._h]:
            self.h[self._h][-1] += data
        if self._ld and self.jsonld:
            self.jsonld[-1] += data
        if not self._skip and not self.in_head:
            self.words += len(data.split())


def parse(html: str) -> Page:
    p = Page()
    try:
        p.feed(html)
        p.close()                                                    # svuota il buffer: senza, il testo finale di una pagina troncata andrebbe perso
    except Exception:                                                # HTML molto rotto: si usa cio' che e' stato letto
        pass
    p.title = " ".join(p.title.split())
    return p


def tls_info(host: str, port: int = 443) -> dict:
    """Protocollo, cifrario, HTTP/2 (ALPN), scadenza e emittente del certificato. Con un certificato non valido ritorna l'errore, non solleva."""
    try:
        ctx = ssl.create_default_context()
        ctx.set_alpn_protocols(["h2", "http/1.1"])
        with socket.create_connection((host, port), timeout=8) as s, ctx.wrap_socket(s, server_hostname=host) as t:
            cert = t.getpeercert()
            exp = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            issuer = dict(x[0] for x in cert.get("issuer", ()))
            return {"versione": t.version(), "cifrario": (t.cipher() or ("?",))[0], "http2": t.selected_alpn_protocol() == "h2",
                    "giorni": (exp - datetime.now(timezone.utc)).days, "emittente": issuer.get("organizationName") or issuer.get("commonName") or "?"}
    except ssl.SSLCertVerificationError as e:
        return {"errore_certificato": str(e.verify_message or e)[:160]}
    except Exception as e:
        return {"errore": f"{type(e).__name__}: {str(e)[:120]}"}


async def first_get(url: str, transport=None, **headers) -> tuple[httpx.Response | None, dict, str]:
    """GET della pagina con le misure di rete. Ritorna (risposta, misure, errore)."""
    t0 = time.monotonic()
    try:
        async with client(transport, headers.pop("follow", True), **{"accept-encoding": _encodings(), **headers}) as c:
            async with c.stream("GET", url) as r:
                ttfb = (time.monotonic() - t0) * 1000
                body = await r.aread()
            total = (time.monotonic() - t0) * 1000
    except Exception as e:
        return None, {}, f"{type(e).__name__}: {str(e)[:140]}"
    r._content = body                                                  # lo stesso corpo e' poi leggibile con r.text
    return r, {"ttfb_ms": round(ttfb), "totale_ms": round(total), "byte": len(body), "redirect": len(r.history), "codifica": r.headers.get("content-encoding", "nessuna"),
               "cache_control": r.headers.get("cache-control", ""), "server": r.headers.get("server", ""), "stato": r.status_code}, ""


# ---- prestazioni ------------------------------------------------------------------------------------------
async def psi(url: str, strategy: str = "mobile", key: str | None = None, transport=None) -> dict:
    params = [("url", url), ("strategy", strategy), ("category", "performance"), ("category", "seo"), ("category", "accessibility"), ("category", "best-practices")]
    if key:
        params.append(("key", key))
    try:
        async with client(transport, timeout=100) as c:
            r = await c.get(PSI, params=params)
        if r.status_code == 429:
            return {"errore": "PageSpeed Insights ha esaurito la quota gratuita condivisa: serve una chiave gratuita di Google (variabile PAGESPEED_API_KEY, 25.000 richieste al giorno)"}
        if r.status_code >= 400:
            try:
                why = (r.json().get("error") or {}).get("message", "")
            except Exception:
                why = ""
            return {"errore": f"PageSpeed Insights ha risposto {r.status_code}: {why[:140] or 'nessun dettaglio'}"}
        d = r.json()
    except Exception as e:
        return {"errore": f"PageSpeed Insights non disponibile ({type(e).__name__})"}
    lh, audits = d.get("lighthouseResult", {}), d.get("lighthouseResult", {}).get("audits", {})
    cats = {k: round((v.get("score") or 0) * 100) for k, v in lh.get("categories", {}).items()}
    metrics = {name: audits.get(aid, {}).get("displayValue") for name, aid in (("LCP", "largest-contentful-paint"), ("FCP", "first-contentful-paint"), ("CLS", "cumulative-layout-shift"),
                                                                              ("TBT", "total-blocking-time"), ("Speed Index", "speed-index"), ("TTI", "interactive"))}
    opps = sorted(((a.get("details", {}).get("overallSavingsMs") or 0, a.get("title", "")) for a in audits.values() if a.get("details", {}).get("type") == "opportunity"), reverse=True)
    field = (d.get("loadingExperience") or {}).get("overall_category")
    return {"fonte": "PageSpeed Insights (motore Lighthouse)", "strategia": strategy, "punteggi": cats, "metriche": {k: v for k, v in metrics.items() if v},
            "esperienza_reale_utenti": field, "occasioni": [f"{t} (risparmio ~{ms / 1000:.1f} s)" for ms, t in opps[:5] if ms > 0]}


def perf_issues(m: dict, page: Page | None, tls: dict) -> list[dict]:
    out = []
    if m["ttfb_ms"] > 1800:
        out.append(issue("alta", "ttfb", f"Il server impiega {m['ttfb_ms']} ms a rispondere (ottimo < 800 ms)", "cache lato server o CDN, hosting piu' vicino, query piu' leggere"))
    elif m["ttfb_ms"] > 800:
        out.append(issue("media", "ttfb", f"Tempo al primo byte {m['ttfb_ms']} ms (ottimo < 800 ms)", "cache della pagina, CDN"))
    if m["codifica"] in ("nessuna", "identity") and m["byte"] > 10_000:
        out.append(issue("alta", "compressione", f"Pagina da {m['byte'] // 1024} KB senza compressione", "attivare gzip o brotli sul server o sulla CDN"))
    if m["byte"] > 250_000:
        out.append(issue("media", "peso_html", f"Il solo HTML pesa {m['byte'] // 1024} KB", "ridurre il markup, rimandare i contenuti non essenziali"))
    if m["redirect"] >= 2:
        out.append(issue("media", "redirect", f"{m['redirect']} redirect prima della pagina", "collegare direttamente all'indirizzo finale"))
    if tls.get("http2") is False:
        out.append(issue("bassa", "http2", "Il sito non usa HTTP/2", "abilitare HTTP/2 (o HTTP/3) sul server o sulla CDN"))
    if page:
        blocking = [s for s in page.scripts if s.get("head") and "async" not in s and "defer" not in s and s.get("type", "").lower() != "module"]
        if blocking:
            out.append(issue("media", "script_bloccanti", f"{len(blocking)} script nell'head senza async/defer bloccano il rendering", "aggiungere defer o async, o spostarli a fine pagina"))
        heavy = [i for i in page.imgs if i.get("loading", "").lower() != "lazy"]
        if len(heavy) > 6:
            out.append(issue("media", "immagini_lazy", f"{len(heavy)} immagini senza loading=\"lazy\"", "lazy loading per le immagini sotto la piega"))
        nodim = [i for i in page.imgs if not (i.get("width") and i.get("height"))]
        if len(nodim) > 3:
            out.append(issue("media", "immagini_dimensioni", f"{len(nodim)} immagini senza width/height (causano spostamenti di layout, CLS)", "indicare width e height su ogni immagine"))
        if len(page.styles) > 6:
            out.append(issue("bassa", "css", f"{len(page.styles)} fogli di stile separati", "unire e minimizzare il CSS"))
    return order(out)


async def perf(url: str, strategy: str = "mobile", key: str | None = None, transport=None, psi_transport=None, tls=tls_info) -> dict:
    r, m, err = await first_get(url, transport)
    if r is None:
        return {"url": url, "errore": f"sito non raggiungibile: {err}"}
    host = urlparse(str(r.url)).hostname or ""
    t = await asyncio.to_thread(tls, host) if str(r.url).startswith("https") else {}
    page = parse(r.text) if "html" in r.headers.get("content-type", "") else None
    issues = perf_issues(m, page, t)
    if (si := status_issue(m["stato"])) is not None:
        return {"url": url, "stato_http": m["stato"], "problemi": [si, *issues], "misure": {**m, "http2": t.get("http2")}, "lighthouse": {"errore": "non eseguito: la pagina non e' disponibile"}}
    g = await psi(url, strategy, key, psi_transport if psi_transport is not None else transport)
    return {"url": url, "misure": {**m, "http2": t.get("http2"), "script": len(page.scripts) if page else None, "immagini": len(page.imgs) if page else None}, "problemi": issues,
            "lighthouse": g, "punteggio_proprio": score(issues)}


# ---- SEO --------------------------------------------------------------------------------------------------
def page_seo_issues(url: str, p: Page, status: int) -> list[dict]:
    out = []
    t = p.title
    if not t:
        out.append(issue("alta", "title_mancante", "Manca il tag <title>", "un titolo unico di 30-60 caratteri con la parola chiave principale"))
    elif not 25 <= len(t) <= 65:
        out.append(issue("media", "title_lunghezza", f"Titolo di {len(t)} caratteri (ideale 30-60): «{t[:60]}»", "accorciare o ampliare il titolo"))
    d = p.meta.get("description", "")
    if not d:
        out.append(issue("alta", "description_mancante", "Manca la meta description", "una descrizione di 70-160 caratteri che inviti al clic"))
    elif not 60 <= len(d) <= 170:
        out.append(issue("bassa", "description_lunghezza", f"Meta description di {len(d)} caratteri (ideale 70-160)"))
    n1 = len([x for x in p.h["h1"] if x.strip()])
    if n1 == 0:
        out.append(issue("alta", "h1_mancante", "Manca l'H1", "un solo H1 che descriva la pagina"))
    elif n1 > 1:
        out.append(issue("media", "h1_multipli", f"{n1} H1 nella pagina", "tenerne uno solo"))
    if not p.canonical:
        out.append(issue("media", "canonical", "Manca il link canonical", "<link rel=\"canonical\"> verso l'indirizzo preferito"))
    if "noindex" in p.meta.get("robots", "").lower():
        out.append(issue("alta", "noindex", "La pagina ha noindex: non comparira' su Google", "togliere noindex se la pagina deve essere trovata"))
    if not p.lang:
        out.append(issue("media", "lang", "Manca l'attributo lang su <html>", "<html lang=\"it\">"))
    if "viewport" not in p.meta:
        out.append(issue("alta", "viewport", "Manca il meta viewport (la pagina non e' pensata per il telefono)", "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"))
    noalt = [i for i in p.imgs if "alt" not in i]
    if noalt:
        out.append(issue("media", "alt", f"{len(noalt)} immagini su {len(p.imgs)} senza testo alternativo", "descrivere ogni immagine con alt (anche vuoto se decorativa)"))
    if not any(k.startswith("og:") for k in p.meta):
        out.append(issue("bassa", "open_graph", "Mancano i tag Open Graph (anteprima sui social)", "og:title, og:description, og:image"))
    if not p.jsonld:
        out.append(issue("bassa", "dati_strutturati", "Nessun dato strutturato (schema.org)", "JSON-LD Organization/LocalBusiness e, dove serve, FAQ o Product"))
    if p.words < 150:
        out.append(issue("media", "poco_testo", f"Solo ~{p.words} parole di testo", "contenuti piu' ricchi sulle pagine che devono posizionarsi"))
    return out


async def seo(url: str, transport=None, max_pages: int = 6, max_links: int = 10) -> dict:
    r, m, err = await first_get(url, transport)
    if r is None:
        return {"url": url, "errore": f"sito non raggiungibile: {err}"}
    base = str(r.url)
    host = urlparse(base).netloc
    p = parse(r.text) if "html" in r.headers.get("content-type", "") else None
    if (si := status_issue(r.status_code)) is not None:
        return {"url": base, "stato_http": r.status_code, "problemi": [si], "nota": "Analisi SEO non eseguita: la pagina principale non e' disponibile."}
    if p is None:
        return {"url": url, "errore": f"la risposta non e' una pagina HTML (tipo: {r.headers.get('content-type', 'sconosciuto')[:40]})"}
    issues = page_seo_issues(base, p, r.status_code)
    robots, sitemap_urls, sample = None, [], []
    async with client(transport, timeout=15) as c:
        try:
            rr = await c.get(urljoin(base, "/robots.txt"))
            robots = rr.text if rr.status_code == 200 and "html" not in rr.headers.get("content-type", "") else None
        except Exception:
            robots = None
        if robots is None:
            issues.append(issue("media", "robots", "Manca robots.txt", "aggiungere robots.txt con l'indirizzo della sitemap"))
        else:
            if re.search(r"(?im)^\s*disallow:\s*/\s*$", robots) and re.search(r"(?im)^\s*user-agent:\s*\*", robots):
                issues.append(issue("alta", "robots_blocca_tutto", "robots.txt blocca tutto il sito (Disallow: /)", "togliere il blocco se il sito deve essere indicizzato"))
            sitemap_urls = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots)
        found = False
        for smap in [*sitemap_urls[:1], urljoin(base, "/sitemap.xml")]:                # prima quella dichiarata in robots.txt, poi quella di default
            try:
                sm = await c.get(smap)
            except Exception as e:
                issues.append(issue("media", "sitemap_robots", f"La sitemap indicata in robots.txt non risponde ({smap}): {type(e).__name__}",
                                    "correggere l'indirizzo in robots.txt (su uno staging e' normale se punta al dominio di produzione)"))
                continue
            if sm.status_code == 200 and "<loc>" in sm.text:
                found = True
                sample = [u for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sm.text) if urlparse(u).netloc == host and not u.endswith(".xml")]
                break
        if not found:
            issues.append(issue("media", "sitemap", "Sitemap non trovata", "generare sitemap.xml e indicarla in robots.txt"))
        # altre pagine dalla sitemap: stessi controlli, titoli e descrizioni duplicati
        titles, descs, pages = {p.title: [base]} if p.title else {}, {p.meta.get("description", ""): [base]} if p.meta.get("description") else {}, []
        for u in [x for x in sample if x.rstrip("/") != base.rstrip("/")][: max_pages - 1]:
            try:
                pr = await c.get(u)
            except Exception:
                continue
            if "html" not in pr.headers.get("content-type", ""):
                continue
            pp = parse(pr.text)
            pages.append({"url": u, "problemi": [f"{i['gravita']}: {i['messaggio']}" for i in order(page_seo_issues(u, pp, pr.status_code))[:4]]})
            titles.setdefault(pp.title, []).append(u)
            if pp.meta.get("description"):
                descs.setdefault(pp.meta["description"], []).append(u)
        for what, code, grp in (("titolo", "titolo_duplicato", titles), ("meta description", "description_duplicata", descs)):
            dup = [v for k, v in grp.items() if k and len(v) > 1]
            if dup:
                issues.append(issue("media", code, f"{what.capitalize()} identico su {len(dup[0])} pagine (es. {dup[0][0]})", "ogni pagina deve avere un testo suo"))
        # link interni rotti (come linkinator), a campione
        internal = list(dict.fromkeys(urljoin(base, h).split("#")[0] for h in p.links if h and not h.startswith(("mailto:", "tel:", "javascript:", "#"))
                                      and urlparse(urljoin(base, h)).netloc == host))[:max_links]
        broken = []
        for u in internal:
            try:
                x = await c.head(u)
                if x.status_code in (405, 501):
                    x = await c.get(u)
                if x.status_code >= 400:
                    broken.append(f"{u} -> {x.status_code}")
            except Exception as e:
                broken.append(f"{u} -> {type(e).__name__}")
        if broken:
            issues.append(issue("alta", "link_rotti", f"{len(broken)} link interni rotti su {len(internal)} controllati: " + "; ".join(broken[:3]), "correggere o rimuovere i link, o aggiungere un redirect 301"))
    return {"url": base, "punteggio": score(issues), "voto": grade(score(issues)), "problemi": order(issues), "pagine_controllate": 1 + len(pages), "altre_pagine": pages,
            "misure": {"titolo": p.title[:80], "parole": p.words, "immagini": len(p.imgs), "link": len(p.links), "dati_strutturati": len(p.jsonld), "lingue_alternate": p.hreflang[:6]}}


# ---- sicurezza (solo controlli passivi) -------------------------------------------------------------------
SENSITIVE = (("/.git/HEAD", lambda r: r.text.strip().startswith("ref:"), "repository Git esposto: si puo' scaricare il codice sorgente"),
             ("/.git/config", lambda r: "[core]" in r.text, "configurazione Git esposta"),
             ("/.env", lambda r: "html" not in r.headers.get("content-type", "") and re.search(r"(?m)^[A-Z][A-Z0-9_]{2,}=", r.text) is not None, "file .env esposto: puo' contenere chiavi e password"),
             ("/.DS_Store", lambda r: b"Bud1" in r.content[:16], "file .DS_Store esposto (elenca i file del server)"),
             ("/phpinfo.php", lambda r: "phpinfo()" in r.text, "phpinfo() esposto: rivela la configurazione del server"),
             ("/server-status", lambda r: "Apache Server Status" in r.text, "pagina di stato di Apache esposta"))


def header_issues(h: httpx.Headers, https: bool) -> list[dict]:
    out, g = [], h.get
    csp = g("content-security-policy", "")
    if https and not g("strict-transport-security"):
        out.append(issue("alta", "hsts", "Manca Strict-Transport-Security (HSTS)", "Strict-Transport-Security: max-age=31536000; includeSubDomains"))
    elif https:
        m = re.search(r"max-age=(\d+)", g("strict-transport-security", ""))
        if m and int(m.group(1)) < 15_552_000:
            out.append(issue("bassa", "hsts_breve", f"HSTS con max-age basso ({m.group(1)} s)", "almeno 15552000 (6 mesi), meglio 31536000"))
    if not csp:
        out.append(issue("media", "csp", "Manca Content-Security-Policy", "partire da default-src 'self' e allargare solo il necessario (OWASP Secure Headers)"))
    elif re.search(r"'unsafe-eval'|script-src[^;]*'unsafe-inline'", csp):
        out.append(issue("media", "csp_debole", "La CSP ammette 'unsafe-inline' o 'unsafe-eval' negli script", "usare nonce o hash al posto di unsafe-inline"))
    if g("x-content-type-options", "").lower() != "nosniff":
        out.append(issue("media", "nosniff", "Manca X-Content-Type-Options: nosniff", "X-Content-Type-Options: nosniff"))
    if not g("x-frame-options") and "frame-ancestors" not in csp:
        out.append(issue("media", "clickjacking", "Il sito si puo' incorporare in un iframe altrui (clickjacking)", "X-Frame-Options: DENY o CSP frame-ancestors 'none'"))
    if not g("referrer-policy"):
        out.append(issue("bassa", "referrer", "Manca Referrer-Policy", "Referrer-Policy: strict-origin-when-cross-origin"))
    if not g("permissions-policy"):
        out.append(issue("bassa", "permissions", "Manca Permissions-Policy", "disattivare camera, microfono, geolocalizzazione se non servono"))
    if re.search(r"\d", g("server", "")) or g("x-powered-by"):
        out.append(issue("bassa", "versione_esposta", f"Il server rivela software e versione ({g('server') or ''} {g('x-powered-by') or ''}".strip() + ")", "nascondere Server e X-Powered-By"))
    return out


def cookie_issues(h: httpx.Headers, https: bool) -> list[dict]:
    out = []
    for c in h.get_list("set-cookie"):
        name, low = c.split("=", 1)[0], c.lower()
        flags = []
        if https and "secure" not in low:
            flags.append("Secure")
        if "httponly" not in low:
            flags.append("HttpOnly")
        if "samesite" not in low:
            flags.append("SameSite")
        if flags:
            out.append(issue("media", "cookie", f"Il cookie «{name}» non ha: {', '.join(flags)}", "impostare Secure; HttpOnly; SameSite=Lax (o Strict)"))
    return out


def sri_issues(p: Page, host: str) -> list[dict]:
    """Script caricati da domini esterni senza `integrity` (Subresource Integrity): se quel dominio viene compromesso, il codice entra nel sito."""
    ext = []
    for s in p.scripts:
        h = urlparse(s.get("src", "")).hostname
        if h and h != host and not s.get("integrity"):
            ext.append(h)
    if not ext:
        return []
    doms = ", ".join(sorted(set(ext))[:4])
    return [issue("bassa", "script_senza_sri", f"{len(ext)} script esterni senza controllo di integrita' (SRI): {doms}", "aggiungere l'attributo integrity (e crossorigin) agli script di terzi, oppure ospitarli sul proprio dominio")]


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}")
SOCIAL = ("facebook.com", "instagram.com", "linkedin.com", "tiktok.com", "youtube.com", "x.com", "twitter.com", "wa.me", "t.me")
HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")


async def profile(url: str, transport=None) -> dict:
    """Scheda del sito di un cliente (ripresa dall'intake del vecchio Hermes): contatti, social, colori, caratteri, logo e tecnologia. Solo una GET alla home."""
    r, m, err = await first_get(url, transport)
    if r is None:
        return {"url": url, "errore": f"sito non raggiungibile: {err}"}
    if (si := status_issue(m["stato"])) is not None and m["stato"] >= 400:
        return {"url": url, "stato_http": m["stato"], "problemi": [si]}
    if "html" not in r.headers.get("content-type", ""):
        return {"url": url, "errore": "la risposta non e' una pagina HTML"}
    html, p, host = r.text, parse(r.text), urlparse(str(r.url)).hostname or ""
    emails = sorted({e.lower() for e in EMAIL_RE.findall(html) if not e.lower().endswith((".png", ".jpg", ".webp", ".svg", ".gif"))})[:5]
    tel = sorted({re.sub(r"[^\d+]", "", x) for x in re.findall(r"tel:([+\d\s.-]+)", html)})[:5]
    social = sorted({l.split("?")[0] for l in p.links if any(d in (urlparse(l).hostname or "") for d in SOCIAL)})[:8]
    colori = [c for c, _ in Counter(c.lower() for c in HEX_RE.findall(html)).most_common(6)]
    if p.meta.get("theme-color"):
        colori.insert(0, p.meta["theme-color"])
    fonts = sorted({urllib.parse.unquote(f).split("family=")[1].split("&")[0].split(":")[0].replace("+", " ") for f in p.styles if "family=" in f and "fonts.googleapis" in f})[:4]
    icon = re.search(r'<link[^>]+rel="[^"]*icon[^"]*"[^>]+href="([^"]+)"', html, re.I)
    scripts = " ".join(s.get("src", "") for s in p.scripts) + " " + html[:20000]
    tech = [n for n, k in (("WordPress", "wp-content"), ("Shopify", "cdn.shopify"), ("Wix", "wixstatic"), ("Squarespace", "squarespace"), ("Webflow", "webflow"),
                           ("Next.js", "_next/"), ("React", "react"), ("Elementor", "elementor"), ("Google Analytics", "googletagmanager")) if k in scripts.lower()]
    return {"url": str(r.url), "titolo": p.title, "descrizione": p.meta.get("description", ""), "lingua": p.lang, "email": emails, "telefoni": tel, "social": social,
            "colori": colori[:6], "caratteri": fonts, "logo_o_immagine": urljoin(str(r.url), p.meta.get("og:image", "") or (icon.group(1) if icon else "")),
            "tecnologia": tech or (["non riconosciuta"]), "generator": p.meta.get("generator", ""), "https": str(r.url).startswith("https"),
            "nota": "Dati pubblici letti dalla home: da verificare con il cliente prima di usarli in un preventivo o in un nuovo sito."}



async def full(url: str, transport=None, psi_transport=None, key: str | None = None) -> dict:
    """Analisi completa in una sola chiamata: prestazioni + SEO + sicurezza, con un riepilogo e le prime azioni da fare."""
    perf_r, seo_r, sec_r = await asyncio.gather(perf(url, "mobile", key, transport, psi_transport), seo(url, transport), security(url, transport))
    top = []
    for nome, r in (("sicurezza", sec_r), ("prestazioni", perf_r), ("seo", seo_r)):
        for i in (r.get("problemi") or [])[:3]:
            top.append({"area": nome, **i})
    rank = {"alta": 0, "media": 1, "bassa": 2}
    top.sort(key=lambda i: rank.get(i.get("gravita"), 3))
    return {"url": url,
            "riepilogo": {"sicurezza": sec_r.get("voto"), "seo": seo_r.get("punteggio"), "prestazioni": perf_r.get("punteggio_proprio")},
            "priorita": top[:8], "prestazioni": perf_r, "seo": seo_r, "sicurezza": sec_r}


async def security(url: str, transport=None, tls=tls_info) -> dict:
    r, m, err = await first_get(url, transport)
    if r is None:
        return {"url": url, "errore": f"sito non raggiungibile: {err}"}
    final = urlparse(str(r.url))
    https = final.scheme == "https"
    issues, checks = [], {}
    if (si := status_issue(r.status_code)) is not None:
        issues.append(si)
    if not https:
        issues.append(issue("alta", "no_https", "Il sito risponde solo in HTTP, senza cifratura", "installare un certificato (gratuito con Let's Encrypt) e passare a HTTPS"))
    issues += header_issues(r.headers, https) + cookie_issues(r.headers, https)
    if https:
        t = await asyncio.to_thread(tls, final.hostname or "")
        checks["tls"] = t
        if "errore_certificato" in t:
            issues.append(issue("alta", "certificato", f"Certificato non valido: {t['errore_certificato']}", "rinnovare o correggere il certificato"))
        elif "errore" not in t:
            if t["giorni"] < 0:
                issues.append(issue("alta", "certificato_scaduto", "Certificato scaduto", "rinnovarlo subito"))
            elif t["giorni"] < 14:
                issues.append(issue("media", "certificato_in_scadenza", f"Il certificato scade tra {t['giorni']} giorni", "controllare il rinnovo automatico"))
            if t["versione"] in ("TLSv1", "TLSv1.1", "SSLv3"):
                issues.append(issue("alta", "tls_vecchio", f"Protocollo obsoleto: {t['versione']}", "ammettere solo TLS 1.2 e 1.3"))
        try:                                                           # http -> https
            async with client(transport, follow=False, timeout=10) as c:
                x = await c.get(f"http://{final.netloc}/")
            loc = x.headers.get("location", "")
            checks["http_verso_https"] = f"{x.status_code} -> {loc[:60]}"
            if not (x.status_code in (301, 308) and loc.startswith("https://")):
                issues.append(issue("media", "redirect_https", "L'indirizzo http:// non porta in modo permanente a https://", "redirect 301 da http a https"))
        except Exception:
            checks["http_verso_https"] = "porta 80 non raggiungibile"
        p = parse(r.text) if "html" in r.headers.get("content-type", "") else None
        if p:
            mixed = [s for s in [i.get("src", "") for i in p.imgs] + [s.get("src", "") for s in p.scripts] + p.styles if s.startswith("http://")]
            if mixed:
                issues.append(issue("alta" if any(s in str(p.scripts) for s in mixed) else "media", "contenuto_misto", f"{len(mixed)} risorse caricate in HTTP dentro una pagina HTTPS", "caricare tutto in https"))
    if (page := parse(r.text) if "html" in r.headers.get("content-type", "") else None) is not None:
        issues += sri_issues(page, final.hostname or "")
    exposed = []
    async with client(transport, timeout=10) as c:
        for path, check, msg in SENSITIVE:
            try:
                x = await c.get(urljoin(f"{final.scheme}://{final.netloc}", path))
                if x.status_code == 200 and check(x):
                    exposed.append(path)
                    issues.append(issue("alta", "esposto" + path.replace("/", "_"), f"{path}: {msg}", "bloccare l'accesso a questo percorso dal server e cambiare le chiavi eventualmente esposte"))
            except Exception:
                continue
        try:
            sec = await c.get(urljoin(f"{final.scheme}://{final.netloc}", "/.well-known/security.txt"))
            checks["security_txt"] = sec.status_code == 200 and "contact" in sec.text.lower()
        except Exception:
            checks["security_txt"] = False
        try:                                                            # CORS: l'origine di un estraneo non deve essere riflessa con le credenziali
            x = await c.get(str(r.url), headers={"origin": "https://origine-estranea.example"})
            if x.headers.get("access-control-allow-origin") == "https://origine-estranea.example" and x.headers.get("access-control-allow-credentials", "").lower() == "true":
                issues.append(issue("alta", "cors", "CORS riflette qualsiasi origine con le credenziali", "ammettere solo le origini note"))
        except Exception:
            pass
    p_ = score(issues)
    return {"url": str(r.url), "punteggio": p_, "voto": grade(p_), "problemi": order(issues), "esposizioni": exposed, "controlli": checks,
            "nota": "Solo controlli passivi (intestazioni, certificato, percorsi noti). Non sostituisce un test di sicurezza completo con strumenti attivi (nuclei, OWASP ZAP), da fare solo con autorizzazione."}


# ---- siti ammessi -----------------------------------------------------------------------------------------
def audit_targets(db=None, quotas: dict | None = None) -> dict[str, str]:
    """{nome: url}: i siti GIA' registrati. Quelli monitorati (progetti online del CRM, quotas.yaml) e gli indirizzi di staging dei progetti.
    Nessun indirizzo a scelta: un agente non puo' analizzare un sito qualunque."""
    from . import uptime
    out = dict(uptime.targets(db, quotas, netlify_fn=lambda: {}))
    try:
        if db is None:
            from .tools.crm_tools import PgDb
            db = PgDb()
        for r in db.query("SELECT name, staging_url, target_url FROM projects WHERE (staging_url IS NOT NULL AND staging_url <> '') OR (target_url IS NOT NULL AND target_url <> '')"):
            for key, label in (("target_url", ""), ("staging_url", " (staging)")):
                u = (r.get(key) or "").strip()
                if u.startswith(("http://", "https://")):
                    out.setdefault(f"{r['name']}{label}", u)
    except Exception:
        pass
    return out
