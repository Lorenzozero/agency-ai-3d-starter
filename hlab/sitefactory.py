"""Fabbrica di siti (ex Hermes V10) dentro hlab.

Divisione del lavoro, un agente per passo:
  direttore-creativo : legge il cliente e scrive lo SPEC (JSON: marca, palette, testi, sezioni) -> `save_spec`
  costruttore-siti   : costruisce dai template, prova nel browser (desktop+mobile), auto-correzione -> `build` e `report`
  deployer           : propone la PUBBLICAZIONE (kind `site_publish` in powers.py); online solo dopo l'approvazione di Lorenzo
Il lavoro pesante (template Jinja, immagini, Chromium) gira nell'ambiente del vecchio bot tramite `hermes_v10_cli.py`: qui solo i confini
(nome ammesso, dimensioni, tetto di costruzioni al giorno, timeout). Gli agenti non scrivono HTML e non vedono i segreti.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import config

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{2,59}$")

# Scheletro OBBLIGATORIO di ogni pagina (dalla lavagna di Lorenzo), nell'ordine. Ogni sezione dello spec porta un `role`.
SPINE = ("attenzione", "problema", "soluzione", "offerta", "usp", "prova", "cta", "faq")
ROLE_TYPES = {"attenzione": ("hero_shader", "hero_image", "hero_video"), "offerta": ("offer",), "faq": ("faq",), "cta": ("cta",)}   # gli altri ruoli accettano qualunque tipo
MAX_EXTRA = 4
EDIT_GUIDE = """NUOVO SITO O MODIFICA? Decidi PRIMA di scrivere qualsiasi cosa.
1. Chiama `site_existing_list`: sono i siti GIA' pubblicati. Se il cliente o il progetto ha gia' un sito li' (stesso nome o stesso indirizzo del progetto nel CRM), la richiesta e' una MODIFICA.
2. MODIFICA: `site_open(repo)` scarica quel sito (spec e immagini). Lavori sullo spec esistente, cambi solo cio' che serve e `site_spec_save` lo salva SUL SUO REPO: il sito resta lo stesso, stesso indirizzo.
   Non crei mai un secondo sito per un cliente che ne ha gia' uno, a meno che Lorenzo lo chieda esplicitamente ("fanne uno nuovo").
