"""Registro dei costi di produzione per sito (`data/ledger.jsonl`): una riga per voce, sempre in append.

Serve a rispondere «quanto mi costa produrre il sito X?» e a calcolare il margine (incassato meno costi). Chi spende lo registra:
la fabbrica siti (crediti Tripo, anteprime Netlify, token dei modelli a pagamento…) oppure tu a mano.
Ogni voce è REALE (importo pagato) oppure STIMATA (`est`: prezzo di listino di ciò che oggi è gratuito o non fatturato, es. i token
di un modello su piano free): nelle risposte le due cose restano separate. `units` conta ciò che è stato usato (token, immagini, pubblicazioni).
Uso:  python -m hlab.ledger add --site "Officina Rapida" --kind tripo --usd 0.35 [--note "modello 3D"]
      python -m hlab.ledger show
Non contiene mai chiavi né dati dei clienti: solo sito, tipo di spesa, importo in dollari e una nota breve."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def path() -> Path:
    return Path(os.environ.get("HLAB_DATA") or (Path(os.environ.get("HLAB_HOME", ".")) / "data")) / "ledger.jsonl"


def add(site: str, kind: str, usd: float, note: str = "", p: Path | None = None, now: float | None = None, est: bool = False, units: dict | None = None) -> dict:
    site, kind = site.strip()[:60], kind.strip()[:30]
    if not site or not kind:
        raise ValueError("servono sito e tipo di spesa")
    if not (0 <= usd < 10_000):
        raise ValueError("importo fuori dai limiti")
    row = {"ts": int(now or time.time()), "site": site, "kind": kind, "usd": round(float(usd), 4), "est": bool(est), "note": note.strip()[:120]}
    if units:
        row["units"] = {str(k)[:20]: float(v) for k, v in list(units.items())[:6] if isinstance(v, (int, float))}
    p = p or path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hlab.ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--site", required=True)
    a.add_argument("--kind", required=True)
    a.add_argument("--usd", type=float, required=True)
    a.add_argument("--note", default="")
    sub.add_parser("show")
    args = ap.parse_args(argv)
    if args.cmd == "add":
        try:
            print(json.dumps(add(args.site, args.kind, args.usd, args.note), ensure_ascii=False))
        except ValueError as e:
            print(f"errore: {e}", file=sys.stderr)
            return 2
        return 0
    from .push_vita import read_ledger
    print(json.dumps(read_ledger(path()), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
