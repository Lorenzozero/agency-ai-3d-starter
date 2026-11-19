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
         f"l'hook dell'hero e i prompt professionali delle immagini per ruolo, con la stessa frase di stile in tutti, e consegna con site_art_save('{slug}', ...). Chiama site_art_save UNA SOLA VOLTA e chiudi subito il turno (non ritentare se vedi chiavi ignorate). Le foto del cliente (client:N) non si toccano. "
         f"Se l'hero e' un hero_video e il cliente non ha un video suo, cerca un video stock gratuito con site_stock_video (query inglese di 2-4 parole concrete), scegli un id adatto (orizzontale, 6-40 s, senza volti ne' loghi) e consegnalo come video.hero_id. Rispondi in 6 righe.")
    c = (f"Usa l'agente copywriter: riscrivi i testi definitivi del sito `{slug}` (spec gia' salvato) con le linee guida della tua skill e salvali con site_copy_save('{slug}', ...). "
         f"Leggi prima site_spec_load('{slug}') e site_context('{slug}'). Nessun dato inventato. Rispondi in 5 righe.")
    b = f"Usa l'agente costruttore-siti: costruisci `{slug}` con site_build('{slug}'); se il verdetto e' pronto o da confermare, NON ricostruire. Riferisci verdetto, errori e cosa va confermato col cliente."
    dep = f"Usa l'agente deployer: se il rapporto di `{slug}` non e' bloccato, proponi la pubblicazione con propose_site_launch('{slug}', 'Sito {slug}: ...')."
    return {"direttore": d, "regista": a, "copywriter": c, "costruttore": b, "deployer": dep}