3. Per una modifica tieni invariati marca, palette, font, testi e prompt delle immagini che non devi toccare (le immagini con prompt identico vengono riusate gratis).
4. Se il sito esistente non ha lo scheletro obbligatorio (nessun `role` nelle sezioni) e Lorenzo vuole adeguarlo o rifarlo, aggiungi i ruoli e le sezioni mancanti. Se invece e' un RITOCCO piccolo (un colore, un telefono, una frase) usa `ritocco: true` in `site_spec_save`: lo scheletro non viene imposto e la struttura resta com'e'.
5. Se non sei sicuro di quale sito si tratti, chiedi: non indovinare.
6. Nuovo sito (nessun sito esistente per quel cliente): segui lo scheletro qui sotto."""

FUNNEL_GUIDE = """SCHEMA OBBLIGATORIO DELLA PAGINA (non negoziabile). Ogni sezione ha un campo "role" e l'ordine e' questo:
1. attenzione  (un hero_*): cattura in 3 secondi. Promessa chiara, nessuna spiegazione.
2. problema: il problema o il desiderio del cliente finale, con parole sue. Usa text_reveal, statement o stacked_cards.
3. soluzione: la transizione dal problema alla risposta: come lavora l'attivita', in pochi passi. Usa split, stacked_cards o services.
4. offerta (type "offer"): cosa si ottiene, cosa e' incluso, a che condizioni (prezzo o "su preventivo").
5. usp: perche' proprio questa attivita': 2-4 motivi di differenza VERI. Usa stats (solo numeri forniti), split o stacked_cards.
6. prova: prova sociale. type "proof" SOLO con recensioni reali fornite; altrimenti stats/split con garanzie verificabili (es. "preventivo gratuito", "risposta entro 24 ore") e una voce in todo per raccogliere recensioni vere.
7. cta (type "cta"): UN solo invito all'azione, con verbo + beneficio.
8. faq (type "faq", ultima sezione): 4-8 domande che tolgono le obiezioni (prezzo, tempi, come funziona, garanzie, cosa succede dopo).
SEZIONI EXTRA SU MISURA: puoi aggiungere fino a 4 sezioni con "role": "extra" tra "attenzione" e "cta", SOLO se servono a quello che il cliente vende
(es. menu del ristorante, catalogo prodotti, listino, galleria dei lavori, area servita, orari). Ogni extra ha un campo "extra_why": una riga che dice quale servizio/prodotto del cliente giustifica la sezione.
Niente extra "per fare scena". Se il cliente non ha prodotti/servizi che lo richiedono, zero extra."""


FAKE_ACCENT = re.compile(r"[A-Za-z]'(?=[\s.,;:!?)]|$)")                  # "caffe'", "citta'", "e'": accento scritto come apostrofo
NO_ACCENT_CHECK = {"image", "video_query", "art_direction", "sector_en", "model_prompt", "prompt", "href", "repo_name", "type", "role", "extra_why"}


def fake_accents(obj, path: str = "") -> list[str]:
    """Testi del sito con l'accento scritto come apostrofo (caffe', perche', e'): sulla pagina pubblica si vedono male. Esclusi i campi tecnici e i prompt delle immagini (inglese)."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in NO_ACCENT_CHECK:
                out += fake_accents(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += fake_accents(v, f"{path}[{i}]")
    elif isinstance(obj, str) and FAKE_ACCENT.search(obj):
        out.append(f"{path}: «{obj[:50]}»")
    return out


def _find_section(spec: dict, key: str):
    """Sezione per ruolo (`attenzione`...), per posizione (`sezione-3`) o per extra (`extra-1`)."""
    secs = spec.get("sections") or []
    n = re.fullmatch(r"sezione-(\d+)", key)
    if n:
        i = int(n.group(1))
        return secs[i - 1] if 0 < i <= len(secs) else None
    m = re.fullmatch(r"extra-(\d)", key)
    if m:
        extras = [x for x in secs if x.get("role") == "extra"]
        i = int(m.group(1))
        return extras[i - 1] if 0 < i <= len(extras) else None
    return next((x for x in secs if x.get("role") == key), None)


def _prompt_ok(p) -> bool:
    return isinstance(p, str) and 25 <= len(p.strip()) <= 500 and not re.search(r"https?://|#[0-9a-fA-F]{3,8}", p)       # niente indirizzi e niente codici HEX: i colori si scrivono con il nome


def merge_art(slug: str, art) -> dict:
    """La direzione artistica dell'Art Director: frase di stile (`art_direction`), prompt delle immagini per ruolo, ricerca del video hero.
    Cambia SOLO i prompt delle immagini che esistono gia' e la frase di stile: niente tipi, testi, colori, font, collegamenti o ordine.
    Salva anche `art.json` e `art.md` (bibbia visiva, shot list e prompt video pronti per gli strumenti a mano) nella cartella di lavoro."""
    spec = load_spec(slug)
    if spec is None:
        raise ValueError("nessuno spec con questo nome: lo scrive prima il direttore creativo")
    if isinstance(art, str):
        try:
            art = json.loads(art)
        except json.JSONDecodeError as e:
            raise ValueError(f"la direzione artistica non e' JSON valido: {e}")
    if not isinstance(art, dict) or not art:
        raise ValueError("la direzione artistica deve essere un oggetto con almeno art_direction o immagini")
    applied, ignored = [], []
    ad = art.get("art_direction")
    if isinstance(ad, str) and len(ad.strip()) >= 15:
        spec.setdefault("brand", {})["art_direction"] = ad.strip()[:200]
        applied.append("art_direction")
    elif ad:
        ignored.append("art_direction (troppo breve: serve una frase di stile di almeno 15 caratteri)")
    for key, val in (art.get("immagini") or {}).items():
        sec = _find_section(spec, key)
        if sec is None:
            ignored.append(key)
            continue
        if isinstance(val, str):
            val = {"image": val}
        if not isinstance(val, dict):
            ignored.append(key)
            continue
        if "image" in val:
            if sec.get("image") is not None and _prompt_ok(val["image"]):
                sec["image"] = val["image"].strip()
                applied.append(f"{key}.image")
            else:
                ignored.append(f"{key}.image (la sezione non ha immagini o il prompt non e' valido: 25-500 caratteri, niente indirizzi, niente codici colore #HEX: usa i nomi dei colori)")
        for i, it in enumerate(val.get("items") or []):
            old = (sec.get("items") or [])
            if i < len(old) and isinstance(it, dict) and old[i].get("image") is not None and _prompt_ok(it.get("image")):
                old[i]["image"] = it["image"].strip()
                applied.append(f"{key}.items[{i}].image")
            elif isinstance(it, dict) and it.get("image"):
                ignored.append(f"{key}.items[{i}]")
    hid = re.sub(r"\D", "", str((art.get("video") or {}).get("hero_id") or "")) if isinstance(art.get("video"), dict) else ""
    if hid:
        hero = (spec.get("sections") or [{}])[0]
        if hero.get("type") == "hero_video":
            hero["video_id"] = hid[:12]
            applied.append("video.hero_id")
        else:
            ignored.append("video.hero_id (l'hero non e' un hero_video: lo sceglie il direttore creativo)")
    hq = (art.get("video") or {}).get("hero_query") if isinstance(art.get("video"), dict) else None
    if hq:
        hero = (spec.get("sections") or [{}])[0]
        if hero.get("type") == "hero_video":
            hero["video_query"] = str(hq).strip()[:60]
            applied.append("video.hero_query")
        else:
            ignored.append("video.hero_query (l'hero non e' un hero_video: il video resta una proposta nel file art.md)")
    d = workdir(slug)
    (d / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    (d / "art.json").write_text(json.dumps(art, ensure_ascii=False), encoding="utf-8")
    lines = [f"# Direzione artistica: {slug}", ""]
    if art.get("art_direction"):
        lines += ["## Frase di stile", str(art["art_direction"]), ""]
    if isinstance(art.get("bibbia"), dict):
        lines += ["## Bibbia visiva"] + [f"- **{k}**: {v}" for k, v in art["bibbia"].items()] + [""]
    if isinstance(art.get("shotlist"), list):
        lines += ["## Shot list"] + [f"{i}. {s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)}" for i, s in enumerate(art["shotlist"], 1)] + [""]
    if isinstance(art.get("video"), dict) and art["video"].get("prompt"):
        lines += ["## Prompt video (da lanciare a mano: Seedance, Kling, Wan)", str(art["video"]["prompt"]), ""]
    (d / "art.md").write_text("\n".join(lines), encoding="utf-8")
    return {"slug": slug, "applicati": applied, "ignorati": ignored, "file": ["art.json", "art.md"]}


PIXABAY_MAX_MB = 10


def _rendition(videos: dict) -> dict | None:
    """Stessa scelta della fabbrica: la versione piu' vicina al full HD tra 1280 e 2560 px sotto il peso massimo."""
    ok = [v for v in ((videos or {}).get(n) or {} for n in ("large", "medium", "small", "tiny")) if v.get("url") and 0 < (v.get("size") or 0) <= PIXABAY_MAX_MB * 1_000_000]
    best = [v for v in ok if 1280 <= (v.get("width") or 0) <= 2560]
    if best:
        return min(best, key=lambda v: abs(v["width"] - 1920))
    wide = [v for v in ok if (v.get("width") or 0) >= 960]
    return max(wide, key=lambda v: v["width"]) if wide else None


def stock_videos(query: str, transport=None, limit: int = 8) -> dict:
    """Ricerca di video stock gratuiti su Pixabay per scegliere l'hero. Regole dell'API: cache di 24 ore, 100 richieste/minuto, solo ricerche vere (mai scaricamenti di massa).
    Solo lettura: ritorna i dati per scegliere, non scarica nessun video."""
    import hashlib
    import httpx
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return {"errore": "manca PIXABAY_API_KEY: la ricerca di video stock non e' attiva"}
    q = re.sub(r"[^\w\s'-]", " ", query or "").strip()[:100]
    if len(q) < 3:
        return {"errore": "serve una ricerca di almeno 3 lettere, in inglese: 2-4 parole concrete (es. 'espresso pouring')"}
    cache = config.DATA_DIR / "pixabay_cache"
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / (hashlib.sha1(q.lower().encode()).hexdigest()[:16] + ".json")
    if f.exists() and time.time() - f.stat().st_mtime < 86400:
        d = json.loads(f.read_text(encoding="utf-8"))
    else:
        try:
            with httpx.Client(timeout=20, transport=transport) as c:
                r = c.get("https://pixabay.com/api/videos/", params={"key": key, "q": q, "video_type": "film", "min_width": 1280, "safesearch": "true", "per_page": 20, "order": "popular", "lang": "en"})
        except httpx.HTTPError as e:
            return {"errore": f"Pixabay non raggiungibile ({type(e).__name__})"}
        if r.status_code != 200:
            return {"errore": f"Pixabay ha risposto {r.status_code}: {r.text[:80]}"}
        d = r.json()
        f.write_text(json.dumps(d), encoding="utf-8")
    words = set(re.findall(r"[a-z]{3,}", q.lower()))
    items = []
    for h in d.get("hits") or []:
        v = _rendition(h.get("videos") or {})
        if not v:
            continue
        items.append({"id": h.get("id"), "durata_s": h.get("duration"), "tag": h.get("tags"), "risoluzione": f"{v['width']}x{v['height']}", "mb": round(v["size"] / 1e6, 1),
                      "orizzontale": v["width"] >= v["height"], "adatto_hero": 6 <= (h.get("duration") or 0) <= 40 and v["width"] >= v["height"],
                      "pertinenza": len(words & set(re.findall(r"[a-z]{3,}", (h.get("tags") or "").lower()))), "autore": h.get("user"), "pagina": h.get("pageURL"),
                      "miniatura": ((h.get("videos") or {}).get("medium") or {}).get("thumbnail", "")})
    items.sort(key=lambda x: (-x["adatto_hero"], -x["pertinenza"]))
    return {"ricerca": q, "totale": d.get("totalHits"), "video": items[:limit],
            "regole": "Licenza Pixabay: uso gratuito anche commerciale senza attribuzione, MA niente marchi o loghi riconoscibili, niente persone riconoscibili in modo ingannevole, e un video stock non va presentato come il locale del cliente. Preferisci natura, oggetti, luoghi, mani; evita volti e insegne."}


def check_funnel(spec: dict) -> list[str]:
    """Controlla lo scheletro dello spec. Ritorna l'elenco dei problemi (vuoto = ok)."""
    secs = spec.get("sections") or []
    roles = [str((s or {}).get("role", "")).strip().lower() for s in secs]
    errs = []
    if len(str((spec.get("brand") or {}).get("name", "")).strip()) < 2:
        errs.append("manca il nome della marca (brand.name): e' il logo scritto in alto e il titolo della pagina")
    missing = [r for r in SPINE if r not in roles]
    if missing:
        errs.append("mancano le sezioni obbligatorie: " + ", ".join(missing))
    spine_pos = [SPINE.index(r) for r in roles if r in SPINE]
    if spine_pos != sorted(spine_pos):
        errs.append("l'ordine dello scheletro non e' rispettato (attenzione, problema, soluzione, offerta, usp, prova, cta, faq)")
    for r in SPINE:
        if roles.count(r) > 1:
            errs.append(f"il ruolo '{r}' compare piu' volte")
    for s, r in zip(secs, roles):
        if r in ROLE_TYPES and s.get("type") not in ROLE_TYPES[r]:
            errs.append(f"il ruolo '{r}' richiede una sezione di tipo {' o '.join(ROLE_TYPES[r])}, non '{s.get('type')}'")
        if r and r not in SPINE and r != "extra":
            errs.append(f"ruolo sconosciuto: '{r}'")
        if not r:
            errs.append(f"sezione '{s.get('type')}' senza role")
        if r == "extra" and len(str(s.get("extra_why", "")).strip()) < 10:
            errs.append(f"la sezione extra '{s.get('type')}' non spiega a quale servizio o prodotto serve (extra_why)")
    if roles.count("extra") > MAX_EXTRA:
        errs.append(f"troppe sezioni extra (max {MAX_EXTRA})")
    if "extra" in roles:
        first_cta = roles.index("cta") if "cta" in roles else len(roles)
        if any(r == "extra" and (i == 0 or i > first_cta) for i, r in enumerate(roles)):
            errs.append("le sezioni extra stanno tra 'attenzione' e 'cta'")
    return list(dict.fromkeys(errs))
MAX_SPEC_BYTES = 120_000
BUILDS_PER_DAY = int(os.environ.get("HLAB_SITE_BUILDS_DAY", "6"))         # tetto: le immagini AI e il browser costano tempo e quote gratuite
BUILD_TIMEOUT_S = 900
PUBLISH_TIMEOUT_S = 300


def sites_dir() -> Path:
    return config.DATA_DIR / "sites"


def _py() -> str:
    return os.environ.get("HLAB_SITE_PY") or str(Path.home() / "venv-lab" / "bin" / "python3")


def _cli() -> str:
    return os.environ.get("HLAB_SITE_CLI") or str(Path.home() / "hermes_v10_cli.py")


def run_cli(args: list[str], timeout: int) -> dict:
    """Esegue la fabbrica e ritorna l'ultima riga JSON. Mai eccezioni: un errore e' {"errore": ...}."""
    try:
        p = subprocess.run([_py(), _cli(), *args], capture_output=True, text=True, timeout=timeout, cwd=str(Path.home()))
    except subprocess.TimeoutExpired:
        return {"errore": f"tempo scaduto dopo {timeout // 60} minuti"}
    except OSError as e:
        return {"errore": f"fabbrica non avviabile: {e}"}
    line = (p.stdout.strip().splitlines() or [""])[-1]
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"errore": f"risposta non valida della fabbrica (codice {p.returncode}): {(p.stderr or p.stdout)[-300:]}"}


def check_slug(slug: str) -> str:
    s = (slug or "").strip().lower()
    if not SLUG.match(s):
        raise ValueError("nome non ammesso: 3-60 caratteri, solo minuscole, numeri e trattini")
    return s


def workdir(slug: str) -> Path:
    return sites_dir() / check_slug(slug)


def guide(runner=None) -> dict:
    g = (runner or run_cli)(["guide"], 60)
    if "errore" not in g:
        g["nuovo_sito_o_modifica"] = EDIT_GUIDE
        g["scheletro_obbligatorio"] = FUNNEL_GUIDE
    return g


def list_published(runner=None) -> dict:
    """I siti gia' pubblicati dalla fabbrica (repo con hermes.json): quelli che si modificano."""
    return (runner or run_cli)(["list"], 120)


def origin(slug: str) -> dict | None:
    p = workdir(slug) / "origin.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def is_edit(slug: str) -> bool:
    return (workdir(slug) / "origin.json").exists()


def open_site(repo: str, runner=None) -> dict:
    """Scarica un sito pubblicato nella cartella di lavoro: da qui in poi si MODIFICA quel sito (stesso repo, stesso indirizzo)."""
    d = workdir(repo)
    if (d / "spec.json").exists() and not is_edit(repo):
        raise ValueError(f"esiste gia' un lavoro nuovo chiamato '{repo}' che non e' un sito pubblicato: usa un altro nome o chiedi a Lorenzo")
    res = (runner or run_cli)(["pull", repo, str(d)], 180)
    if "errore" in res:
        raise ValueError(res["errore"])
    return res


def save_spec(slug: str, spec, ritocco: bool = False, runner=None) -> dict:
    """Salva lo spec del direttore creativo. Accetta un oggetto o una stringa JSON. Controlla solo forma e dimensione: la validazione vera la fa la costruzione."""
    d = workdir(slug)
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except json.JSONDecodeError as e:
            raise ValueError(f"lo spec non e' JSON valido: {e}")
    if not isinstance(spec, dict) or not isinstance(spec.get("sections"), list) or not spec["sections"] or not isinstance(spec.get("brand"), dict):
        raise ValueError("lo spec deve essere un oggetto con almeno 'brand' e 'sections' (elenco non vuoto)")
    spec = {**spec}                                                      # una copia: salvando non si modifica il dizionario di chi chiama
    edit = is_edit(slug)
    if ritocco and not edit:
        raise ValueError("'ritocco' vale solo per un sito gia' pubblicato (aprilo prima con site_open): un sito nuovo deve avere lo scheletro")
    if edit:
        spec["repo_name"] = origin(slug)["repo"]                       # modifica: sempre lo stesso repo, mai un sito nuovo
    bad = fake_accents(spec)
    if bad:
        raise ValueError("usa le lettere accentate (è, é, à, ò, ù, ì) e non l'apostrofo (caffe', perche', e'): " + "; ".join(bad[:4]))
    errs = [] if ritocco else check_funnel(spec)
    if errs:
        raise ValueError("scheletro non valido: " + "; ".join(errs) + (" (se e' solo un ritocco di un sito gia' pubblicato usa ritocco: true)" if edit else ""))
    text = json.dumps(spec, ensure_ascii=False)
    if len(text.encode()) > MAX_SPEC_BYTES:
        raise ValueError("spec troppo grande")
    d.mkdir(parents=True, exist_ok=True)
    (d / "spec.json").write_text(text, encoding="utf-8")
    flag = d / "ritocco"
    if ritocco:
        flag.write_text("1")
    elif flag.exists():
        flag.unlink()
    res = {"slug": d.name, "sezioni": len(spec["sections"]), "marca": spec["brand"].get("name", ""), "ruoli": [s.get("role") for s in spec["sections"]],
           "modifica": edit, "repo": spec.get("repo_name")}
    if ritocco:
        res["avviso"] = "ritocco: lo scheletro non e' stato imposto, la struttura resta quella del sito pubblicato"
    return res


SEC_TEXT = {"eyebrow", "kicker", "title", "subtitle", "text", "lines", "bullets", "points", "price", "note", "number"}
ITEM_TEXT = {"title", "text", "caption", "tag", "label", "value", "prefix", "suffix", "q", "a", "quote", "author", "detail"}
APPEND_OK = {"faq", "proof"}                      # solo queste liste non hanno immagini: il copywriter puo' aggiungere voci


def merge_copy(slug: str, copy) -> dict:
    """Il copywriter riscrive SOLO i testi: {ruolo: {campi}} (ruoli dello scheletro, `extra-1`, `extra-2`, ... e `meta` con seo_description e nav_cta_label).
    Non puo' cambiare tipo, immagini, collegamenti, ordine, palette o font. Ritorna cosa e' stato applicato e cosa ignorato."""
    spec = load_spec(slug)
    if spec is None:
        raise ValueError("nessuno spec con questo nome: lo scrive prima il direttore creativo")
    if isinstance(copy, str):
        try:
            copy = json.loads(copy)
        except json.JSONDecodeError as e:
            raise ValueError(f"il copy non e' JSON valido: {e}")
    if not isinstance(copy, dict) or not copy:
        raise ValueError("il copy deve essere un oggetto {ruolo: {campi}}")
    secs, extras = spec["sections"], [x for x in spec["sections"] if x.get("role") == "extra"]
    applied, ignored = [], []
    for key, fields in copy.items():
        if key == "meta":
            if isinstance(fields, dict):
                if fields.get("seo_description"):
                    spec.setdefault("seo", {})["description"] = str(fields["seo_description"])[:160]
                    applied.append("meta.seo_description")
                if fields.get("nav_cta_label"):
                    spec.setdefault("nav_cta", {"href": "#contatti"})["label"] = str(fields["nav_cta_label"])[:30]
                    applied.append("meta.nav_cta_label")
            continue
        m = re.fullmatch(r"extra-(\d)", key)
        n = re.fullmatch(r"sezione-(\d+)", key)                           # siti vecchi senza ruoli: per posizione (1 = prima sezione)
        if n:
            sec = secs[int(n.group(1)) - 1] if 0 < int(n.group(1)) <= len(secs) else None
        else:
            sec = (extras[int(m.group(1)) - 1] if m and 0 < int(m.group(1)) <= len(extras) else None) if m else next((x for x in secs if x.get("role") == key), None)
        if sec is None or not isinstance(fields, dict):
            ignored.append(key)
            continue
        for k, v in fields.items():
            if k in SEC_TEXT and isinstance(v, (str, list)):
                sec[k] = v
                applied.append(f"{key}.{k}")
            elif k == "cta" and isinstance(v, dict) and v.get("label"):
                sec.setdefault("cta", {"href": "#contatti"})["label"] = str(v["label"])[:40]
                applied.append(f"{key}.cta")
            elif k == "items" and isinstance(v, list) and isinstance(sec.get("items"), list):
                olds = sec["items"]
                for i, new in enumerate(v):
                    if not isinstance(new, dict):
                        continue
                    if i < len(olds):
                        olds[i].update({a: b for a, b in new.items() if a in ITEM_TEXT})
                    elif sec.get("type") in APPEND_OK:
                        olds.append({a: b for a, b in new.items() if a in ITEM_TEXT})
                    else:
                        ignored.append(f"{key}.items[{i}] (voce nuova: servono immagini, le sceglie il direttore)")
                applied.append(f"{key}.items")
            else:
                ignored.append(f"{key}.{k}")
    bad = fake_accents(spec)
    if bad:
        raise ValueError("usa le lettere accentate (è, é, à, ò, ù, ì) e non l'apostrofo (caffe', perche', e'): " + "; ".join(bad[:4]))
    (workdir(slug) / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return {"slug": slug, "applicati": applied, "ignorati": ignored}


def load_spec(slug: str) -> dict | None:
    p = workdir(slug) / "spec.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Fallback di sicurezza: se l'agente ha salvato con un altro slug o sul repo originale, recupera lo spec recente
    try:
        candidates = []
        for sf in sites_dir().glob("*/spec.json"):
            if sf != p and sf.is_file():
                candidates.append((sf.stat().st_mtime, sf))
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            newest_mtime, newest_file = candidates[0]
            if time.time() - newest_mtime < 900:
                workdir(slug).mkdir(parents=True, exist_ok=True)
                shutil.copy2(newest_file, p)
                return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _counter() -> Path:
    return sites_dir() / "builds.json"


def builds_today(now: float | None = None) -> int:
    try:
        d = json.loads(_counter().read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    day = time.strftime("%Y-%m-%d", time.gmtime(now or time.time()))
    return d.get("n", 0) if d.get("day") == day else 0


def _count_build(now: float | None = None) -> None:
    sites_dir().mkdir(parents=True, exist_ok=True)
    day = time.strftime("%Y-%m-%d", time.gmtime(now or time.time()))
    _counter().write_text(json.dumps({"day": day, "n": builds_today(now) + 1}))


def build(slug: str, runner=None) -> dict:
    d = workdir(slug)
    if not (d / "spec.json").exists():
        return {"errore": "manca lo spec: lo scrive prima il direttore creativo (site_spec_save)"}
    if builds_today() >= BUILDS_PER_DAY:
        return {"errore": f"tetto di {BUILDS_PER_DAY} costruzioni al giorno raggiunto: riprova domani o chiedi a Lorenzo di alzarlo (HLAB_SITE_BUILDS_DAY)"}
    _count_build()
    before = gemini_usd()
    rep = (runner or run_cli)(["build", str(d / "spec.json"), str(d)], BUILD_TIMEOUT_S)
    if "errore" not in rep:
        rep["ritocco"] = (d / "ritocco").exists()
        rep["verdetto"] = verdict(rep)
        record_costs(rep, slug, max(0.0, gemini_usd() - before))
    return rep


def gemini_usd() -> float:
    """Spesa Gemini del mese letta dal contatore del bot (`~/hermes_gemini_usage.json`): la differenza prima/dopo una costruzione e' il costo delle sue immagini."""
    try:
        d = json.loads((Path.home() / "hermes_gemini_usage.json").read_text())
        return float(d.get("usd", 0)) if d.get("month") == time.strftime("%Y-%m") else 0.0
    except (OSError, ValueError):
        return 0.0


def record_costs(rep: dict, slug: str, gemini_delta: float) -> None:
    """Scrive nel registro dei costi per sito (`data/ledger.jsonl`, lo stesso che legge Vita) cosa e' costata questa costruzione. Mai blocca la fabbrica.
    Le voci a zero restano: dicono «gratis» in modo documentato (quanti modelli e immagini, da quali fornitori)."""
    try:
        from . import ledger
        site = str(rep.get("marca") or slug)
        im = rep.get("immagini") or {}
        provs = ", ".join(im.get("fornitori") or []) or "nessuno"
        p = config.DATA_DIR / "ledger.jsonl"
        ledger.add(site, "immagini", round(gemini_delta, 4), f"{im.get('totale', 0)} immagini ({provs})", p=p)
        ledger.add(site, "modelli", 0.0, "agenti su modelli gratuiti (nessun addebito)", p=p)
    except Exception:                                                                       # noqa: BLE001 - il registro e' un contorno: non deve fermare un sito
        pass


def verdict(rep: dict) -> str:
    """Semaforo per il Deployer e per Lorenzo: pronto, da rivedere, bloccato."""
    missing = [] if rep.get("ritocco") else [r for r in SPINE if "ruoli" in rep and r not in rep["ruoli"]]
    if missing:
        return "bloccato: nella pagina costruita mancano sezioni dello scheletro (" + ", ".join(missing) + "): controllare i testi obbligatori di quelle sezioni"
    if rep.get("errori_js") or rep.get("immagini_rotte"):
        return "bloccato: errori nel browser o immagini rotte, correggere lo spec e ricostruire"
    if rep.get("richieste_fallite") or rep.get("immagini", {}).get("segnaposto"):
        return "da rivedere: richieste fallite o immagini segnaposto (mancano le chiavi delle immagini?)"
    return "pronto: nessun errore nel browser"


def report(slug: str) -> dict | None:
    p = workdir(slug) / "report.json"
    if not p.exists():
        return None
    rep = json.loads(p.read_text(encoding="utf-8"))
    rep["ritocco"] = (workdir(slug) / "ritocco").exists()
    rep["verdetto"] = verdict(rep)
    return rep


def listing() -> list[dict]:
    out = []
    for p in sorted(sites_dir().glob("*/spec.json")) if sites_dir().exists() else []:
        rep = report(p.parent.name)
        out.append({"slug": p.parent.name, "modifica_di_sito_pubblicato": is_edit(p.parent.name), "costruito": rep is not None,
                    "verdetto": rep["verdetto"] if rep else "non ancora costruito"})
    return out


def publish(slug: str, runner=None) -> dict:
    """Pubblica su GitHub Pages. La chiama SOLO l'approvazione di Lorenzo (powers.approve, kind site_publish)."""
    rep = report(slug)
    if rep is None:
        raise ValueError("sito non costruito: niente da pubblicare")
    if rep["verdetto"].startswith("bloccato"):
        raise ValueError("il controllo nel browser ha trovato errori: ricostruire prima di pubblicare")
    res = (runner or run_cli)(["publish", str(workdir(slug))], PUBLISH_TIMEOUT_S)
    if "errore" in res:
        raise ValueError(res["errore"])
    register_site(rep.get("marca") or slug, res.get("url"))                    # il monitor di uptime lo controlla da solo
    return res


def published_registry() -> Path:
    return config.DATA_DIR / "published_sites.json"


def register_site(name: str, url: str | None) -> bool:
    """Annota un sito pubblicato: il monitor di uptime (hlab.uptime.targets) lo controlla senza interventi a mano."""
    if not url or not str(url).startswith(("http://", "https://")):
        return False
    p = published_registry()
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except ValueError:
        d = {}
    d[str(name)[:80]] = str(url)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def write_job(slug: str, job: dict) -> Path:
    """Il lavoro consegnato alla fabbrica (da Telegram o dalla GUI): richiesta, modo (nuovo|modifica), eventuale repo, materiale trovato."""
    d = workdir(slug)
    d.mkdir(parents=True, exist_ok=True)
    (d / "job.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    if job.get("context"):
        (d / "context.txt").write_text(str(job["context"])[:60_000], encoding="utf-8")
    return d / "job.json"


def read_job(slug: str) -> dict | None:
    p = workdir(slug) / "job.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def read_context(slug: str) -> str | None:
    p = workdir(slug) / "context.txt"
    return p.read_text(encoding="utf-8") if p.exists() else None


def remove(slug: str) -> None:
    shutil.rmtree(workdir(slug), ignore_errors=True)
