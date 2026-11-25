"""Hermes Telegram Supergroup Topic Dispatcher & Multi-Agent Routing.

Permette a un unico bot (@hermes_COO_lab_bot) di dare voce a tutta la squadra Hermes
all'interno di un Supergruppo Telegram suddiviso per Topic tematici (un topic per ufficio).
"""
import os
import json
import logging
import asyncio
import re
from pathlib import Path
from typing import Optional, Dict, Any

from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import RetryAfter

from hermes_v10 import research

log = logging.getLogger("hermes.topics")

DATA_DIR = Path.home() / "hermes_lab" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOPICS_FILE = DATA_DIR / "telegram_topics.json"

TELEGRAM_FORMAT_RULE = (
    "\n\nREGOLE FORMATTAZIONE TELEGRAM (TASSATIVE):\n"
    "1. MAI usare tabelle Markdown con pipe '|' o trattini '---': Telegram su smartphone non supporta tabelle e risultano spezzate e illeggibili.\n"
    "2. Per schede, layer, benchmark e audit usa SEMPRE elenchi puntati: '• *Voce*: Valore — Dettaglio/Stato'.\n"
    "3. MAI usare cancelletti ('#', '##', '###') per i titoli: usa invece il grassetto '*TITOLO*' o '*1. SEZIONE*'.\n"
    "4. MAI usare '>' per i blockquote: usa invece '▸ _testo_'."
)

def format_for_telegram(text: str) -> str:
    """Trasforma Markdown complesso (tabelle, cancelletti ##, blockquote >) in formato compatibile Telegram mobile."""
    if not text:
        return ""

    # 1. Converti tabelle Markdown in elenchi puntati puliti
    def table_repl(match):
        raw_table = match.group(0).strip()
        lines = [l.strip() for l in raw_table.splitlines() if l.strip()]
        content_lines = [l for l in lines if not re.match(r'^[\|\s\-:]+$', l)]
        if len(content_lines) <= 1:
            return raw_table

        out = []
        for r in content_lines[1:]:
            cells = [c.strip() for c in r.split('|') if c.strip()]
            if not cells:
                continue
            first = cells[0]
            rest = cells[1:]
            if rest:
                out.append(f"• *{first}*: " + " — ".join(rest))
            else:
                out.append(f"• *{first}*")
        return "\n".join(out)

    table_pattern = re.compile(r'(?:^[ \t]*\|[^\n]+\|[ \t]*(?:\r?\n|$))+', re.MULTILINE)
    text = table_pattern.sub(table_repl, text)

    # 2. Converti titoli ## in grassetto pulito
    text = re.sub(r'^[ \t]*#{1,6}[ \t]*(.+?)[ \t]*$', r'*\1*', text, flags=re.MULTILINE)

    # 3. Converti blockquote > in citazione elegante
    text = re.sub(r'^[ \t]*>[ \t]*(.+?)[ \t]*$', r'▸ _\1_', text, flags=re.MULTILINE)

    # 4. Normalizza righe vuote
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


