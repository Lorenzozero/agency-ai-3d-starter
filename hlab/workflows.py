"""Lavori durevoli registrati nel worker (`python -m hlab worker`). Ogni passo ha un nome: dopo un riavvio si riparte dall'ultimo salvato."""
from __future__ import annotations

from pathlib import Path

from . import factory_heal
from .events import Events


async def echo(ctx, payload):
    a = await ctx.step("a", lambda: payload.get("n", 1) * 2)
    return {"risultato": await ctx.step("b", lambda: a + 1)}


async def heal_site(ctx, payload):
    """QA in browser -> patch automatica con il modello -> nuova verifica. Mostra a una persona solo il risultato gia' corretto."""
    site = Path(payload["site_dir"])
    res = await ctx.step("heal", lambda: factory_heal.heal(site, factory_heal.llm_patcher(), int(payload.get("max_attempts", 2))))
    Events().log("heal_site", "builder", detail=f"{res['prima']['punteggio']} -> {res['dopo']['punteggio']} (healed={res['healed']})", ok=res["healed"])
    return {"healed": res["healed"], "prima": res["prima"]["punteggio"], "dopo": res["dopo"]["punteggio"], "work_dir": res["work_dir"],
            "residui": res["dopo"]["problemi"], "diff": res["diff"][:3000]}


async def learn(ctx, payload):
    """Il Memory Keeper legge i feedback non elaborati e propone regole (le attivi tu con `rule-approve`)."""
    n = await ctx.step("conta", lambda: len(Events().feedback_rows(limit=500)))
    if not n:
        return {"feedback": 0, "nota": "niente da elaborare"}
    from .runner import run
    out = await ctx.step("keeper", lambda: run("Usa l'agente memory-keeper: leggi i feedback non elaborati, proponi le regole per i ruoli giusti "
                                                "e segna i feedback come elaborati. Riferisci le regole proposte.", on_text=lambda t: None))
    return {"feedback": n, "esito": out.get("subtype"), "costo_stimato": out.get("cost_usd")}


def _tell(text: str, buttons=None) -> None:
    """Messaggio a Lorenzo su Telegram. Mai un errore: se il bot non risponde il lavoro prosegue."""
    try:
        from . import notify
        notify.send(text, buttons=buttons)
    except Exception:
        pass


def _factory_prompts(slug: str, job: dict) -> dict:
    brief, edit = job.get("brief", ""), job.get("mode") == "modifica"
    repo = job.get("repo") or ""
    base = (f"Sito «{slug}». RICHIESTA DEL TITOLARE (le domande al cliente le ha gia' fatte il consulente: NON farne altre, usa quello che c'e' e scrivi i dati mancanti in todo):\n{brief}\n")
    if edit:
        d = (f"Usa l'agente direttore-creativo. {base}\nQuesta e' una MODIFICA del sito gia' pubblicato `{repo}`: apri con site_open('{repo}'), leggi site_context('{repo}') se esiste, cambia SOLO cio' che e' stato chiesto "
             f"e salva con site_spec_save('{repo}', ...). Lo slug di lavoro e' `{repo}`. Rispondi in 5 righe.")
        c = (f"Usa l'agente copywriter per il sito `{repo}` (gia' pubblicato, modifica). {base}\nCambia solo i testi richiesti con site_copy_save('{repo}', ...). Rispondi in 5 righe.")
        b = f"Usa l'agente costruttore-siti: costruisci `{repo}` con site_build('{repo}') e riferisci il verdetto, gli errori e cosa va confermato col cliente."
        dep = f"Usa l'agente deployer: se il rapporto di `{repo}` non e' bloccato, proponi la pubblicazione con propose_site_launch('{repo}', 'Modifica sito {repo}: ...')."
        return {"direttore": d, "copywriter": c, "costruttore": b, "deployer": dep}
    d = (f"Usa l'agente direttore-creativo. {base}\nLeggi PRIMA site_existing_list (se il cliente ha gia' un sito e' una modifica, non un sito nuovo) e site_context('{slug}'). "
         f"Poi progetta lo spec con lo scheletro obbligatorio e salvalo con site_spec_save('{slug}', ...). Rispondi in 5 righe: marca, palette, sezioni, cosa manca.")
    a = (f"Usa l'agente art-director per il sito `{slug}` (spec gia' salvato dal direttore creativo). Leggi site_context('{slug}') e site_spec_load('{slug}'): scrivi la bibbia visiva del brand, "
         f"l'hook dell'hero e i prompt professionali delle immagini per ruolo, con la stessa frase di stile in tutti, e consegna con site_art_save('{slug}', ...). Le foto del cliente (client:N) non si toccano. "
         f"Se l'hero e' un hero_video e il cliente non ha un video suo, cerca un video stock gratuito con site_stock_video (query inglese di 2-4 parole concrete), scegli un id adatto (orizzontale, 6-40 s, senza volti ne' loghi) e consegnalo come video.hero_id. Rispondi in 6 righe.")
    c = (f"Usa l'agente copywriter: riscrivi i testi definitivi del sito `{slug}` (spec gia' salvato) con le linee guida della tua skill e salvali con site_copy_save('{slug}', ...). "
         f"Leggi prima site_spec_load('{slug}') e site_context('{slug}'). Nessun dato inventato. Rispondi in 5 righe.")
    b = f"Usa l'agente costruttore-siti: costruisci `{slug}` con site_build('{slug}'); se e' 'bloccato' correggi lo spec UNA volta e ricostruisci. Riferisci verdetto, errori e cosa va confermato col cliente."
    dep = f"Usa l'agente deployer: se il rapporto di `{slug}` non e' bloccato, proponi la pubblicazione con propose_site_launch('{slug}', 'Sito {slug}: ...')."
    return {"direttore": d, "regista": a, "copywriter": c, "costruttore": b, "deployer": dep}


