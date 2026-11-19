"""Fabbrica self-healing: QA in browser -> patch automatica -> nuova verifica -> si mostra all'umano SOLO la versione gia' corretta.

Anello chiuso: `scan` apre il sito con Playwright (desktop e mobile), rileva errori JavaScript, immagini rotte, overflow orizzontale,
elementi fuori schermo e hero vuota; `heal` chiede a un PATCHER le correzioni e le applica a una COPIA di lavoro. Una patch si tiene solo se
il punteggio dei problemi scende, altrimenti si ripristina: non puo' peggiorare il sito. Dopo al massimo N tentativi restituisce la
versione migliore e la lista dei problemi residui (da far vedere a una persona).

Il patcher LLM lavora in modo confinato: risponde con modifiche cerca/sostituisci su file di testo del sito (html, css, js, j2, json),
niente comandi, niente percorsi fuori dalla cartella, niente file binari.
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

ALLOWED_EXT = {".html", ".css", ".js", ".j2", ".json", ".svg"}
MAX_FILE = 500_000
VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}
WEIGHT = {"js_error": 5, "broken_image": 3, "overflow_x": 4, "offscreen": 1, "empty_hero": 6, "load_failed": 8}

PROBE_JS = """() => {
  const vw = window.innerWidth, out = {};
  out.overflow = Math.max(0, document.documentElement.scrollWidth - vw);
  out.offscreen = [...document.querySelectorAll('body *')].filter(e => { const r = e.getBoundingClientRect();
      return r.width > 0 && r.right > vw + 4 && getComputedStyle(e).position !== 'fixed'; }).slice(0, 5)
      .map(e => ({tag: e.tagName.toLowerCase(), cls: (e.className && e.className.toString().slice(0, 60)) || '', html: e.outerHTML.slice(0, 220)}));
  out.broken = [...document.images].filter(i => i.complete && i.naturalWidth === 0).map(i => i.getAttribute('src')).slice(0, 8);
  const first = document.querySelector('main section, body > section, section') || document.body.firstElementChild;
  out.hero = first ? {h: Math.round(first.getBoundingClientRect().height), text: (first.innerText || '').trim().length} : {h: 0, text: 0};
  return out;
}"""


@dataclass
class Issue:
    kind: str
    viewport: str
    detail: str

    @property
    def weight(self) -> int:
        return WEIGHT.get(self.kind, 1)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "viewport": self.viewport, "detail": self.detail}


async def scan(site_dir: Path, entry: str = "index.html", shots_dir: Path | None = None) -> dict:
    from playwright.async_api import async_playwright
    issues: list[Issue] = []
    shots: dict[str, str] = {}
    snippets: list[dict] = []
    url = (Path(site_dir) / entry).resolve().as_uri()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            for name, (w, h) in VIEWPORTS.items():
                page = await browser.new_page(viewport={"width": w, "height": h})
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)[:200]))
                page.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" and "Failed to load resource" not in m.text else None)
                try:
                    await page.goto(url, wait_until="load", timeout=30_000)
                    await page.wait_for_timeout(700)
                except Exception as e:  # noqa: BLE001
                    issues.append(Issue("load_failed", name, f"{type(e).__name__}: {str(e)[:120]}"))
                    await page.close()
                    continue
                for e in errors[:5]:
                    issues.append(Issue("js_error", name, e))
                r = await page.evaluate(PROBE_JS)
                if r["overflow"] > 2:
                    issues.append(Issue("overflow_x", name, f"la pagina e' {r['overflow']}px piu' larga dello schermo"))
                for o in r["offscreen"]:
                    issues.append(Issue("offscreen", name, f"<{o['tag']} class='{o['cls']}'> esce dallo schermo"))
                    snippets.append({"viewport": name, **o})
                for src in r["broken"]:
                    issues.append(Issue("broken_image", name, f"immagine non caricata: {src}"))
                if r["hero"]["h"] < 150 or r["hero"]["text"] == 0:
                    issues.append(Issue("empty_hero", name, f"la prima sezione e' alta {r['hero']['h']}px con {r['hero']['text']} caratteri di testo"))
                if shots_dir:
                    shots_dir.mkdir(parents=True, exist_ok=True)
                    path = shots_dir / f"{name}.png"
                    await page.screenshot(path=str(path))
                    shots[name] = str(path)
                await page.close()
        finally:
            await browser.close()
    return {"issues": [i.to_dict() for i in issues], "score": sum(i.weight for i in issues), "shots": shots, "dom": snippets}


def _safe(work: Path, rel: str) -> Path | None:
    p = (work / rel).resolve()
    if work.resolve() not in p.parents or p.suffix.lower() not in ALLOWED_EXT:
        return None
    return p


def apply_edits(work: Path, edits: list[dict]) -> dict[str, str]:
    """Applica modifiche cerca/sostituisci confinate. Ritorna {file: contenuto precedente} per poter ripristinare."""
    before: dict[str, str] = {}
    for e in edits[:8]:
        p = _safe(work, str(e.get("file", "")))
        if not p or not p.is_file() or p.stat().st_size > MAX_FILE:
            continue
        txt = p.read_text(encoding="utf-8")
        search, rep = str(e.get("search", "")), str(e.get("replace", ""))
        if not search or txt.count(search) != 1 or len(rep) > 20_000:      # solo corrispondenze univoche: niente sostituzioni a tappeto
            continue
        before.setdefault(str(p), txt)
        p.write_text(txt.replace(search, rep, 1), encoding="utf-8")
    return before


def _restore(before: dict[str, str]) -> None:
    for path, txt in before.items():
        Path(path).write_text(txt, encoding="utf-8")


def _diff(original: Path, work: Path) -> str:
    out = []
    for f in sorted(original.rglob("*")):
        if f.is_file() and f.suffix.lower() in ALLOWED_EXT:
            w = work / f.relative_to(original)
            if w.exists() and w.read_text(encoding="utf-8") != f.read_text(encoding="utf-8"):
                out += list(difflib.unified_diff(f.read_text(encoding="utf-8").splitlines(), w.read_text(encoding="utf-8").splitlines(),
                                                 f"originale/{f.name}", f"corretto/{f.name}", lineterm="", n=1))
    return "\n".join(out)


async def heal(site_dir: Path, patcher, max_attempts: int = 2, entry: str = "index.html") -> dict:
    """patcher(report, work_dir) -> lista di modifiche [{file, search, replace}]. Mai peggiora: una patch che non abbassa il punteggio si annulla."""
    site_dir = Path(site_dir)
    work = Path(tempfile.mkdtemp(prefix="heal-")) / "site"
    shutil.copytree(site_dir, work)
    first = cur = await scan(work, entry)
    log, kept = [], 0
    for attempt in range(1, max_attempts + 1):
        if not cur["issues"]:
            break
        edits = await patcher(cur, work)
        if not edits:
            log.append({"tentativo": attempt, "esito": "nessuna patch proposta"})
            break
        before = apply_edits(work, edits)
        if not before:
            log.append({"tentativo": attempt, "esito": "nessuna modifica applicabile (cerca non univoco o file non ammesso)"})
            continue
        after = await scan(work, entry)
        if after["score"] < cur["score"]:
            log.append({"tentativo": attempt, "esito": "tenuta", "punteggio": [cur["score"], after["score"]], "file": sorted(Path(p).name for p in before)})
            cur, kept = after, kept + 1
        else:
            _restore(before)
            log.append({"tentativo": attempt, "esito": "annullata: non migliora", "punteggio": [cur["score"], after["score"]]})
    return {"healed": not cur["issues"], "migliorato": kept > 0, "prima": {"punteggio": first["score"], "problemi": first["issues"]},
            "dopo": {"punteggio": cur["score"], "problemi": cur["issues"]}, "tentativi": log, "diff": _diff(site_dir, work), "work_dir": str(work)}


PATCH_SYSTEM = ("Sei il Builder di una fabbrica di siti. Ricevi i problemi rilevati dal browser e il codice. Rispondi SOLO con JSON: "
                '{"edits":[{"file":"percorso relativo","search":"testo ESATTO e UNICO da cambiare","replace":"nuovo testo"}]}. '
                "Al massimo 5 modifiche, minime, che correggano i problemi senza cambiare contenuti o design. Nessun altro testo.")


def _context(report: dict, work: Path, budget: int = 9000) -> str:
    parts, used = [], 0
    hints = " ".join(i["detail"] for i in report["issues"]) + " " + " ".join(d["html"] for d in report["dom"])
    for f in sorted(work.rglob("*")):
        if f.is_file() and f.suffix.lower() in ALLOWED_EXT and f.stat().st_size < MAX_FILE:
            txt = f.read_text(encoding="utf-8")
            if f.name == "index.html" or any(tok in txt for tok in re.findall(r"[\w./-]{6,}", hints)[:20]):
                chunk = txt[: max(1500, budget - used)]
                parts.append(f"--- {f.relative_to(work)} ---\n{chunk}")
                used += len(chunk)
                if used >= budget:
                    break
    return "\n".join(parts)


def llm_patcher(complete=None):
    """Patcher che usa un modello (router/Anthropic). `complete(prompt, system)` e' iniettabile per i test."""
    async def patcher(report: dict, work: Path) -> list[dict]:
        from .llm import complete as default_complete
        ask = complete or default_complete
        prompt = ("PROBLEMI:\n" + json.dumps(report["issues"][:10], ensure_ascii=False, indent=1) + "\n\nELEMENTI COINVOLTI:\n"
                  + json.dumps(report["dom"][:5], ensure_ascii=False, indent=1) + "\n\nFILE:\n" + _context(report, work))
        raw = await ask(prompt, PATCH_SYSTEM)
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            return list(json.loads(m.group(0)).get("edits") or []) if m else []
        except (ValueError, AttributeError):
            return []
    return patcher