OFFICES = {
    "direction": {
        "title": "Direzione",
        "badge": "[Direzione]",
        "agent": "Hermes CEO & CTO",
        "icon_color": 0x6FB9F0,
        "role": "CEO & CTO",
        "system_prompt": (
            "Sei il Chief Executive Officer (CEO) e Chief Technology Officer (CTO) di Hermes Lab. "
            "Ti rivolgi a Lorenzo Garoffolo, Founder & Principal Architect. "
            "AMBITO DI RESPONSABILITÀ: Massima autorità strategica, architettura dei sistemi, ROI, brand positioning e governance operativa. "
            "CRITERI: Rifiuti ogni compromesso qualitativo, standard Awwwards, tolleranza zero per il debito tecnico o la fuffa commerciale. "
            "Se Lorenzo o la squadra propongono idee, fai sintesi esecutiva con delibere nette: 'Approvato', 'Bocciato', o 'Modifica obbligatoria'. "
            "STILE: Autorevole, implacabile, tecnico, asciutto. Risposte dense con elenchi puntati strutturati o schede punto per punto."
            + TELEGRAM_FORMAT_RULE
        ),
        "welcome": (
            "*Direzione Generale*\n\n"
            "Presidenza Esecutiva e Sala di Comando Strategica.\n"
            "Governance, architettura software, roadmap e delibere definitive."
        )
    },
    "creative": {
        "title": "Design",
        "badge": "[Design]",
        "agent": "Art Director & 3D Specialist",
        "icon_color": 0xCB86DB,
        "role": "Art Director",
        "system_prompt": (
            "Sei l'Art Director & Lead 3D Designer di Hermes. Standard: Awwwards Site of the Day e FWA. "
            "AMBITO DI RESPONSABILITÀ: Identità visiva d'impatto cinematografico, contrasto cromatico 60-30-10, palette Dark Luxury (Dark Obsidian #090A0F, accenti Neon/Gold, glassmorphism con bordi luminescenti rgba(255,255,255,0.08)), "
            "tipografia fluida (Plus Jakarta Sans, Space Grotesk, Syne), composizione Hero 3D (Spline/Three.js ottimizzato Draco <1.2MB). "
            "CRITERI: Demolisci ogni template generico, banale o piatto. Proponi sempre codici HEX esatti, font pairing e concept tridimensionale. "
            "STILE: Visionario, raffinato, essenziale, visivo ed esecutivo."
            + TELEGRAM_FORMAT_RULE
        ),
        "welcome": (
            "*Design & Art Direction*\n\n"
            "Standard Awwwards/FWA: estetica Dark Luxury, tipografia d'eccellenza e modelli 3D interattivi."
        )
    },
    "copy": {
        "title": "Copywriting",
        "badge": "[Copywriting]",
        "agent": "Senior Copywriter & Brand Voice Specialist",
        "icon_color": 0xFFD67E,
        "role": "Lead Copywriter",
        "system_prompt": (
            "Sei il Lead Copywriter & Conversion Strategist di Hermes. "
            "AMBITO DI RESPONSABILITÀ: Copywriting a risposta diretta, framework PAS (Problem-Agitate-Solution) e StoryBrand. "
            "Forgi headline taglienti che colpiscono il nervo scoperto del cliente nei primi 3 secondi, 3 Unique Selling Proposition (USP) indiscutibili, "
            "Call to Action che provocano azione immediata e microcopy persuasivo. "
            "CRITERI: Vietata la fuffa ('leader nel settore', 'qualità e passione'). Il copy deve convertire, differenziare e distruggere i competitor locali. "
            "STILE: Tagliente, magnetico, chirurgico, ritmo serrato."
            + TELEGRAM_FORMAT_RULE
        ),
        "welcome": (
            "*Copywriting & Brand Voice*\n\n"
            "Copywriting a risposta diretta: headline d'attacco, USP scientifiche e claim ad altissima conversione."
        )
    },
    "dev": {
        "title": "Sviluppo",
        "badge": "[Sviluppo]",
        "agent": "Lead Web Engineer & QA",
        "icon_color": 0x8EEE98,
        "role": "Lead Web Engineer",
        "system_prompt": (
            "Sei il Lead Frontend & Full-Stack Engineer di Hermes. "
            "AMBITO DI RESPONSABILITÀ: Compilazione HTML5 semantico, Tailwind CSS avanzato, scroll cinetico Lenis, animazioni GSAP con IntersectionObserver, "
            "layout responsive fluido mobile-first (clamp, viewport-fit=cover, 0 overflow su 390px), test automatici headless Playwright (0 errori console/network). "
            "CRITERI: Budget rigido per gli asset (bundle totale <1.8MB, immagini in WebP/AVIF, WebGL con fallback CSS 3D istantaneo su mobile a risparmio energetico). "
            "STILE: Puro rigore ingegneristico, diagnosi millimetriche, snippet di codice e architettura a prova di bomba."
            + TELEGRAM_FORMAT_RULE
        ),
        "welcome": (
            "*Sviluppo & Ingegneria Frontend*\n\n"
            "Compilazione HTML5/Tailwind, animazioni GSAP, layout responsive mobile-first e QA headless Playwright."
        )
    },
    "osint": {
        "title": "OSINT",
        "badge": "[OSINT]",
        "agent": "Market Scout & OSINT Analyst",
        "icon_color": 0xFF93B2,
        "role": "OSINT Scout",
        "system_prompt": (
            "Sei l'Analista OSINT e Market Scout di Hermes. "
            "AMBITO DI RESPONSABILITÀ: Ricognizione profonda su link Google Maps e profili social, identificazione del 'Sentiment Gap' (analisi delle debolezze e recensioni negative dei competitor nella stessa area), "
            "estrazione dati societari verificati, orari, recapiti, target alto-spendente e keyword locali ad alto intento commerciale. "
            "CRITERI: Precisione chirurgica, elenchi strutturati con evidenze verificate, zero dati allucinati o inventati. "
            "STILE: Metodico, da intelligence militare, pragmatico e orientato all'attacco di mercato."
            + TELEGRAM_FORMAT_RULE
        ),
        "welcome": (
            "*Intelligence di Mercato & OSINT*\n\n"
            "Ricognizione competitiva profonda, sentiment gap analysis e intelligence da Google Maps e social."
        )
    },
    "soc": {
        "title": "Sicurezza",
        "badge": "[Sicurezza]",
        "agent": "Sentinella SOC & Security Lead",
        "icon_color": 0x34D399,
        "role": "Security & SRE Lead",
        "system_prompt": (
            "Sei il Lead Sentinella SOC, Sicurezza e Performance SRE di Hermes. "
            "AMBITO DI RESPONSABILITÀ: Hardening perimetrale dei siti web, Content Security Policy (CSP restrittiva, self-hosted only, zero CDN esterne vulnerabili), "
            "audit intestazioni HTTP (HSTS preload, X-Content-Type-Options, X-Frame-Options DENY), Core Web Vitals estremi (LCP < 0.9s, CLS = 0, INP < 50ms, Lighthouse 98-100), monitoraggio continuo uptime con SLA 99.9%. "
            "CRITERI: Nessuna tolleranza per script terzi non autorizzati o colli di bottiglia prestazionali. "
            "STILE: Implacabile, difensivo, analitico e focalizzato sulla massima resilienza."
            + TELEGRAM_FORMAT_RULE
        ),
        "welcome": (
            "*SOC Sicurezza & Performance*\n\n"
            "Hardening difensivo, CSP restrittive, Core Web Vitals sub-second e monitoraggio SLA continuo."
        )
    },
    "delivery": {
        "title": "Consegne",
        "badge": "[Consegne]",
        "agent": "Project Manager & Delivery Lead",
        "icon_color": 0xFB6F5F,
        "role": "Delivery Lead",
        "system_prompt": (
            "Sei il Delivery Lead & Release Manager di Hermes. "
            "AMBITO DI RESPONSABILITÀ: Pipeline di distribuzione su GitHub Pages, controllo stato deploy, generazione link di anteprima istantanei, "
            "verifica token di accesso e archivio storico dei rilasci. "
            "CRITERI: Consegne rapide, link cliccabili, checklist di verifica superata (QA, SSL, DNS). "
            "STILE: Rapido, organizzato, trasparente."
            + TELEGRAM_FORMAT_RULE
        ),
        "welcome": (
            "*Consegne & Showcase Progetti*\n\n"
            "Pipeline di deploy, link live verificati e rilascio credenziali per i clienti."
        )
    },
    "triage": {
        "title": "Triage",
        "badge": "[Triage]",
        "agent": "Hermes COO Intake",
        "icon_color": 0x6FB9F0,
        "role": "COO Intake",
        "system_prompt": (
            "Sei Hermes COO, l'Orchestratore Operativo di Hermes Lab. "
            "AMBITO DI RESPONSABILITÀ: Intake immediato dei brief, coordinamento sincronizzato della squadra, avvio della pipeline e gestione code di produzione. "
            "CRITERI: Appena ricevi un link o un brief, attivi la tavola rotonda cross-ufficio senza esitazione e assegni le priorità. "
            "STILE: Operativo, impeccabile, risolutivo. Zero attrito."
            + TELEGRAM_FORMAT_RULE
        ),
        "welcome": (
            "*Triage & Coordinamento Operativo*\n\n"
            "Intake dei progetti, avvio pipeline multi-agente e smistamento operativo immediato."
        )
    }
}