async def site_factory(ctx, payload):
    """La fabbrica di siti in 4 passi durevoli (direttore creativo -> copywriter -> costruttore -> deployer), avviata dalla GUI o da Telegram.
    Ogni passo si salva: dopo un riavvio si riparte da dove era arrivato. La pubblicazione resta una PROPOSTA che approva Lorenzo."""
    from . import powers, sitefactory as sf
    from .events import Events
    from .runner import run
    slug = payload["slug"]
    job = sf.read_job(slug) or {"brief": payload.get("brief", ""), "mode": "nuovo"}
    work = job.get("repo") if job.get("mode") == "modifica" else slug
    prompts = _factory_prompts(slug, job)
    labels = {"direttore": "Direttore creativo", "regista": "Art Director", "copywriter": "Copywriter", "costruttore": "Costruttore", "deployer": "Deployer"}
    _tell(f"🏭 Fabbrica avviata per «{slug}»: direttore creativo, copywriter, costruttore, poi la proposta di pubblicazione. Ti scrivo a ogni passo.")
    steps = ("direttore", "copywriter") if job.get("mode") == "modifica" else ("direttore", "regista", "copywriter")
    for step in steps:
        out = await ctx.step(step, lambda p=prompts[step]: run(p, on_text=lambda t: None))
        ok = out.get("subtype") == "success"
        if step == "regista":                                       # la direzione artistica migliora ma non blocca: senza, restano i prompt del direttore
            _tell("🎬 Art Director ha finito: bibbia visiva e prompt delle immagini." if ok else "⚠️ Art Director non e' riuscito: proseguo con i prompt del direttore creativo.")
            continue
        spec = sf.load_spec(work)
        if not ok or (step == "direttore" and spec is None):
            _tell(f"❌ {labels[step]}: non e' riuscito ({out.get('subtype') or 'errore'}). Lavoro fermo: dimmi se riprovare.")
            return {"esito": "fermo", "passo": step}
        _tell(f"✅ {labels[step]} ha finito" + (f": {len(spec['sections'])} sezioni." if step == "direttore" and spec else "."))
    out = await ctx.step("costruttore", lambda: run(prompts["costruttore"], on_text=lambda t: None))
    rep = sf.report(work)
    if rep is None:
        _tell("❌ Costruttore: nessun rapporto di costruzione. Lavoro fermo: dimmi se riprovare.")
        return {"esito": "fermo", "passo": "costruttore"}
    todo = "; ".join((rep.get("da_confermare_col_cliente") or [])[:4])
    _tell(f"🧱 Costruttore: {rep['verdetto']}. Sezioni: {len(rep.get('sezioni', []))}." + (f"\nDa confermare col cliente: {todo}." if todo else ""))
    try:                                                           # video hero gratis: il prompt dell'Art Director da lanciare a mano sui servizi con crediti giornalieri, poi si rimanda l'mp4
        import re as _re
        art = sf.workdir(work) / "art.md"
        m = _re.search(r"## Prompt video[^\n]*\n(.+?)(?:\n\n|$)", art.read_text(encoding="utf-8"), _re.S) if art.exists() else None
        if m:
            name = (sf.load_spec(work) or {}).get("repo_name") or work
            _tell("🎬 Video hero (facoltativo, gratis, a mano). Prompt dell'Art Director:\n" + m.group(1).strip()[:900] +
                  "\n\nLancialo su Dreamina, Kling o Google Flow (crediti gratuiti giornalieri), usando come immagine di partenza l'hero del sito. Poi mandami qui l'mp4 con la didascalia "
                  f"«metti questo video come hero di {name}» e aggiorno il sito.")
    except Exception:
        pass
    if rep["verdetto"].startswith("bloccato"):
        return {"esito": "bloccato", "verdetto": rep["verdetto"]}
    await ctx.step("deployer", lambda: run(prompts["deployer"], on_text=lambda t: None))
    ev = Events()
    prop = [c for c in powers.listing(ev) if c["kind"] == "site_publish" and c["target"] == work and c["status"] == "proposed"]
    if not prop:
        _tell("⚠️ Il sito e' pronto ma il deployer non ha proposto la pubblicazione. Chiedila a voce: «pubblica " + work + "».")
        return {"esito": "pronto_senza_proposta"}
    cid = prop[-1]["id"]
    try:                                            # il messaggio con i bottoni parte ora: il giro periodico non deve ripeterlo
        from . import notify
        st = notify.load()
        st.setdefault("changes", {})[str(cid)] = "proposed"
        notify.save(st)
    except Exception:
        pass
    _tell(f"🛠️ Sito «{work}» pronto da pubblicare (#{cid}): {rep['verdetto']}. Approvi la pubblicazione?", buttons=[("✅ Approva e pubblica", f"hc:ok:{cid}"), ("❌ Rifiuta", f"hc:no:{cid}")])
    return {"esito": "in_attesa_di_approvazione", "change": cid}


