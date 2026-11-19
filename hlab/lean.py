"""Via leggera: le richieste di ogni giorno con il MINIMO di token.

Il peso nascosto e' il contorno della CLI degli agenti (~18.000 token a ogni giro, tra istruzioni e strumenti): per una domanda di stato
e' uno spreco. Qui l'ordine e' invertito: prima il codice, poi (solo se serve) un modello con un prompt minuscolo.
  - stato, scadenze, progetti fermi, lead, scheda cliente: strumenti diretti + modelli di testo   -> 0 token
  - preventivo: le voci si leggono con regole; se non bastano, UNA chiamata da <= 1.500 token     -> 0 o ~1.000 token
  - nuovo lead: si crea una PROPOSTA (nessuna scrittura sul CRM)                                   -> 0 token
  - sito: UNA sola chiamata per lo spec JSON, tetto rigido di 10.000 token (entrata + uscita)     -> <= 10.000 token
  - qualsiasi altra cosa: una classificazione da <= 600 token, poi si sceglie un percorso sopra.
Ogni richiesta registra i token spesi (events.db) e rispetta il suo tetto: superarlo e' un errore, non un'abitudine.
Per lavori che richiedono ragionamento tra piu' specialisti restano gli agenti (`hlab run`).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from . import commercial as co
from .events import Events
from .translate import estimate_tokens

FACTS_TTL_S = 120                                                   # per quanto tempo si riusa il riassunto del CRM dato alle conversazioni
CAPS = {"status": 0, "deadlines": 0, "stale": 0, "leads": 0, "client": 0, "lead_create": 0, "quote": 1500, "classify": 900, "site": 10_000, "chat": 800}
CLASSIFY_SYS = ("Classifica la richiesta. Rispondi SOLO con una parola tra: status, deadlines, stale, leads, chat. "
                "Rispondi status, deadlines, stale o leads SOLO se il messaggio chiede esplicitamente numeri, elenchi o dati aggiornati su quell argomento. "
                "Usa chat per conversazione, consigli, idee, lavori da fare, domande a un collega e in caso di dubbio. "
                "Esempi: \"come va l'agenzia?\" -> status; \"cosa scade questa settimana?\" -> deadlines; \"ci sono lavori bloccati?\" -> stale; "
                "\"quanti contatti nuovi abbiamo?\" -> leads; \"scrivi una mail al cliente\" -> chat; \"che ne pensi del logo?\" -> chat; \"ciao, come stai?\" -> chat. "
                "Rispondi con la sola parola, niente altro.")


def label_of(raw: str | None) -> str:
    """L'ULTIMA etichetta citata nella risposta: se il modello ragiona ad alta voce prima di rispondere, la risposta vera e' in fondo."""
    found = re.findall(r"\b(status|deadlines|stale|leads|chat)\b", (raw or "").lower())
    return found[-1] if found else ""


class BudgetExceeded(Exception):
    pass


@dataclass
class Budget:
    cap: int
    used: int = 0
    calls: int = 0

    @property
    def remaining(self) -> int:
        return self.cap - self.used

    def check(self, est_in: int, want_out: int) -> None:
        if self.cap <= 0 or est_in + min(want_out, 200) > self.remaining:
            raise BudgetExceeded(f"tetto di {self.cap} token: ne restano {max(0, self.remaining)}, la richiesta ne vuole almeno {est_in + min(want_out, 200)}")

    def charge(self, tokens_in: int, tokens_out: int) -> None:
        self.used += tokens_in + tokens_out
        self.calls += 1
        if self.used > self.cap:
            raise BudgetExceeded(f"tetto di {self.cap} token superato ({self.used})")


@dataclass
class Answer:
    text: str
    intent: str
    tokens: int = 0
    calls: int = 0


# ---- riconoscimento dell'intento: regole, niente modello ----------------------------------------
def detect(text: str) -> tuple[str, dict]:
    t = text.lower().strip()
    if re.search(r"\b(crea|aggiungi|nuovo|inserisci)\b.*\blead\b|\blead\b.*\b(nuovo|da creare)\b", t):
        return "lead_create", {}
    if re.search(r"\bpreventiv", t):
        return "quote", {}
    if re.search(r"\b(sito|landing)\b.*\b(crea|fai|realizza|genera|costruisci)\b|\b(crea|fai|realizza|genera|costruisci)\b.*\b(sito|landing)\b", t) and "preventiv" not in t:
        return "site", {}
    if re.search(r"scadenz|cosa scade|in ritardo", t):
        return "deadlines", {"days": int(m.group(1)) if (m := re.search(r"(\d+)\s*(?:giorni|gg)", t)) else 30}
    if re.search(r"\bfermi\b|non aggiornat|bloccat", t):
        return "stale", {"days": int(m.group(1)) if (m := re.search(r"(\d+)\s*(?:giorni|gg)", t)) else 14}
    if re.search(r"\blead\b", t):
        return "leads", {}
    if re.search(r"\b(stato|status|come va|situazione|briefing|riepilogo)\b", t):
        return "status", {}
    m = re.search(r"(?:cliente|scheda|cosa sai di)\s+([A-Za-zÀ-ú' .-]{3,40})", text, re.I)
    if m:
        return "client", {"name": m.group(1).strip(" .?")}
    return "unknown", {}


# ---- estrazione dei dati con regole ------------------------------------------------------------
NUM = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?"


def cents(s: str) -> int:
    s = s.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    return int(round(float(s) * 100))


def parse_quote(text: str) -> dict | None:
    """'preventivo per Studio Fenio: landing con design 1500 euro, testi SEO 300, hosting 250, manutenzione 20 al mese' -> voci."""
    cur = "USD" if re.search(r"\$|\busd\b|dollar", text, re.I) else "EUR"
    ptype = next((k for k, pat in (("manutenzione", r"manutenzion"), ("mobile", r"app mobile|\bios\b|android"), ("webapp", r"web ?app|gestionale"),
                                   ("landing", r"landing|sito|vetrina")) if re.search(pat, text, re.I)), "landing")
    body = text.split(":", 1)[1] if ":" in text else text
    items = []
    for seg in re.split(r"[;,\n]| e (?=[A-Za-zÀ-ú])", body):
        m = re.search(rf"(?P<name>[A-Za-zÀ-ú][^\d€$]*?)\s*[-–]?\s*(?:€|\$)?\s*(?P<n>{NUM})\s*(?:€|\$|euro|eur|usd|dollari)?\s*(?P<m>al mese|/mese|mensil\w*)?\s*$", seg.strip(), re.I)
        if m and len(m["name"].strip()) >= 3:
            name = re.sub(r"^(una|un|il|la|lo|con|di|per)\s+", "", m["name"].strip(" -–"), flags=re.I)
            items.append({"name": name[:60].capitalize(), "price_cents": cents(m["n"]), "monthly": bool(m["m"])})
    if not items:
        return None
    c = re.search(r"per\s+([A-ZÀ-Ú][\w'.À-ú-]*(?:\s+[A-ZÀ-Ú][\w'.À-ú-]*){0,3})", text)
    return {"client": c.group(1) if c else "Cliente da indicare", "project_type": ptype, "currency": cur, "items": items}


EXTRACT_SYS = ('Estrai un preventivo. Rispondi SOLO JSON: {"client":str,"project_type":"landing|webapp|mobile|manutenzione|altro","currency":"EUR|USD",'
               '"items":[{"name":str,"price_cents":int,"monthly":bool}]}. Importi in centesimi.')


def repair_json(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    txt = re.sub(r",\s*([}\]])", r"\1", m.group(0))
    try:
        return json.loads(txt)
    except ValueError:
        return None


def history_block(history: list | None, max_chars: int = 1000) -> str:
    """Gli ultimi scambi come righe "Chi: testo", i piu' recenti in fondo, tagliati per restare nel tetto di token (si scartano i piu' vecchi)."""
    lines, size = [], 0
    for who, txt in reversed(history or []):
        line = f"{who}: {' '.join(str(txt).split())[:220]}"
        if size + len(line) > max_chars:
            break
        lines.append(line)
        size += len(line) + 1
    return "\n".join(reversed(lines))


# ---- il motore ---------------------------------------------------------------------------------
class Lean:
    def __init__(self, db=None, events: Events | None = None, complete=None):
        self.db, self.ev, self._complete = db, events or Events(), complete
        self._facts_cache: tuple[float, str] | None = None

    async def _facts(self) -> str:
        """Riassunto REALE del CRM per i modelli che conversano: clienti, progetti, scadenze, lead. Sono sole letture (0 token) con una cache di 2 minuti.
        Solo nomi e stati, nessun contatto. "" se il CRM non risponde: allora la chat dice di non avere i dati invece di inventarli."""
        if self._facts_cache and time.time() - self._facts_cache[0] < FACTS_TTL_S:
            return self._facts_cache[1]
        try:
            clients = await self._call("list_clients", limit=50)
            projects = await self._call("list_projects", limit=40)
            deadlines = await self._call("list_deadlines", days=30)
            leads = await self._call("list_leads", limit=50)
        except Exception:
            return ""
        by: dict[str, int] = {}
        for r in leads:
            by[r.get("status") or "?"] = by.get(r.get("status") or "?", 0) + 1
        proj = [f"{p['name']} [{p.get('client') or '-'}] {p.get('status') or '?'}" + (f", scadenza {p['deadline']}" if p.get("deadline") else "") for p in projects[:8]]
        text = "\n".join([
            f"Clienti ({len(clients)}): " + (", ".join(c["name"] for c in clients[:12]) or "nessuno"),
            "Progetti: " + ("; ".join(proj) or "nessuno"),
            "Scadenze nei prossimi 30 giorni: " + ("; ".join(f"{d['due']} {d['client']} ({d['title']})" for d in deadlines[:5]) or "nessuna"),
            "Lead: " + (", ".join(f"{n} {k}" for k, n in by.items()) or "nessuno"),
        ])[:1400]
        self._facts_cache = (time.time(), text)
        return text

    async def _llm(self, prompt: str, system: str, budget: Budget, max_tokens: int, model: str = "light") -> str:
        if self._complete:                                           # iniettabile nei test
            budget.check(estimate_tokens({"p": prompt + system}), max_tokens)
            out = await self._complete(prompt, system, max_tokens)
            budget.charge(estimate_tokens({"p": prompt + system}), estimate_tokens({"o": out}))
            return out
        from .llm import complete
        return await complete(prompt, system, model=model, max_tokens=max_tokens, budget=budget)

    def _tools(self) -> dict:
        from .tools import crm_tools
        if self.db is None:
            self.db = crm_tools.PgDb()
        return {t.name: t for t in crm_tools.make_tools(self.db)}

    async def _call(self, name: str, **args):
        r = await self._tools()[name].handler(args)
        txt = r["content"][0]["text"]
        if r.get("is_error"):
            raise RuntimeError(txt)
        return json.loads(txt)

    async def ask(self, text: str, history: list | None = None, persona: str | None = None) -> Answer:
        """`history` = gli ultimi messaggi della conversazione [(chi, testo)], `persona` = chi risponde. Servono solo dove c'e' un modello (riconoscere a cosa si
        riferisce "e quelli di ieri?" e rispondere in prima persona): le richieste riconosciute dalle regole restano a 0 token e non li usano."""
        intent, args = detect(text)
        budget = Budget(CAPS.get(intent, CAPS["classify"]) or 10 ** 9)   # 0 = percorso senza modello: nessun addebito ma nessuna chiamata permessa
        no_llm = CAPS.get(intent, 1) == 0
        pre_used = pre_calls = 0                                     # token della classificazione, se c'e' stata
        if intent == "unknown":
            budget = Budget(CAPS["classify"])
            hist = history_block(history, 500)
            # Misurato sul server (12 casi): con 12 token la risposta era SEMPRE vuota (un modello che ragiona finisce i token prima di rispondere);
            # modello strong + 300 token + esempi nel prompt = 11/12 giuste, 0 vuote, ~0,4 s.
            raw = await self._llm(f"Conversazione finora:\n{hist}\n\nNuovo messaggio: {text[:600]}" if hist else text[:600], CLASSIFY_SYS, budget, 300, model="strong")
            word = label_of(raw)
            # Il modello leggero sceglie solo tra le letture a 0 token e la chat. Preventivi, siti, lead e schede cliente partono soltanto dalle regole
            # esplicite di detect(): un classificatore gratuito che le scambia per una frase qualunque produrrebbe bozze senza senso.
            intent = word if word in ("status", "deadlines", "stale", "leads") else "chat"
            if intent == "chat":
                chist = history_block(history, 1000)
                talk = bool(chist or persona)
                facts = await self._facts() if talk else ""
                budget = Budget(CAPS["chat"] + (2600 if facts else 1200 if talk else 0), used=budget.used, calls=budget.calls)     # una conversazione pesa piu' di una frase secca
                try:
                    if talk:
                        system = ((persona or "Sei Hermes, assistente di un'agenzia web.") + " Rispondi in italiano, massimo 4 frasi, in modo concreto. "
                                  "Usa la conversazione per capire a cosa si riferisce il nuovo messaggio. "
                                  "Non citare mai statistiche, percentuali, benchmark o risultati medi di settore: parla in termini qualitativi, a meno che il numero sia nei dati sotto. ")
                        if facts:
                            system += ("Qui sotto trovi i DATI REALI letti ora dal CRM: sono l'unica fonte per nomi di clienti, progetti, scadenze e numeri dell'agenzia; "
                                       "non inventare altro. Se un dato non c'e', dillo e suggerisci una richiesta esplicita ('scheda cliente <nome>', 'lead', 'scadenze'). "
                                       "Consigli, idee e bozze di testo sono benvenuti.\nDATI REALI:\n" + facts)
                        else:
                            system += ("In questa risposta NON hai accesso al CRM: non inventare mai nomi di clienti, progetti, prezzi, date o numeri dell'agenzia. "
                                       "Se servono dati reali dillo chiaramente e suggerisci di chiederli con una richiesta esplicita "
                                       "('situazione', 'lead', 'scadenze', 'cliente <nome>'). Consigli, idee e bozze di testo sono invece benvenuti.")
                        prompt = f"Conversazione finora:\n{chist}\n\nNuovo messaggio: {text[:600]}" if chist else text[:600]
                        reply = await self._llm(prompt, system, budget, 700, model="strong")
                        if not reply.strip():                        # un modello che ragiona puo' finire i token prima di rispondere: si riprova con piu' spazio
                            reply = await self._llm(prompt, system, budget, 1000, model="strong")
                    else:
                        reply = await self._llm(text[:600], "Sei Hermes, assistente di un'agenzia web. Rispondi in italiano, massimo 3 frasi.", budget, 200)
                except BudgetExceeded as e:
                    return self._done(Answer(f"Fermato per il tetto di token: {e}.", "chat", budget.used, budget.calls))
                return self._done(Answer(reply.strip(), "chat", budget.used, budget.calls))
            args = detect(text)[1]
            no_llm = CAPS.get(intent, 1) == 0
            pre_used, pre_calls = budget.used, budget.calls
            budget = Budget(CAPS.get(intent) or 10 ** 9)
        handler = getattr(self, "_" + intent)
        try:
            out = await handler(text, args, budget)
        except BudgetExceeded as e:
            return self._done(Answer(f"Fermato per il tetto di token: {e}.", intent, budget.used + pre_used, budget.calls + pre_calls))
        if no_llm and budget.calls:
            raise AssertionError(f"il percorso '{intent}' non deve usare il modello")
        return self._done(Answer(out, intent, budget.used + pre_used, budget.calls + pre_calls))

    def _done(self, a: Answer) -> Answer:
        self.ev.log("lean", "lean", a.intent, True, "", tokens=a.tokens)
        return a

    # ---- percorsi senza modello ----
    async def _status(self, text, args, budget) -> str:
        d, s = await self._call("list_deadlines", days=14), await self._call("stale_projects", days=14)
        p, l = await self._call("list_projects", limit=60), await self._call("list_leads", limit=50)
        by_status: dict[str, int] = {}
        for r in p:
            by_status[r.get("status") or "?"] = by_status.get(r.get("status") or "?", 0) + 1
        lead_by: dict[str, int] = {}
        for r in l:
            lead_by[r.get("status") or "?"] = lead_by.get(r.get("status") or "?", 0) + 1
        urgent = f"{len(d)} in scadenza entro 14 giorni" if d else "nessuna scadenza entro 14 giorni"
        return (f"Progetti: {', '.join(f'{n} {k}' for k, n in by_status.items()) or 'nessuno'}. {urgent}. "
                f"Fermi da 14+ giorni: {len(s)}. Lead: {', '.join(f'{n} {k}' for k, n in lead_by.items()) or 'nessuno'}.")

    async def _deadlines(self, text, args, budget) -> str:
        rows = await self._call("list_deadlines", days=args.get("days", 30))
        if not rows:
            return f"Nessuna scadenza nei prossimi {args.get('days', 30)} giorni."
        return "\n".join(f"- {r['due']}  {r['client']} / {r['title']} ({r['kind']})" for r in rows[:15])

    async def _stale(self, text, args, budget) -> str:
        rows = await self._call("stale_projects", days=args.get("days", 14))
        return "\n".join(f"- {r['client']} / {r['name']}: fermo da {r['days_idle']} giorni ({r['status']})" for r in rows[:15]) or "Nessun progetto fermo."

    async def _leads(self, text, args, budget) -> str:
        rows = await self._call("list_leads", limit=10)
        return "\n".join(f"- {r['name']} ({r.get('category') or '-'}): {r['status']}, punteggio {r.get('score')}" for r in rows) or "Nessun lead."

    async def _client(self, text, args, budget) -> str:
        r = await self._call("get_client", client=args["name"])
        if r.get("ambiguo"):
            return "Piu' clienti corrispondono: " + ", ".join(c["name"] for c in r["candidati"])
        c, projs = r["cliente"], r["progetti"]
        return (f"{c['name']} — contatto {c.get('contact_name') or '-'} — {len(projs)} progetti: "
                + "; ".join(f"{p['name']} ({p['status']}, scadenza {p.get('deadline') or '-'})" for p in projs) + f". Task aperti: {r['task_aperti']}.")

    async def _lead_create(self, text, args, budget) -> str:
        phone = re.search(r"(\+?\d[\d ]{7,}\d)", text)
        mail = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
        name = re.sub(r"(?i)\b(crea|aggiungi|nuovo|inserisci|un|il|lead)\b", " ", re.sub(r"(\+?\d[\d ]{7,}\d)|[\w.+-]+@[\w-]+\.[\w.-]+", " ", text)).strip(" :,-")
        if len(name) < 3:
            return "Dimmi almeno il nome dell'attivita' (e se li hai telefono o email)."
        payload = json.dumps({"name": name[:80], "phone": phone.group(1) if phone else None, "email": mail.group(0) if mail else None}, ensure_ascii=False)
        pid = self.ev.add_proposal("lean", "lead", "nuovo", "create", payload, "richiesta di Lorenzo: nuovo lead")
        return f"Proposta #{pid} di nuovo lead «{name[:80]}» registrata: il CRM non e' stato toccato, la approvi tu (`hlab proposals`)."

    # ---- preventivo: regole, poi (se servono) una chiamata minuscola ----
    async def _quote(self, text, args, budget) -> str:
        q = parse_quote(text)
        if not q:
            raw = await self._llm(text[:900], EXTRACT_SYS, budget, 400)
            q = repair_json(raw)
            if not q or not q.get("items"):
                return "Non riesco a leggere le voci: scrivi per esempio «preventivo per Rossi: landing 1500 euro, testi SEO 300»."
        items = [{"name": str(i["name"]), "price_cents": int(i["price_cents"]), "monthly": bool(i.get("monthly"))} for i in q["items"]]
        chk = co.check_quote(q.get("project_type", "landing"), q.get("currency", "EUR"), items)
        if chk["errors"]:
            return "Preventivo non valido: " + "; ".join(chk["errors"])
        calc, cur = co.compute_quote(items), q.get("currency", "EUR")
        did = self.ev.add_draft("lean", "preventivo", q["client"], None, cur, {"project_type": q["project_type"], "items": items, "quote": calc,
                                                                               "warnings": chk["warnings"]}, "creato da `ask`")
        lines = [f"Preventivo per {q['client']} ({q['project_type']}, {cur}) — bozza #{did}, non inviata",
                 f"Una tantum {co.money(calc['one_off_cents'], cur)}" + (f" + canone {co.money(calc['monthly_cents'], cur)}/mese" if calc["monthly_cents"] else "")]
        lines += [f"  {t['tranche']}: {co.money(t['total_cents'], cur)} (compenso {co.money(t['net_cents'], cur)}"
                  f"{', bollo ' + co.money(t['stamp_duty_cents'], cur) if t['stamp_duty_cents'] else ''})" for t in calc["tranches"]]
        lines += [f"  ! {w}" for w in chk["warnings"]]
        return "\n".join(lines)

    # ---- sito: UNA chiamata, tetto rigido ----
    async def _site(self, text, args, budget) -> str:
        spec, used = await site_spec(text, self._llm_for_site())
        budget.used, budget.calls = used, 1
        return f"Spec del sito pronto ({used} token su {SITE_CAP}): {spec['brand']['name']} — {len(spec['sections'])} sezioni. Il rendering usa i template V10 (nessun altro token)."

    def _llm_for_site(self):
        async def call(prompt, system, max_tokens, budget):
            return await self._llm(prompt, system, budget, max_tokens, model="strong")
        return call


# ---- sito: lo spec JSON in un giro solo, entro 10.000 token ----------------------------------------
SITE_CAP = 10_000
SITE_SECTIONS = ("hero_image|hero_video|hero_shader, text_reveal, horizontal_gallery, stacked_cards, image_zoom, marquee, stats, split, services, cta, "
                 "statement, scene_moment, object_showcase")
SITE_SYS = ("Sei il Creative Director. Restituisci SOLO JSON (niente codice): "
            '{"repo_name":"kebab","brand":{"name","tagline","sector_en","mission","keywords":[5],"display","body"},'
            '"palette":{"bg","fg","accent","muted","surface"},"seo":{"description"},"contact":{"email","phone","address","hours"},'
            '"sections":[{"type":"<tipo>", ...campi}],"experience":{"mode":"classic|scene3d|cinema"},"todo":[str]}. '
            f"Tipi di sezione ammessi: {SITE_SECTIONS}. 7-9 sezioni, copy italiano breve, titoli max 8 parole, nessun dato di contatto inventato "
            "(se manca: vuoto e una voce in todo). Niente immagini da generare: solo il campo image come prompt inglese di 5 parole.")


async def site_spec(brief: str, call, cap: int = SITE_CAP) -> tuple[dict, int]:
    """UNA chiamata. Entrata ~1.200 token di istruzioni + brief tagliato; uscita limitata. Se il JSON e' sporco si ripara col codice: mai una seconda chiamata."""
    budget = Budget(cap)
    brief = brief.strip()[:2500]
    raw = await call(brief, SITE_SYS, min(5500, cap - estimate_tokens({"p": brief + SITE_SYS}) - 300), budget)
    spec = repair_json(raw)
    if not spec or not spec.get("sections") or not spec.get("brand"):
        raise ValueError("lo spec del sito non e' leggibile (nessun secondo tentativo: i token sono finiti)")
    return spec, budget.used