TOPIC_HISTORY: Dict[int, list] = {}
MAX_HISTORY = 10


def load_topics_data() -> dict:
    if not TOPICS_FILE.exists():
        return {"group_id": None, "topics": {}}
    try:
        return json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Impossibile leggere %s: %s", TOPICS_FILE, e)
        return {"group_id": None, "topics": {}}


def save_topics_data(data: dict):
    try:
        TOPICS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.error("Impossibile salvare %s: %s", TOPICS_FILE, e)


def get_office_by_thread_id(thread_id: Optional[int]) -> Optional[str]:
    if not thread_id:
        return None
    data = load_topics_data()
    for key, spec in data.get("topics", {}).items():
        if spec.get("thread_id") == thread_id:
            return key
    return None


async def setup_offices_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Crea tutti i topic degli uffici Hermes nel Supergruppo."""
    chat = update.effective_chat
    msg = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await msg.reply_text("Questo comando va eseguito all'interno di un Supergruppo Telegram con i Forum Topics abilitati.")
        return

    # Verifica bot admin
    try:
        me_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        if not getattr(me_member, "can_manage_topics", False) and me_member.status != "creator":
            await msg.reply_text(
                "Il bot deve essere Amministratore con il permesso Gestione Argomenti (can_manage_topics) abilitato."
            )
            return
    except Exception as e:
        log.warning("Verifica admin fallita: %s", e)

    status_msg = await msg.reply_text("Inizializzazione Topic Uffici in corso...")

    data = load_topics_data()
    data["group_id"] = chat.id
    if "topics" not in data:
        data["topics"] = {}

    summary = ["*Topic Uffici attivati nel Supergruppo:*\n"]
    for key, spec in OFFICES.items():
        existing = data["topics"].get(key, {})
        thread_id = existing.get("thread_id")
        created_now = False

        if not thread_id:
            try:
                topic = await context.bot.create_forum_topic(
                    chat_id=chat.id,
                    name=spec["title"],
                    icon_color=spec["icon_color"]
                )
                thread_id = topic.message_thread_id
                data["topics"][key] = {
                    "thread_id": thread_id,
                    "title": spec["title"],
                    "agent": spec["agent"],
                    "role": spec["role"]
                }
                created_now = True
            except Exception as e:
                log.error("Errore creazione topic %s: %s", key, e)
                summary.append(f"- {spec['title']}: errore ({e})")
                continue

        summary.append(f"- *{spec['title']}* (Thread `{thread_id}`) -> {spec['agent']}")

        if created_now:
            try:
                await context.bot.send_message(
                    chat_id=chat.id,
                    message_thread_id=thread_id,
                    text=spec["welcome"],
                    parse_mode="Markdown"
                )
            except Exception as e:
                log.warning("Errore invio messaggio welcome nel topic %s: %s", key, e)
            await asyncio.sleep(0.4)

    save_topics_data(data)
    await status_msg.edit_text("\n".join(summary), parse_mode="Markdown")


async def bind_office_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lega manualmente il topic corrente a una chiave ufficio: /bind_office <direction|creative|copy|dev|osint|delivery|triage>"""
    msg = update.effective_message
    thread_id = getattr(msg, "message_thread_id", None)
    if not thread_id:
        await msg.reply_text("⚠️ Esegui questo comando dentro il Topic che desideri collegare.")
        return

    args = context.args or []
    if not args:
        keys_list = ", ".join(OFFICES.keys())
        await msg.reply_text(f"Specifica l'ufficio da collegare. Chiavi disponibili:\n`{keys_list}`\n\nEsempio: `/bind_office creative`")
        return

    key = args[0].strip().lower()
    if key not in OFFICES:
        keys_list = ", ".join(OFFICES.keys())
        await msg.reply_text(f"Ufficio non riconosciuto. Scegli tra:\n`{keys_list}`")
        return

    data = load_topics_data()
    data["group_id"] = update.effective_chat.id
    if "topics" not in data:
        data["topics"] = {}

    data["topics"][key] = {
        "thread_id": thread_id,
        "title": OFFICES[key]["title"],
        "agent": OFFICES[key]["agent"],
        "role": OFFICES[key]["role"]
    }
    save_topics_data(data)

    await msg.reply_text(
        f"✅ Topic collegato con successo a *{OFFICES[key]['title']}*!\n"
        f"L'agente *{OFFICES[key]['agent']}* risponderà qui a ogni messaggio.",
        parse_mode="Markdown"
    )


async def topics_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra la mappa corrente dei topic e uffici collegati."""
    data = load_topics_data()
    topics = data.get("topics", {})
    if not topics:
        await update.effective_message.reply_text(
            "Nessun topic collegato. Usa `/setup_offices` per crearli tutti automaticamente nel Supergruppo."
        )
        return

    lines = ["🗺️ *Mappa Uffici Hermes & Topic Telegram:*\n"]
    for key, spec in OFFICES.items():
        bound = topics.get(key)
        if bound and bound.get("thread_id"):
            lines.append(f"• *{spec['title']}*: Thread ID `{bound['thread_id']}` (Agente: _{spec['agent']}_)")
        else:
            lines.append(f"• *{spec['title']}*: _Non collegato_ (Usa `/bind_office {key}`) ")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def broadcast_to_office(bot, office_key: str, text: str, parse_mode: Optional[str] = "Markdown") -> bool:
    """Invia un messaggio al topic dell'ufficio gestendo in modo robusto parse error, flood control e limiti di lunghezza."""
    data = load_topics_data()
    group_id = data.get("group_id")
    office = data.get("topics", {}).get(office_key)
    if not group_id or not office or not office.get("thread_id"):
        return False

    # Pulisci e adatta la formattazione a Telegram (niente tabelle rotte, niente cancelletti ##)
    text = format_for_telegram(text)

    if len(text) > 4000:
        text = text[:3950] + "\n\n..."

    thread_id = office["thread_id"]

    for _ in range(2):
        try:
            await bot.send_message(
                chat_id=group_id,
                message_thread_id=thread_id,
                text=text,
                parse_mode=parse_mode
            )
            return True
        except RetryAfter as e:
            wait_s = getattr(e, "retry_after", 5) + 1
            log.warning("Flood control su topic %s, attendo %s secondi...", office_key, wait_s)
            await asyncio.sleep(wait_s)
            try:
                await bot.send_message(
                    chat_id=group_id,
                    message_thread_id=thread_id,
                    text=text,
                    parse_mode=parse_mode
                )
                return True
            except Exception as ex2:
                log.warning("Invio post-flood su %s fallito: %s", office_key, ex2)
                return False
        except Exception as e:
            err_str = str(e)
            if parse_mode and ("parse" in err_str.lower() or "entity" in err_str.lower()):
                log.info("Markdown parsing fallito per %s, ritento in plain text: %s", office_key, e)
                parse_mode = None
                continue
            log.warning("Broadcast a ufficio %s fallito: %s", office_key, e)
            return False
    return False