SENTINEL_BUDGET_USD = 12.0      # tetto teorico per esecuzione (listino Anthropic applicato a token gratuiti): la revisione fa molte prove e supera i 3 $ predefiniti, la spesa vera e' 0


async def sentinel_review(ctx, payload):
    """Revisione quotidiana della Sentinella (LLM): stato, documentazione, modelli nuovi da provare, modelli da togliere. I controlli veri stanno in hlab/sentinel.py."""
    from . import sentinel
    from .runner import run
    prompt = ("Usa l'agente sentinella per la REVISIONE QUOTIDIANA del sistema. Segui la tua skill miglioramento-continuo: leggi lo stato (system_status), le statistiche dei modelli (router_stats con days=2) "
              "e il registro delle tue azioni (sentinel_log); cerca candidati (router_candidates) e provane al massimo 3 (router_test_model); aggiungi solo chi supera la prova, togli solo chi ha "
              "errori ADESSO (il codice lo permette solo con almeno 10 richieste nelle ultime 6 ore, meno del 50% di riuscite e l'ultima fallita da meno di 2 ore: non insistere se rifiuta). Consulta la documentazione dei fornitori solo se serve a capire un limite o un modello. "
              "Chiudi con un rapporto di MASSIMO 6 righe: cosa hai controllato, cosa hai cambiato e perche', cosa chiedi a Lorenzo. Se non e' cambiato nulla scrivi solo: NESSUN CAMBIAMENTO.")
    out = await ctx.step("revisione", lambda: run(prompt, on_text=lambda t: None, budget=SENTINEL_BUDGET_USD))
    text = (out.get("result") or "").strip() if isinstance(out, dict) else ""
    if out.get("subtype") == "success" and text and "NESSUN CAMBIAMENTO" not in text.upper():
        await ctx.step("avviso", lambda: sentinel.tell_lorenzo("📋 Revisione quotidiana\n" + text[:1200]))
    return {"esito": out.get("subtype"), "rapporto": text[:600]}


HANDLERS = {"echo": echo, "heal_site": heal_site, "learn": learn, "site_factory": site_factory, "sentinel_review": sentinel_review}
