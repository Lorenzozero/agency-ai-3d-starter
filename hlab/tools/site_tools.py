"""Strumenti della fabbrica di siti: due gruppi su un solo server. `site_design` (direttore creativo) e `site_build` (costruttore). La logica sta in hlab/sitefactory.py."""
from __future__ import annotations

from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

from .. import scopes, sitefactory
from .base import S, err, js, schema

DESIGN = ["site_guide", "site_context", "site_existing_list", "site_open", "site_list", "site_spec_load", "site_spec_save"]
BUILD = ["site_list", "site_spec_load", "site_spec_save", "site_build", "site_report"]
COPY = ["site_context", "site_existing_list", "site_list", "site_spec_load", "site_copy_save"]
ART = ["site_context", "site_list", "site_spec_load", "site_stock_video", "site_art_save"]
NAMES = sorted(set(DESIGN + BUILD + COPY + ART))
RISK = {"site_guide": "read", "site_context": "read", "site_existing_list": "read", "site_open": "reversible", "site_list": "read", "site_spec_load": "read", "site_report": "read",
        "site_spec_save": "reversible", "site_build": "reversible", "site_copy_save": "reversible", "site_art_save": "reversible", "site_stock_video": "read"}      # scrivono solo nella cartella di lavoro dei siti; nessuna pubblicazione da qui


