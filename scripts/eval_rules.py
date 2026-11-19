"""Valuta il recupero delle regole condivise (modello vero, store condiviso popolato): python scripts/eval_rules.py
Due serie: TUNING (usata mentre si scrivono tag e sinonimi) e HELD-OUT (scritta prima di ritoccare, mai usata per tarare).
Conta il risultato giusto se e' tra quelli accettati."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hlab import config  # noqa: E402
from hlab.memory import MemoryStore  # noqa: E402

TUNING = [("il cliente americano deve pagare le tasse italiane?", {"/regole/fiscale-clienti-usa.md"}),
          ("quanto costa una landing page", {"/regole/listino-prezzi.md"}),
          ("cosa succede se il cliente non risponde dopo la consegna?", {"/regole/clausola-accettazione-tacita-10-giorni.md"}),
          ("quanto vale la marca da bollo?", {"/regole/fiscale-marca-da-bollo.md"}),
          ("posso rimborsare l'anticipo?", {"/regole/clausola-acconto-non-rimborsabile.md"}),
          ("chi e' proprietario del codice prima del saldo?", {"/regole/clausola-proprieta-del-codice.md"}),
          ("quanto abbiamo fatto pagare a Ristorante Aurora?", {"/regole/prezzi-lavori-chiusi.md"}),
          ("in che lingua scrivo il contratto per un cliente in dollari?", {"/regole/lingua-documenti-clienti-usa.md"}),
          ("cosa devo chiedere al commercialista?", {"/regole/fiscale-da-confermare-commercialista.md"}),
          ("quando pagano il saldo?", {"/regole/acconto-saldo-tranche.md"})]
HELD_OUT = [("come funziona il pagamento in due rate?", {"/regole/acconto-saldo-tranche.md"}),
            ("devo mettere l'IVA in fattura?", {"/regole/fiscale-regime-forfettario.md", "/regole/fiscale-clienti-usa.md"}),
            ("quanto costa far fare una app per telefono?", {"/regole/listino-prezzi.md"}),
            ("se il cliente contesta il pagamento fatto con la carta?", {"/regole/clausola-contestazioni-e-foro.md"}),
            ("la firma elettronica ha valore legale?", {"/regole/firma-elettronica-contratto.md"}),
            ("a chi appartiene il sito finche' non ha pagato tutto?", {"/regole/clausola-proprieta-del-codice.md"}),
            ("cosa propongo di includere in un sito vetrina?", {"/regole/listino-voci-suggerite.md"}),
            ("dollari o euro per un cliente di Las Vegas?", {"/regole/valute-eur-usd.md", "/regole/lingua-documenti-clienti-usa.md"}),
            ("dopo quanto tempo ricevo la fattura?", {"/regole/fattura-e-link-pagamento.md"}),
            ("cosa copre l'anticipo che il cliente versa?", {"/regole/clausola-acconto-non-rimborsabile.md", "/regole/acconto-saldo-tranche.md"})]

m = MemoryStore(config.SHARED_STORE, readonly=True)
for title, qs in (("TUNING", TUNING), ("HELD-OUT", HELD_OUT)):
    t1 = t3 = 0
    for q, want in qs:
        hits = [h["path"] for h in m.search(q, k=3)]
        a, b = bool(hits) and hits[0] in want, any(h in want for h in hits)
        t1, t3 = t1 + a, t3 + b
        if "-v" in sys.argv:
            print(("OK  " if a else "top3" if b else "MISS"), q, "->", hits[0] if hits else None)
    print(f"{title:9s} top-1 {t1}/{len(qs)}  top-3 {t3}/{len(qs)}")
print(f"({m.stats()['entries']} voci)")
