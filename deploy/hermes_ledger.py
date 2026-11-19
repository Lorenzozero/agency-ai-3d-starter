"""Registro dei costi di produzione per sito, lato Hermes (produzione). Append su ~/hermes_lab/data/ledger.jsonl, che hlab legge
(`hlab.push_vita`) e invia a Vita. Solo libreria standard; ogni errore è inghiottito: il registro non deve MAI bloccare un sito.

Cosa registra a ogni pubblicazione, per il sito (nome del repository):
- `llm`: token usati (stima a prezzo di listino: oggi i modelli sono sui piani gratuiti, quindi il costo REALE è 0);
- `immagini:<fornitore>`: quante immagini e da chi (stima solo per i fornitori a pagamento, es. Gemini);
- `deploy`: una pubblicazione su GitHub Pages (0 $).
I prezzi stimati si cambiano in `~/hermes_lab/data/ledger_rates.json` (stessa forma di DEFAULT_RATES)."""
import json
import os
import time
from pathlib import Path

PATH = Path(os.environ.get("HLAB_LEDGER") or (Path.home() / "hermes_lab" / "data" / "ledger.jsonl"))
DEFAULT_RATES = {"llm_usd_per_mtok": 0.35, "image_usd": {"gemini": 0.04}}


def _rates() -> dict:
    r = json.loads(json.dumps(DEFAULT_RATES))
    try:
        over = json.loads(PATH.with_name("ledger_rates.json").read_text("utf-8"))
        r["llm_usd_per_mtok"] = float(over.get("llm_usd_per_mtok", r["llm_usd_per_mtok"]))
        r["image_usd"].update({str(k): float(v) for k, v in (over.get("image_usd") or {}).items()})
    except (OSError, ValueError, TypeError):
        pass
    return r


def record(site, kind, usd=0.0, est=False, units=None, note="") -> bool:
    try:
        site, kind = str(site or "").strip()[:60], str(kind or "").strip()[:30]
        if not site or not kind:
            return False
        row = {"ts": int(time.time()), "site": site, "kind": kind, "usd": round(float(usd), 4), "est": bool(est), "note": str(note or "")[:120]}
        if units:
            row["units"] = {str(k)[:20]: float(v) for k, v in list(units.items())[:6] if isinstance(v, (int, float))}
        PATH.parent.mkdir(parents=True, exist_ok=True)
        with PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception:   # noqa: BLE001 - mai bloccare un sito per un registro
        return False


def record_build(spec: dict, tok: int = 0, title: str = "") -> None:
    """Chiamata a ogni pubblicazione riuscita. `spec` è lo spec del sito (repo_name, images con il loro fornitore)."""
    try:
        site = (spec.get("repo_name") or (spec.get("brand") or {}).get("name") or "").strip()
        if not site:
            return
        rates = _rates()
        if tok:
            record(site, "llm", tok / 1e6 * rates["llm_usd_per_mtok"], est=True, units={"tokens": tok}, note=title)
        by: dict[str, int] = {}
        for im in spec.get("images") or []:
            p = str(im.get("provider") or "").strip()
            if p and p not in ("client", "reuse"):      # immagini del cliente o già pagate in precedenza: nessun costo nuovo
                by[p] = by.get(p, 0) + 1
        for p, n in by.items():
            record(site, "immagini:" + p[:18], n * rates["image_usd"].get(p, 0.0), est=True, units={"images": n}, note=title)
        record(site, "deploy", 0.0, units={"deploys": 1}, note=title)
    except Exception:   # noqa: BLE001
        pass