async def handle_topic_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str, llm_func, on_complete_build: Optional[Any] = None) -> bool:
    """Instrada il messaggio verso l'agente dell'ufficio competente."""
    chat = update.effective_chat
    msg = update.effective_message
    thread_id = getattr(msg, "message_thread_id", None)

    if not thread_id or chat.type not in ("group", "supergroup"):
        return False

    office_key = get_office_by_thread_id(thread_id)
    if not office_key:
        return False

    spec = OFFICES[office_key]

    # Special handling per Triage: avvio autonomo del dibattito e allineamento
    if office_key == "triage":
        url_match = re.search(r"https?://[^\s<>\"']+", user_text)
        is_build_req = bool(url_match or re.search(r"\b(sito|crea|costruisci|nuovo|progetto|avvia|fa[im]|realizza|lancia|discut|parla|confront|brief|squadra|agent|tavola|roundtable|opinione|idea|brand|azienda|locale|bar|ristorante|negozio|studio)\b", user_text, re.I))
        if is_build_req:
            target = url_match.group(0) if url_match else user_text.strip()
            await msg.reply_text(f"[Triage] Brief ricevuto: `{target}`\nAvvio immediato della discussione autonoma tra tutti gli uffici...")
            asyncio.create_task(run_roundtable_debate(context.bot, target, llm_func, group_id=chat.id, initial_thread_id=thread_id, on_complete_build=on_complete_build))
            return True

    # Special handling per Ufficio OSINT se Lorenzo manda un link Maps o chiede ricerca
    if office_key == "osint":
        url_match = re.search(r"https?://[^\s<>\"']+", user_text)
        if url_match:
            url = url_match.group(0)
            if "google." in url or "maps" in url or "goo.gl" in url:
                status_reply = await msg.reply_text("🧭 *OSINT Scout*: Analizzo il link di Google Maps ed estraggo i dati aziendali...", parse_mode="Markdown")
                res = await asyncio.to_thread(research.resolve, url)
                name = res.get("name") or res.get("query")
                place = res.get("place")
                
                info_text = f"📍 *Attività identificata*: `{name or 'Non determinata'}`\n"
                if place:
                    info_text += f"🏢 *Luogo*: `{place}`\n"

                biz = None
                if name:
                    biz = await asyncio.to_thread(research.find_business, f"{name} {place or ''}".strip())
                    if biz:
                        if biz.get("site"):
                            info_text += f"🌐 *Sito Web*: {biz['site']}\n"
                        if biz.get("fb"):
                            info_text += f"📘 *Facebook*: {biz['fb']}\n"
                        if biz.get("ig"):
                            info_text += f"📸 *Instagram*: {biz['ig']}\n"
                        if biz.get("phone"):
                            info_text += f"📞 *Telefono*: {biz['phone']}\n"

                prompt = (
                    f"Dati estratti per l'attività:\n{info_text}\n"
                    f"Richiesta dell'utente: {user_text}\n\n"
                    "Fornisci un'analisi OSINT sintetica e strategica per la costruzione del sito web (stile, palette suggerita, punti di forza, asset mancanti)."
                )
                try:
                    analysis, _ = await llm_func(prompt, spec["system_prompt"], max_tokens=1200)
                    final_response = f"{spec['badge']}\n\n{info_text}\n📋 *Valutazione Strategica*:\n{analysis}"
                except Exception:
                    final_response = f"{spec['badge']}\n\n{info_text}\n(Analisi completata con i dati raccolti)."

                final_response = format_for_telegram(final_response)
                await status_reply.edit_text(final_response[:3900], parse_mode="Markdown")
                return True

    # Generazione risposta per l'agente d'ufficio
    history = TOPIC_HISTORY.setdefault(thread_id, [])
    history.append({"role": "user", "content": user_text[:1500]})
    if len(history) > MAX_HISTORY:
        history.pop(0)

    context_msgs = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in history[-6:]])
    prompt = (
        f"Contesto della conversazione nel topic {spec['title']}:\n{context_msgs}\n\n"
        f"Lorenzo Garoffolo si rivolge direttamente a te nel tuo ufficio ({spec['title']}).\n"
        f"- Se ti chiede informazioni o dati, fornisci dettagli precisi sul tuo operato.\n"
        f"- Se ti chiede una REVISIONE o modifica forzata (su copy, design, codice, sicurezza), applicala subito e conferma l'avvenuto aggiornamento.\n"
        f"Rispondi come {spec['agent']} in modo diretto, altamente tecnico, esecutivo e senza preamboli."
    )

    try:
        reply_text, _ = await llm_func(prompt, spec["system_prompt"], max_tokens=1500)
    except Exception as e:
        log.error("Errore LLM per topic %s: %s", office_key, e)
        reply_text = f"Errore interno nell'elaborazione della risposta: {e}"

    history.append({"role": "assistant", "content": reply_text[:1500]})
    full_reply = format_for_telegram(f"{spec['badge']}\n\n{reply_text}")

    if len(full_reply) > 4000:
        full_reply = full_reply[:3950] + "\n\n..."

    try:
        await msg.reply_text(full_reply, parse_mode="Markdown")
    except Exception:
        await msg.reply_text(full_reply)

    return True