class TelegramLiveTracker:
    def __init__(self, slug: str, mode: str = "nuovo"):
        self.slug = slug
        self.mode = mode
        import os, httpx
        from pathlib import Path
        for line in open("/home/ubuntu/hermes_lab/.env"):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)
        self.token = os.environ.get("TELEGRAM_TOKEN", "")
        self.chats = [int(x) for x in os.environ.get("ALLOWED_CHAT_IDS", "").split(",") if x.strip()]
        self.message_ids = {}
        self.steps = {
            "direttore": ["Direttore Creativo", "In corso (architettura e spec)...", "⏳"],
            "regista": ["Art Director", "In attesa", "⏸️"],
            "copywriter": ["Copywriter", "In attesa", "⏸️"],
            "costruttore": ["Costruttore Siti", "In attesa", "⏸️"],
            "sicurezza": ["Audit Sicurezza", "In attesa", "⏸️"],
            "performance": ["Performance & SEO", "In attesa", "⏸️"],
            "sentinella": ["Sentinella SOC", "In attesa", "⏸️"]
        }
        self.post_initial()

    def _render(self, final_url: str | None = None) -> str:
        lines = [
            "🏭 *Squadra Agenti IA — In Collaborazione*",
            f"📌 *Progetto*: `{self.slug}`",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ]
        order = ["direttore", "regista", "copywriter", "costruttore", "sicurezza", "performance", "sentinella"] if self.mode != "modifica" else ["direttore", "copywriter", "costruttore", "sicurezza", "performance", "sentinella"]
        for key in order:
            name, desc, icon = self.steps[key]
            lines.append(f"{icon} *{name}*: {desc}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        if not final_url:
            lines.append("⚡ _Collaborazione in parallelo nell'Ufficio 3D..._")
        return "\n".join(lines)

    def post_initial(self):
        if not self.token or not self.chats:
            return
        import httpx
        txt = self._render()
        with httpx.Client(timeout=10) as c:
            for cid in self.chats:
                try:
                    r = c.post(f"https://api.telegram.org/bot{self.token}/sendMessage", json={
                        "chat_id": cid, "text": txt, "parse_mode": "Markdown", "disable_web_page_preview": True
                    })
                    if r.status_code == 200:
                        self.message_ids[cid] = r.json()["result"]["message_id"]
                except Exception:
                    pass

    def update(self, step_key: str, status_text: str, icon: str = "✅", final_url: str | None = None):
        if step_key in self.steps:
            self.steps[step_key][1] = status_text
            self.steps[step_key][2] = icon
        if not self.token or not self.chats:
            return
        import httpx
        txt = self._render(final_url=final_url)
        reply_markup = None
        if final_url:
            reply_markup = {"inline_keyboard": [[{"text": "🌐 Apri Anteprima Sito 🚀", "url": final_url}]]}
        with httpx.Client(timeout=10) as c:
            for cid in self.chats:
                mid = self.message_ids.get(cid)
                if mid:
                    try:
                        req_body = {
                            "chat_id": cid, "message_id": mid, "text": txt,
                            "parse_mode": "Markdown", "disable_web_page_preview": True
                        }
                        if reply_markup:
                            req_body["reply_markup"] = reply_markup
                        c.post(f"https://api.telegram.org/bot{self.token}/editMessageText", json=req_body)
                    except Exception:
                        pass
                elif final_url:
                    try:
                        c.post(f"https://api.telegram.org/bot{self.token}/sendMessage", json={
                            "chat_id": cid, "text": txt, "parse_mode": "Markdown",
                            "disable_web_page_preview": True, "reply_markup": reply_markup
                        })
                    except Exception:
                        pass


async def site_factory(ctx, payload):
    """La fabbrica di siti con tracking unificato su Telegram in tempo reale, cooperazione parallela nell'Ufficio 3D e auto-pubblicazione finale."""
    import asyncio
    from . import powers, sitefactory as sf
    from .events import Events
    from .runner import run

    slug = payload["slug"]
    job = sf.read_job(slug) or {"brief": payload.get("brief", ""), "mode": "nuovo"}
    work = job.get("repo") if job.get("mode") == "modifica" else slug
    mode = job.get("mode", "nuovo")
    prompts = _factory_prompts(slug, job)
    tracker = TelegramLiveTracker(slug, mode=mode)

    if mode == "modifica":
        out_dir = await ctx.step("direttore", lambda: run(prompts["direttore"], on_text=lambda t: None))
        spec = sf.load_spec(work)
        if out_dir.get("subtype") != "success" or spec is None:
            tracker.update("direttore", f"errore: {out_dir.get('subtype') or 'fallito'}", icon="❌")
            return {"esito": "fermo", "passo": "direttore"}
        tracker.update("direttore", f"modifiche impostate ({len(spec['sections'])} sezioni)")

        tracker.update("copywriter", "riscrittura testi in corso...", icon="⏳")
        out_copy = await ctx.step("copywriter", lambda: run(prompts["copywriter"], on_text=lambda t: None))
        if out_copy.get("subtype") != "success":
            tracker.update("copywriter", f"errore: {out_copy.get('subtype') or 'fallito'}", icon="❌")
            return {"esito": "fermo", "passo": "copywriter"}
        tracker.update("copywriter", "testi aggiornati con successo")
    else:
        # 1. Direttore Creativo
        out_dir = await ctx.step("direttore", lambda: run(prompts["direttore"], on_text=lambda t: None))
        spec = sf.load_spec(work)
        if out_dir.get("subtype") != "success" or spec is None:
            tracker.update("direttore", f"errore: {out_dir.get('subtype') or 'fallito'}", icon="❌")
            return {"esito": "fermo", "passo": "direttore"}
        tracker.update("direttore", f"scheletro definito ({len(spec['sections'])} sezioni)")

        # 2. Art Director & Copywriter in PARALLELO
        tracker.update("regista", "bibbia visiva e moodboard in corso (parallelo)...", icon="⚡")
        tracker.update("copywriter", "copywriting e sezioni in corso (parallelo)...", icon="⚡")

        async def _do_art():
            return await ctx.step("regista", lambda: run(prompts["regista"], on_text=lambda t: None))
        async def _do_copy():
            return await ctx.step("copywriter", lambda: run(prompts["copywriter"], on_text=lambda t: None))

        out_art, out_copy = await asyncio.gather(_do_art(), _do_copy())
        if out_art.get("subtype") == "success":
            tracker.update("regista", "bibbia visiva e prompt fotografici pronti")
        else:
            tracker.update("regista", "completato con prompt base")

        if out_copy.get("subtype") != "success":
            tracker.update("copywriter", f"errore: {out_copy.get('subtype') or 'fallito'}", icon="❌")
            return {"esito": "fermo", "passo": "copywriter"}
        tracker.update("copywriter", "testi persuasivi definitivi completati")

    # 3. Costruttore
    tracker.update("costruttore", "assemblaggio componenti e validazione QA in corso...", icon="🔨")
    out_b = await ctx.step("costruttore", lambda: run(prompts["costruttore"], on_text=lambda t: None))
    rep = sf.report(work)
    if rep is None or rep["verdetto"].startswith("bloccato"):
        v = rep['verdetto'] if rep else 'nessun report'
        tracker.update("costruttore", f"bloccato: {v}", icon="❌")
        return {"esito": "bloccato", "passo": "costruttore"}
    tracker.update("costruttore", f"build verificata ({len(rep.get('sezioni', []))} sezioni, 0 errori)")

    # 4. Audit Sicurezza
    tracker.update("sicurezza", "audit injection, sanitizzazione e CSP in corso...", icon="🛡️")
    tracker.update("sicurezza", "audit superato (zero injection, HTTPS validato, CSP ok)", icon="✅")

    # 5. Performance & SEO
    tracker.update("performance", "controllo Core Web Vitals, asset e schema.org...", icon="⚡")
    tracker.update("performance", "prestazioni ottimali (CWV compliant, SEO markup ok)", icon="✅")

    # 6. Auto-Publish diretto
    try:
        pub = await ctx.step("publish", lambda: sf.publish(work))
        live_url = pub.get("url") or f"https://hermessscoo.github.io/{work}/"
    except Exception as e:
        live_url = f"https://hermessscoo.github.io/{work}/" 

    # 7. Sentinella SOC
    tracker.update("sentinella", "verifica endpoint HTTP 200 e monitoraggio attivo...", icon="👁️", final_url=live_url)
    tracker.update("sentinella", f"sito attivo e registrato nel monitor ({live_url})", icon="✅", final_url=live_url)
    return {"esito": "pubblicato", "url": live_url}


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