def make_tools(runner=None) -> list:
    ro = ToolAnnotations(readOnlyHint=True)
    run = runner or sitefactory.run_cli

    @tool("site_guide", "Il manuale del direttore creativo: schema dello spec JSON, sezioni disponibili, font e palette curati, regole. Leggilo PRIMA di scrivere uno spec.", schema({}), annotations=ro)
    async def site_guide(a):
        g = sitefactory.guide(run)
        return err(g["errore"]) if "errore" in g else js(g)

    @tool("site_context", "Il MATERIALE raccolto per un sito (slug): la richiesta del titolare con le sue risposte alle domande del consulente, cio' che e' stato letto dai link (testi, contatti, colori), "
          "le foto e i video del cliente con il loro codice `client:N` da usare nel campo image. Leggilo PRIMA di scrivere lo spec o i testi.", schema({"slug": S}, ["slug"]), annotations=ro)
    async def site_context(a):
        try:
            c = sitefactory.read_context(a["slug"])
        except ValueError as e:
            return err(str(e))
        return js({"contesto": c}) if c else err("nessun materiale per questo sito: usa solo la richiesta che ti e' stata data")

    @tool("site_existing_list", "I siti GIA' PUBBLICATI dalla fabbrica (repo, indirizzo, marca, sezioni e se hanno gia' lo scheletro). CHIAMALA PRIMA di creare un sito: se il cliente ne ha gia' uno, "
          "la richiesta e' una modifica di quello (site_open), non un sito nuovo.", schema({}), annotations=ro)
    async def site_existing_list(a):
        r = sitefactory.list_published(run)
        return err(r["errore"]) if "errore" in r else js(r)

    @tool("site_open", "Apre per la MODIFICA un sito gia' pubblicato (nome del repo da site_existing_list): scarica spec e immagini nella cartella di lavoro. Da qui `site_spec_save` salva sullo stesso repo "
          "e il sito resta lo stesso, stesso indirizzo. Riaprire un sito gia' aperto riparte dall'ultima versione pubblicata.", schema({"repo": S}, ["repo"]))
    async def site_open(a):
        try:
            return js(sitefactory.open_site(a["repo"], run))
        except ValueError as e:
            return err(str(e))

    @tool("site_list", "I siti in lavorazione: nome (slug), se sono gia' costruiti e il verdetto del controllo nel browser.", schema({}), annotations=ro)
    async def site_list(a):
        return js(sitefactory.listing())

    @tool("site_spec_load", "Legge lo spec salvato di un sito (slug).", schema({"slug": S}, ["slug"]), annotations=ro)
    async def site_spec_load(a):
        try:
            s = sitefactory.load_spec(a["slug"])
        except ValueError as e:
            return err(str(e))
        return js(s) if s is not None else err("nessuno spec con questo nome")

    @tool("site_spec_save", "Salva lo spec JSON di un sito (slug: minuscole, numeri, trattini). `spec` e' una STRINGA JSON con brand, sezioni, palette, ecc. come da site_guide. Non costruisce nulla. Per un sito aperto con site_open salva sullo STESSO repo; `ritocco: true` (solo per modifiche piccole a un sito pubblicato) non impone lo scheletro.",
          schema({"slug": S, "spec": S, "ritocco": {"type": "boolean"}}, ["slug", "spec"]))
    async def site_spec_save(a):
        try:
            return js(sitefactory.save_spec(a["slug"], a["spec"], bool(a.get("ritocco"))))
        except ValueError as e:
            return err(str(e))

    @tool("site_copy_save", "Riscrive i TESTI di uno spec gia' salvato. `copy` e' una STRINGA JSON {ruolo: {campi}} con i ruoli dello scheletro (attenzione, problema, soluzione, offerta, usp, prova, cta, faq), "
          "`extra-1`, `extra-2`... per le sezioni su misura e `meta` ({seo_description, nav_cta_label}). Campi ammessi: eyebrow, kicker, title, subtitle, text, lines, bullets, points, price, note, "
          "cta:{label}, items:[{title,text,caption,tag,label,value,prefix,suffix,q,a,quote,author,detail}]. Non puoi cambiare tipi, immagini, collegamenti o ordine.", schema({"slug": S, "copy": S}, ["slug", "copy"]))
    async def site_copy_save(a):
        try:
            return js(sitefactory.merge_copy(a["slug"], a["copy"]))
        except ValueError as e:
            return err(str(e))

    @tool("site_stock_video", "Cerca video STOCK GRATUITI su Pixabay per l'hero (solo lettura: non scarica nulla). `query` = 2-4 parole concrete in inglese. Ritorna id, durata, tag, risoluzione, peso, "
          "pertinenza e miniatura; i migliori per primi (adatti all'hero: orizzontali, 6-40 s). Scegli un id e consegnalo con site_art_save (video.hero_id). Regole: niente marchi, loghi o volti riconoscibili, "
          "e il video stock non va presentato come il locale del cliente. Risultati in cache 24 ore.", schema({"query": S}, ["query"]), annotations=ro)
    async def site_stock_video(a):
        r = sitefactory.stock_videos(a["query"])
        return err(r["errore"]) if "errore" in r else js(r)

    @tool("site_art_save", "Consegna la DIREZIONE ARTISTICA di uno spec gia' salvato. `art` e' una STRINGA JSON: {art_direction: frase di stile (max 200 car.), bibbia: {palette, luce, ottica, texture, composizione, tono...}, "
          "immagini: {ruolo o sezione-N: {image: prompt in inglese, items: [{image}]}}, video: {hero_query: 2-4 parole inglesi, prompt: prompt video pronto}, shotlist: [righe]}. "
          "Cambia solo i prompt delle immagini esistenti e la frase di stile; salva anche art.md. Non puoi cambiare tipi, testi, colori, font o ordine.", schema({"slug": S, "art": S}, ["slug", "art"]))
    async def site_art_save(a):
        try:
            return js(sitefactory.merge_art(a["slug"], a["art"]))
        except ValueError as e:
            return err(str(e))

    @tool("site_build", "Costruisce il sito dallo spec salvato (template, immagini), lo prova nel browser su desktop e mobile e applica l'auto-correzione. Ritorna il rapporto e il verdetto "
          "(pronto / da rivedere / bloccato). Puo' richiedere alcuni minuti. Tetto giornaliero di costruzioni. NON pubblica.", schema({"slug": S}, ["slug"]))
    async def site_build(a):
        try:
            rep = sitefactory.build(a["slug"], run)
        except ValueError as e:
            return err(str(e))
        return err(rep["errore"]) if "errore" in rep else js(rep)

    @tool("site_report", "L'ultimo rapporto di costruzione di un sito: sezioni, errori nel browser, immagini, auto-correzione, cose da confermare col cliente, verdetto.", schema({"slug": S}, ["slug"]), annotations=ro)
    async def site_report(a):
        try:
            r = sitefactory.report(a["slug"])
        except ValueError as e:
            return err(str(e))
        return js(r) if r else err("sito non ancora costruito")

    return [site_guide, site_context, site_existing_list, site_open, site_list, site_spec_load, site_spec_save, site_copy_save, site_stock_video, site_art_save, site_build, site_report]


def build():
    return create_sdk_mcp_server(name="sites", version="1.0.0", tools=scopes.harden("sites", make_tools()))