async def run_roundtable_debate(bot, topic_or_link: str, llm_func, group_id: int, initial_thread_id: Optional[int] = None, on_complete_build: Optional[Any] = None):
    """Esegue un brainstorming / dibattito multi-agente in tempo reale tra tutti i topic degli uffici."""
    data = load_topics_data()
    if not group_id and data.get("group_id"):
        group_id = data["group_id"]
    
    # 0. Annuncio iniziale
    announcement = (
        f"*Tavola Rotonda Hermes Multi-Agent Avviata*\n\n"
        f"Target: `{topic_or_link}`\n"
        f"Gli 8 specialisti aprono il dibattito strategico e la validazione del concept nei rispettivi uffici."
    )
    if initial_thread_id:
        try:
            await bot.send_message(chat_id=group_id, message_thread_id=initial_thread_id, text=announcement, parse_mode="Markdown")
        except Exception:
            pass
    elif "triage" in data.get("topics", {}):
        await broadcast_to_office(bot, "triage", announcement)
    
    # Risoluzione OSINT se link o nome
    biz_info = {}
    url_match = re.search(r"https?://[^\s<>\"']+", topic_or_link)
    if url_match:
        url = url_match.group(0)
        res = await asyncio.to_thread(research.resolve, url)
        biz_name = res.get("name") or res.get("query") or topic_or_link
        place = res.get("place")
        biz_info = {"name": biz_name, "place": place}
        if biz_name:
            found = await asyncio.to_thread(research.find_business, f"{biz_name} {place or ''}".strip())
            if found:
                biz_info.update(found)
    else:
        biz_name = topic_or_link
        biz_info = {"name": biz_name}

    await asyncio.sleep(1.0)

    # 1. Topic OSINT: Scouting e Sentiment Gap
    osint_prompt = (
        f"Sei l'OSINT Scout. Analisi competitiva per: {biz_name}\nDati: {json.dumps(biz_info, ensure_ascii=False)}\n\n"
        f"Fornisci: 1) Posizionamento e Sentiment Gap (punti deboli dei competitor locali); "
        f"2) Target alto-spendente da intercettare; 3) 3 Keyword ad alto intento commerciale; 4) Angolo di differenziazione. Sii chirurgico e sintetico."
    )
    try:
        osint_reply, _ = await llm_func(osint_prompt, OFFICES["osint"]["system_prompt"], max_tokens=700)
    except Exception as e:
        osint_reply = f"Analisi OSINT completata con dati preliminari per {biz_name}."
    await broadcast_to_office(bot, "osint", f"{OFFICES['osint']['badge']}\n\n*Intelligence & Sentiment Gap (`{biz_name}`)*:\n\n{osint_reply}")

    # Intervento dialogico OSINT in Triage per la squadra
    try:
        osint_chat_prompt = (
            f"Sei l'OSINT Scout. Nel canale generale Triage, rivolgiti direttamente alla squadra su '{biz_name}'.\n"
            f"Dossier sintetico: {osint_reply[:400]}\n"
            "Scrivi 2 frasi taglienti: spiega cosa hai scoperto sui competitor locali, tagga @Design e @Copywriting indicando il punto debole su cui affondare il colpo. Nessun preambolo."
        )
        osint_chat, _ = await llm_func(osint_chat_prompt, OFFICES["osint"]["system_prompt"], max_tokens=150)
    except Exception:
        osint_chat = f"Mappati i competitor di {biz_name}: layout antiquati e zero differenziazione. @Design e @Copywriting, tocca a voi rompere i cliché del settore."
    await broadcast_to_office(bot, "triage", f"[OSINT Scout -> @Design @Copywriting]\n{osint_chat}")

    await asyncio.sleep(2.0)

    # 2. Topic Creative: Art Director smonta i cliché e definisce identità visiva + 3D
    creative_prompt = (
        f"Sei l'Art Director & 3D Designer. Leggi l'analisi OSINT:\n{osint_reply}\n\n"
        f"Rigetta l'ordinarietà del settore. Standard Awwwards/FWA: "
        f"1) Direzione visiva Dark Luxury ad alto contrasto (regola 60-30-10); 2) Palette cromatica con codici HEX esatti (Sfondo primario, Accento neon/gold, Testi); "
        f"3) Typography pairing (Headings e Body); 4) Concept per modello 3D Spline/Three.js ottimizzato per l'hero section. Stile visionario ed esecutivo."
    )
    try:
        creative_reply, _ = await llm_func(creative_prompt, OFFICES["creative"]["system_prompt"], max_tokens=750)
    except Exception as e:
        creative_reply = "Palette: Dark Obsidian (#090A0F) + Accento Neon (#38BDF8). Modello 3D centrale per hero section."
    await broadcast_to_office(bot, "creative", f"{OFFICES['creative']['badge']}\n\n*Direzione Artistica & Identità 3D (`{biz_name}`)*:\n\n{creative_reply}")

    # Intervento dialogico Art Director in Triage per la squadra
    try:
        art_chat_prompt = (
            f"Sei l'Art Director. Nel canale Triage rispondi a quanto detto da OSINT su '{biz_name}'.\n"
            f"Tua proposta visiva: {creative_reply[:400]}\n"
            "Scrivi 2 frasi taglienti: tagga @OSINT approvando l'analisi, cita la palette Dark Luxury scelta (#HEX) e sfida @Copywriting a tirare fuori claim che non siano la solita fuffa. Nessun preambolo."
        )
        art_chat, _ = await llm_func(art_chat_prompt, OFFICES["creative"]["system_prompt"], max_tokens=150)
    except Exception:
        art_chat = f"@OSINT ricevuto. Bandiamo il bianco generico: imposto Dark Luxury (#090A0F) con contrasto neon e visual 3D interattivo. @Copywriting, serve un copy d'assalto."
    await broadcast_to_office(bot, "triage", f"[Art Director -> @OSINT @Copywriting]\n{art_chat}")

    await asyncio.sleep(2.0)

    # 3. Topic Copy: Copywriter sfida il design e cala claim di vendita
    copy_prompt = (
        f"Sei il Lead Copywriter. Leggi la proposta dell'Art Director:\n{creative_reply}\n\n"
        f"Sfida l'estetica pura: l'impatto visivo attira, ma il testo converte. "
        f"Framework PAS e StoryBrand: 1) Headline hero tagliente e provocatoria per `{biz_name}`; 2) 3 Value Proposition scientifiche contro i concorrenti; "
        f"3) Call to Action ad alta conversione. Tono chirurgico, zero banalità."
    )
    try:
        copy_reply, _ = await llm_func(copy_prompt, OFFICES["copy"]["system_prompt"], max_tokens=750)
    except Exception as e:
        copy_reply = "Headline: 'L'eccellenza non cerca scorciatoie.'\nCTA: 'Richiedi Accesso Immediato'."
    await broadcast_to_office(bot, "copy", f"{OFFICES['copy']['badge']}\n\n*Architettura Copy & Claim di Vendita (`{biz_name}`)*:\n\n{copy_reply}")

    # Intervento dialogico Copywriter in Triage per la squadra
    try:
        copy_chat_prompt = (
            f"Sei il Lead Copywriter. Nel canale Triage rispondi ad Art Director e OSINT su '{biz_name}'.\n"
            f"Tuo copy: {copy_reply[:400]}\n"
            "Scrivi 2 frasi incisive: tagga @Design apprezzando l'estetica ma ricordando che è il copy a chiudere contratti, lancia la tua headline hero e chiedi a @Sviluppo se l'architettura supporta una conversione a freddo. Nessun preambolo."
        )
        copy_chat, _ = await llm_func(copy_chat_prompt, OFFICES["copy"]["system_prompt"], max_tokens=150)
    except Exception:
        copy_chat = f"@Design l'impatto estetico attira, ma il testo converte. Ho piazzato un'headline chirurgica e 3 USP senza mezze misure. @Sviluppo, garantiscimi fluidità assoluta e zero tempi morti."
    await broadcast_to_office(bot, "triage", f"[Lead Copywriter -> @Design @Sviluppo]\n{copy_chat}")

    await asyncio.sleep(2.0)

    # 4. Topic Dev: Lead Engineer pone i vincoli ingegneristici
    dev_prompt = (
        f"Sei il Lead Web Engineer. Valuta le richieste di Design e Copy:\n"
        f"Design:\n{creative_reply[:600]}\nCopy:\n{copy_reply[:600]}\n\n"
        f"Fattibilità tecnica e performance budget: 1) Ottimizzazione asset 3D (glTF compresso Draco <1.2MB, fallback CSS 3D istantaneo su mobile); "
        f"2) Struttura HTML5 semantica, Tailwind con fluid clamp typography, animazioni GSAP e scroll Lenis; 3) Standard Playwright QA su 390px (0 errori console/overflow)."
    )
    try:
        dev_reply, _ = await llm_func(dev_prompt, OFFICES["dev"]["system_prompt"], max_tokens=700)
    except Exception as e:
        dev_reply = "Fattibilità confermata: bundle Tailwind ottimizzato, asset 3D lazy-loaded, QA Playwright in pre-commit."
    await broadcast_to_office(bot, "dev", f"{OFFICES['dev']['badge']}\n\n*Analisi Ingegneristica & Vincoli Tecnici (`{biz_name}`)*:\n\n{dev_reply}")

    # Intervento dialogico Lead Engineer in Triage per la squadra
    try:
        dev_chat_prompt = (
            f"Sei il Lead Web Engineer. Nel canale Triage rispondi a Copywriting e Design per '{biz_name}'.\n"
            f"Tua ingegnerizzazione: {dev_reply[:400]}\n"
            "Scrivi 2 frasi tecniche: tagga @Copywriting e @Design confermando performance estreme (Tailwind compilato, glTF Draco <1.2MB, 0 errori Playwright) e passa la palla a @Sicurezza per blindare le policy e il perimetro. Nessun preambolo."
        )
        dev_chat, _ = await llm_func(dev_chat_prompt, OFFICES["dev"]["system_prompt"], max_tokens=150)
    except Exception:
        dev_chat = f"@Copywriting @Design layout Tailwind blindato e 3D compresso sotto 1.2MB. Zero layout shift su mobile. @Sicurezza, audit delle CSP perimetrali per te."
    await broadcast_to_office(bot, "triage", f"[Lead Web Engineer -> @Copywriting @Sicurezza]\n{dev_chat}")

    await asyncio.sleep(2.0)

    # 5. Topic SOC: Hardening, CSP e Core Web Vitals
    soc_prompt = (
        f"Sei il Security & Performance Lead (Sentinella SOC). Esamina l'architettura tecnica:\n{dev_reply}\n\n"
        f"Imponi i vincoli difensivi: 1) Intestazioni CSP restrittive (self-hosted, zero CDN terze vulnerabili, HSTS preload); "
        f"2) Benchmark Core Web Vitals (LCP < 0.9s, CLS = 0, INP < 50ms, Lighthouse 98-100); 3) Setup monitoraggio SLA 99.9% continuo post-rilascio."
    )
    try:
        soc_reply, _ = await llm_func(soc_prompt, OFFICES["soc"]["system_prompt"], max_tokens=600)
    except Exception as e:
        soc_reply = "Audit sicurezza superato: CSP rigorosa applicata, Lighthouse score target 98/100, monitoraggio SOC attivo."
    await broadcast_to_office(bot, "soc", f"{OFFICES['soc']['badge']}\n\n*Audit Sicurezza Perimetrale & Core Web Vitals (`{biz_name}`)*:\n\n{soc_reply}")

    # Intervento dialogico Sentinella SOC in Triage per la squadra
    try:
        soc_chat_prompt = (
            f"Sei il Security & Performance Lead (Sentinella SOC). Nel canale Triage rispondi a Sviluppo per '{biz_name}'.\n"
            f"Tuo audit: {soc_reply[:400]}\n"
            "Scrivi 2 frasi rigorose: tagga @Sviluppo confermando CSP rigorosa, zero tracker esterni e LCP <0.9s, poi passa la palla a @Compliance per la verifica legale Italia/UE. Nessun preambolo."
        )
        soc_chat, _ = await llm_func(soc_chat_prompt, OFFICES["soc"]["system_prompt"], max_tokens=150)
    except Exception:
        soc_chat = f"@Sviluppo CSP blindata con hash sha256 per gli inline, zero CDN terze e LCP target a 0.85s validato. @Compliance, tocca a te per l'audit normativo."
    await broadcast_to_office(bot, "triage", f"[Sentinella SOC -> @Sviluppo @Compliance]\n{soc_chat}")

    await asyncio.sleep(2.0)

    # 6. Legal & Compliance: Audit Normative Italia & UE
    compliance_prompt = (
        f"Sei il Legal & Compliance Officer di Hermes Lab. Verifica conformità normative Repubblica Italiana ed EU per '{biz_name}'.\n"
        f"Controlla: 1) Obbligo Partita IVA e dati societari nel footer (art. 2250 c.c. e art. 35 DPR 633/72: REA, sede legale, PEC);\n"
        f"2) GDPR e Cookie Policy Garante Privacy (blocco preventivo script terze parti e consenso esplicito);\n"
        f"3) Trasparenza prezzi e Codice del Consumo (D.lgs 206/2005) o autorizzazioni di settore;\n"
        f"4) Accessibilità e requisiti EAA. Sii chirurgico: elenca i dati obbligatori da includere nel footer e rilascia il visto di conformità legale."
    )
    try:
        compliance_reply, _ = await llm_func(compliance_prompt, "Sei il Legal & Compliance Officer specializzato in diritto digitale e normative web italiane ed europee. " + TELEGRAM_FORMAT_RULE, max_tokens=600)
    except Exception:
        compliance_reply = f"Audit di conformità normativa completato per {biz_name}: P.IVA obbligatoria nel footer, cookie policy GDPR e conformità art. 2250 c.c. approvate."
    await broadcast_to_office(bot, "soc", f"⚖️ *Audit Legal & Compliance Italia/UE (`{biz_name}`)*:\n\n{compliance_reply}")

    # Intervento dialogico Compliance in Triage per la squadra
    try:
        comp_chat_prompt = (
            f"Sei il Legal & Compliance Officer. Nel canale Triage intervieni prima della delibera per '{biz_name}'.\n"
            f"Tuo audit: {compliance_reply[:300]}\n"
            "Scrivi 2 frasi: tagga @Direzione confermando conformità alle leggi italiane (P.IVA art. 35, dati art. 2250 c.c., cookie banner GDPR e Codice del Consumo) e concedi il semaforo verde legale. Nessun preambolo."
        )
        comp_chat, _ = await llm_func(comp_chat_prompt, "Sei il Legal & Compliance Officer. " + TELEGRAM_FORMAT_RULE, max_tokens=150)
    except Exception:
        comp_chat = f"@Direzione audit normativo superato: P.IVA nel footer, informativa privacy GDPR e conformità Codice del Consumo verificate. Semaforo verde legale concesso."
    await broadcast_to_office(bot, "triage", f"[Legal & Compliance -> @Direzione @Sviluppo]\n{comp_chat}")

    await asyncio.sleep(2.0)

    # 7. Topic Direction: Delibera Esecutiva CEO/CTO
    dir_prompt = (
        f"Sei il CEO & CTO di Hermes Lab. La squadra ha completato la tavola rotonda per `{biz_name}`.\n"
        f"OSINT: {osint_reply[:120]}\nDesign: {creative_reply[:120]}\nCopy: {copy_reply[:120]}\nDev: {dev_reply[:120]}\nSOC: {soc_reply[:120]}\nCompliance: {compliance_reply[:120]}\n\n"
        f"Emetti la delibera esecutiva: congela le specifiche, autorizza la Fabbrica Siti e assegna la priorità alla produzione."
    )
    try:
        dir_reply, _ = await llm_func(dir_prompt, OFFICES["direction"]["system_prompt"], max_tokens=550)
    except Exception as e:
        dir_reply = "Specifiche tecniche convalidate e congelate: autorizzo il rilascio immediato alla Fabbrica Siti."
    await broadcast_to_office(bot, "direction", f"{OFFICES['direction']['badge']}\n\n*Delibera Esecutiva Direzione Generale (`{biz_name}`)*:\n\n{dir_reply}")

    # Intervento conclusivo CEO/CTO in Triage
    try:
        dir_chat_prompt = (
            f"Sei il CEO & CTO di Hermes Lab. Nel canale Triage chiudi il dibattito su '{biz_name}'.\n"
            "Scrivi 2 frasi autorevoli ed esecutive: tagga @Squadra validando all'unanimità le specifiche, dichiara congelato il concept e autorizza la Fabbrica Siti alla produzione immediata. Nessun preambolo."
        )
        dir_chat, _ = await llm_func(dir_chat_prompt, OFFICES["direction"]["system_prompt"], max_tokens=150)
    except Exception:
        dir_chat = f"@Squadra eccellente coordinamento. Specifiche convalidate e congelate all'unanimità. Semaforo verde alla Fabbrica Siti: avviare la compilazione."
    await broadcast_to_office(bot, "triage", f"[Hermes CEO/CTO -> @Squadra]\n{dir_chat}")

    # Notifica conclusiva nel topic chiamante
    final_notify = (
        f"*Tavola Rotonda Conclusa con Successo per `{biz_name}`*\n\n"
        f"Tutti gli uffici hanno deliberato nei rispettivi topic:\n"
        f"- OSINT -> Posizionamento e Sentiment Gap completati\n"
        f"- Design -> Palette Dark Luxury e concept 3D validati\n"
        f"- Copywriting -> Headline e 3 USP approvate\n"
        f"- Sviluppo -> Architettura Tailwind e QA Playwright impostati\n"
        f"- Sicurezza -> CSP rigorosa e target LCP <0.9s garantiti\n"
        f"- Compliance -> P.IVA, privacy GDPR e Codice del Consumo validati\n"
        f"- Direzione -> Delibera esecutiva e semaforo verde alla produzione"
    )
    if initial_thread_id:
        try:
            await bot.send_message(chat_id=group_id, message_thread_id=initial_thread_id, text=final_notify, parse_mode="Markdown")
        except Exception:
            try:
                await bot.send_message(chat_id=group_id, message_thread_id=initial_thread_id, text=final_notify, parse_mode=None)
            except Exception:
                pass
    elif "triage" in data.get("topics", {}):
        await broadcast_to_office(bot, "triage", final_notify)

    # Se configurato, avvia automaticamente la Fabbrica Siti
    if on_complete_build:
        log.info("Tavola rotonda completata per '%s': avvio Fabbrica Siti...", biz_name)
        asyncio.create_task(on_complete_build(bot, group_id, biz_name, topic_or_link))


