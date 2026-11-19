"""Strumenti di analisi dei siti (prestazioni, SEO, sicurezza): sola lettura, solo siti gia' registrati. La logica sta in hlab/audit.py."""
from __future__ import annotations

import os

from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

from .. import audit, scopes
from .base import S, err, js, schema

NAMES = ["list_audit_sites", "perf_check", "seo_check", "security_check", "audit_completo", "site_profile"]
RISK = {n: "read" for n in NAMES}          # solo GET/HEAD verso siti gia' registrati: niente scritture, niente indirizzi a scelta, niente scanner attivi


def make_tools(targets_fn=None) -> list:
    ro = ToolAnnotations(readOnlyHint=True)
    targets = targets_fn or audit.audit_targets

    def resolve(name: str):
        known = targets()
        hit = next((n for n in known if n.lower() == (name or "").strip().lower()), None)
        if hit is None:
            return None, err(f"'{name}' non e' un sito registrato. Siti noti: {', '.join(sorted(known)) or 'nessuno'}. Non accetto indirizzi nuovi: se serve un sito, aggiungilo al progetto nel CRM.")
        return (hit, known[hit]), None

    @tool("list_audit_sites", "Elenco dei siti che si possono analizzare (progetti del CRM con indirizzo online o di staging, e siti extra). Parti da qui.", schema({}), annotations=ro)
    async def list_audit_sites(a):
        return js({"siti": targets()})

    @tool("perf_check", "Prestazioni di un sito registrato: tempo al primo byte, compressione, cache, HTTP/2, script che bloccano il rendering, immagini; piu' i punteggi e le metriche "
          "Core Web Vitals di Lighthouse (PageSpeed Insights, gratuito). `strategy`: mobile (predefinito) o desktop. Puo' richiedere fino a un minuto.",
          schema({"name": S, "strategy": S}, ["name"]), annotations=ro)
    async def perf_check(a):
        hit, e = resolve(a.get("name"))
        if e:
            return e
        strategy = "desktop" if (a.get("strategy") or "").lower() == "desktop" else "mobile"
        return js({"sito": hit[0], **await audit.perf(hit[1], strategy, os.environ.get("PAGESPEED_API_KEY"))})

    @tool("seo_check", "SEO on-page di un sito registrato: titolo, descrizione, H1, canonical, lingua, immagini senza alt, dati strutturati, robots.txt, sitemap, duplicati, "
          "link interni rotti (a campione, al massimo 6 pagine e 10 link). Ritorna un punteggio 0-100 e i problemi in ordine di gravita'.", schema({"name": S}, ["name"]), annotations=ro)
    async def seo_check(a):
        hit, e = resolve(a.get("name"))
        return e or js({"sito": hit[0], **await audit.seo(hit[1])})

    @tool("security_check", "Sicurezza PASSIVA di un sito registrato: intestazioni (HSTS, CSP, ...), cookie, certificato e TLS, redirect a https, contenuto misto, file esposti "
          "(.git, .env), CORS. Voto A-F. Non e' uno scanner attivo e non prova password o iniezioni.", schema({"name": S}, ["name"]), annotations=ro)
    async def security_check(a):
        hit, e = resolve(a.get("name"))
        return e or js({"sito": hit[0], **await audit.security(hit[1])})

    @tool("audit_completo", "Analisi completa di un sito registrato in UNA chiamata: prestazioni + SEO + sicurezza, con riepilogo (voto sicurezza, punteggio SEO, punteggio prestazioni) "
          "e le prime azioni da fare in ordine di priorita'. Usala per un quadro generale; per approfondire un'area usa lo strumento specifico.",
          schema({"name": S}, ["name"]), annotations=ro)
    async def audit_completo(a):
        hit, e = resolve(a.get("name"))
        return e or js({"sito": hit[0], **await audit.full(hit[1], key=os.environ.get("PAGESPEED_API_KEY"))})

    @tool("site_profile", "Scheda del sito di un cliente registrato: titolo, descrizione, email, telefoni, social, colori, caratteri, logo e tecnologia usata (WordPress, Shopify, ...). "
          "Utile per preventivi e per capire da dove parte un cliente. Dati pubblici letti dalla home, da verificare con il cliente.", schema({"name": S}, ["name"]), annotations=ro)
    async def site_profile(a):
        hit, e = resolve(a.get("name"))
        return e or js({"sito": hit[0], **await audit.profile(hit[1])})

    return [list_audit_sites, perf_check, seo_check, security_check, audit_completo, site_profile]


def build():
    return create_sdk_mcp_server(name="audit", version="1.0.0", tools=scopes.harden("audit", make_tools()))