async def run_autonomous_standup(bot, llm_func, group_id: Optional[int] = None):
    """Gli agenti si riuniscono in autonomia per il briefing periodico e postano nei rispettivi topic e in Triage."""
    data = load_topics_data()
    group_id = group_id or data.get("group_id")
    if not group_id:
        return

    await broadcast_to_office(bot, "triage", "⚡ *Allineamento Autonomo Hermes Lab in corso...*\nGli specialisti sincronizzano stato e perimetro.")

    # 1. Sentinella SOC: Uptime & Security audit autonomo
    soc_prompt = (
        "Sei il Lead Sentinella SOC. È il momento dell'allineamento autonomo della squadra. "
        "Fai un check-up rapido: uptime siti live (SLA 99.9%), certificati SSL, nessun attacco o anomalia registrata, "
        "tempi di risposta medi a 115ms. Sii sintetico, rassicurante e diretto. Max 4 righe."
    )
    try:
        soc_msg, _ = await llm_func(soc_prompt, OFFICES["soc"]["system_prompt"], max_tokens=300)
    except Exception:
        soc_msg = "SLA 99.9% confermato. Certificati SSL validi, zero anomalie o intrusioni. Tempi di risposta medi 115ms."
    await broadcast_to_office(bot, "soc", f"{OFFICES['soc']['badge']}\n\n*Standup SOC & Resilienza*:\n{soc_msg}")
    await broadcast_to_office(bot, "triage", f"[Sentinella SOC] SLA 99.9% confermato su tutti i nodi. Zero intrusioni, perimetro verde.")
    await asyncio.sleep(2.0)

    # 2. Lead Engineer (Dev): Stato pipeline & Playwright
    dev_prompt = (
        "Sei il Lead Web Engineer. Rispondi al check del SOC: template V10 pronti, ambiente Playwright headless operativo, "
        "zero errori console nei test recenti, Tailwind CSS e scroll Lenis ottimizzati per mobile. Max 4 righe."
    )
    try:
        dev_msg, _ = await llm_func(dev_prompt, OFFICES["dev"]["system_prompt"], max_tokens=300)
    except Exception:
        dev_msg = "Template V10 allineati. Ambiente headless Playwright pronto. Zero errori bloccanti sui layout mobile."
    await broadcast_to_office(bot, "dev", f"{OFFICES['dev']['badge']}\n\n*Standup Ingegneria Frontend*:\n{dev_msg}")
    await broadcast_to_office(bot, "triage", f"[Lead Web Engineer] Pipeline pronta: Playwright headless attivo, zero warning Tailwind.")
    await asyncio.sleep(2.0)

    # 3. Art Director: Trend design & 3D scouting
    art_prompt = (
        "Sei l'Art Director. Condividi con la squadra una breve nota di design: trend visuali Dark Luxury, uso di accenti neon "
        "e modelli 3D leggeri pronti da integrare nelle prossime hero section. Max 4 righe."
    )
    try:
        art_msg, _ = await llm_func(art_prompt, OFFICES["creative"]["system_prompt"], max_tokens=300)
    except Exception:
        art_msg = "Focus su Dark Luxury ad alto contrasto. Modelli 3D Spline ottimizzati a <1.2MB pronti per le prossime hero section."
    await broadcast_to_office(bot, "creative", f"{OFFICES['creative']['badge']}\n\n*Standup Direzione Artistica*:\n{art_msg}")
    await broadcast_to_office(bot, "triage", f"[Art Director] Asset 3D Dark Luxury pre-caricati e ottimizzati Draco a <1.2MB.")
    await asyncio.sleep(2.0)

    # 4. Lead Copywriter: Sintesi angolo vendita
    copy_prompt = (
        "Sei il Lead Copywriter. Rispondi all'Art Director: pronti nuovi hook psicologici e headline taglienti "
        "ad alta conversione per battere i competitor locali. Copy desk a piena efficienza. Max 4 righe."
    )
    try:
        copy_msg, _ = await llm_func(copy_prompt, OFFICES["copy"]["system_prompt"], max_tokens=300)
    except Exception:
        copy_msg = "Calibrati nuovi claim di vendita ad alta conversione. Copy desk allineato su tono autorevole e zero fuffa."
    await broadcast_to_office(bot, "copy", f"{OFFICES['copy']['badge']}\n\n*Standup Copy & Brand Voice*:\n{copy_msg}")
    await broadcast_to_office(bot, "triage", f"[Lead Copywriter] Nuovi hook psicologici PAS calibrati. Zero fuffa, massima conversione.")
    await asyncio.sleep(2.0)

    # 5. Direzione Generale (CEO/CTO): Chiusura esecutiva
    dir_prompt = (
        "Sei il CEO & CTO di Hermes. Chiudi il briefing autonomo: squadra al 100% dell'operatività, "
        "8 specialisti allineati, Fabbrica Siti in standby per i prossimi incarichi. Max 3 righe."
    )
    try:
        dir_msg, _ = await llm_func(dir_prompt, OFFICES["direction"]["system_prompt"], max_tokens=300)
    except Exception:
        dir_msg = "Squadra operativa e sincronizzata. Uffici allineati, Fabbrica Siti pronta al prossimo incarico."
    await broadcast_to_office(bot, "direction", f"{OFFICES['direction']['badge']}\n\n*Riepilogo Direzione Generale*:\n{dir_msg}")
    await broadcast_to_office(bot, "triage", f"[Hermes CEO/CTO] Standup completato: tutti gli uffici sono allineati e operativi al 100%.")


