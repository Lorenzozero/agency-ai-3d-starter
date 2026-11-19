"""Riempie la memoria condivisa con listino e regole commerciali. Rieseguibile: le voci uguali restano invariate.
Fonti: packages/shared/src/{catalog,billing}.ts del repo agency-ai-website e la nota audit-legale-tecnico-piattaforma (2026-10-29).
Una voce = un argomento: la ricerca e' piu' precisa e nei nomi dei file ci sono le parole chiave.
Uso: python scripts/seed_shared.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hlab import commercial as co  # noqa: E402
from hlab import config  # noqa: E402
from hlab.memory import MemoryStore  # noqa: E402


def eur(c):
    return co.money(c, "EUR")


ENTRIES = {
    "/regole/listino-prezzi.md": (
        "Listino di partenza, prezzi minimi in euro: "
        + "; ".join(f"{co.TYPE_LABELS[t]} {eur(co.BASE_PRICE_CENTS[t])}" + (" al mese" if t == "manutenzione" else " una tantum")
                    for t in co.PROJECT_TYPES if co.BASE_PRICE_CENTS[t])
        + ". Il tipo 'altro' non ha prezzo di partenza: lo decide Lorenzo. Fonte: catalog.ts, PROJECT_TYPE_BASE_PRICE_CENTS.",
        ["listino", "prezzi"], 0.9),
    "/regole/listino-voci-suggerite.md": (
        "Voci suggerite per tipo di preventivo: " + " | ".join(f"{t}: {', '.join(v)}" for t, v in co.SUGGESTED_PARTS.items()) + ". Fonte: catalog.ts.",
        ["listino", "voci"], 0.6),
    "/regole/prezzi-lavori-chiusi.md": (
        "Prezzi reali dei lavori chiusi come riferimento (lead convertiti): Ristorante Aurora 2.050 euro, Bottega Artigiana 1.900 euro, "
        "Studio Nova 1.650 euro. Sono sopra il listino di partenza perche' includono 3D e contenuti su misura. Fonte: note del vault, ottobre 2026.",
        ["prezzi", "storico"], 0.6),
    "/regole/acconto-saldo-tranche.md": (
        "Pagamento in due tranche sul totale una tantum: acconto 50% alla firma del contratto e saldo 50% alla consegna. "
        "Il canone mensile della manutenzione non rientra nelle tranche. Fonte: billing.ts, DEPOSIT_RATE 0,5.",
        ["acconto", "saldo", "tranche"], 0.9),
    "/regole/fattura-e-link-pagamento.md": (
        f"Dopo la firma il cliente riceve fattura e link di pagamento dell'acconto entro {co.INVOICE_WITHIN_DAYS} giorni. Pagamenti con carta o Apple Pay via Stripe; "
        "lo stato di un incasso lo decide solo il server tramite webhook firmato, mai il browser. Fonte: billing.ts e audit 2026-10-29.",
        ["fattura", "stripe", "pagamenti"], 0.7),
    "/regole/fiscale-regime-forfettario.md": (
        f"Regime forfettario: nessuna IVA in fattura. Dicitura in portale (italiano): \"{co.FLAT_RATE_NOTICE['it']}\" Fonte: billing.ts.",
        ["fiscale", "iva", "forfettario"], 0.8),
    "/regole/fiscale-marca-da-bollo.md": (
        "Marca da bollo di 2 euro sulle fatture oltre 77,47 euro, addebitata al cliente su ogni tranche che la richiede (acconto e saldo, ciascuno). "
        "Il portale la calcola come 200 centesimi. Fonte: billing.ts, STAMP_DUTY_CENTS e STAMP_DUTY_THRESHOLD_CENTS.",
        ["fiscale", "bollo"], 0.8),
    "/regole/fiscale-clienti-usa.md": (
        "Clienti USA (B2B): fatturazione a 0% VAT per l'art. 7-ter DPR 633/72 (servizi a committente extra-UE, fuori campo IVA), documento chiamato "
        "'Commercial Invoice & Tax Receipt'; modulo IRS W-8BEN-E per evitare la ritenuta del 30% (0% US withholding). Fonte: audit 2026-10-29.",
        ["fiscale", "usa", "iva", "w8ben"], 0.8),
    "/regole/fiscale-da-confermare-commercialista.md": (
        "DA CONFERMARE COL COMMERCIALISTA, da riferire a Lorenzo e non risolvere: (1) il portale usa la dicitura del regime forfettario (L. 190/2014) per tutti "
        "mentre l'audit cita l'art. 7-ter per i clienti USA: quale dicitura va stampata; (2) il portale applica il bollo come 200 centesimi anche in USD.",
        ["fiscale", "da-confermare"], 0.9),
    "/regole/valute-eur-usd.md": (
        "Valute ammesse: EUR e USD, mai convertite tra loro e mai legate alla lingua dell'interfaccia. Il tipo di progetto condivide l'elenco dei servizi "
        "dei lead: landing, webapp, mobile, manutenzione, altro. Fonte: billing.ts.",
        ["valute", "eur", "usd"], 0.7),
    "/regole/lingua-documenti-clienti-usa.md": (
        "Cliente in euro: documenti in italiano. Cliente in dollari (USA): documenti e contratto in inglese giuridico statunitense (US Legal English). "
        "Fonte: documentLanguage in billing.ts e audit 2026-10-29.",
        ["lingua", "usa", "contratto"], 0.7),
    "/regole/clausola-acconto-non-rimborsabile.md": (
        "Contratto B2B, Section 2: l'acconto del 50% non e' rimborsabile una volta iniziato lo sviluppo (copre provisioning cloud, diagnostica tecnica e "
        "impostazione dell'architettura). Fonte: audit 2026-10-29.", ["clausola", "acconto", "rimborso"], 0.8),
    "/regole/clausola-proprieta-del-codice.md": (
        "Contratto B2B, Section 3: codice, asset 3D, layout e consegne restano proprieta' dello studio fino al saldo completo del prezzo; "
        "col saldo si cedono i diritti operativi. Fonte: audit 2026-10-29.", ["clausola", "proprieta", "ip"], 0.8),
    "/regole/clausola-accettazione-tacita-10-giorni.md": (
        "Contratto B2B, Section 4: dopo la consegna dello staging il cliente ha 10 giorni di calendario per segnalare per iscritto difformita' sostanziali; "
        "se non risponde, i lavori si considerano accettati (silenzio-assenso) e il saldo diventa subito dovuto. Fonte: audit 2026-10-29.",
        ["clausola", "accettazione", "silenzio-assenso", "consegna"], 0.8),
    "/regole/clausola-contestazioni-e-foro.md": (
        "Contratto B2B, Section 8: rinuncia a contestare l'addebito (chargeback) senza prima 30 giorni di trattativa scritta in buona fede; foro competente di Padova. "
        "Fonte: audit 2026-10-29.", ["clausola", "chargeback", "foro"], 0.8),
    "/regole/firma-elettronica-contratto.md": (
        "La firma del contratto e' elettronica (E-SIGN Act, UETA, eIDAS) con IP, user agent, orario UTC e hash SHA-256 registrati. Il testo del contratto lo genera "
        "il portale: il Commerciale non lo riscrive. Fonte: audit 2026-10-29.", ["contratto", "firma"], 0.6),
}

# prima versione, a voci piu' grandi e miste: cancellazione logica
OBSOLETE = ["/regole/listino.md", "/regole/acconto-saldo.md", "/regole/fiscale.md", "/regole/valute-lingue.md", "/regole/clausole-contratto.md"]

if __name__ == "__main__":
    s = MemoryStore(config.SHARED_STORE)
    for path in OBSOLETE:
        if s.get(path):
            s.forget(path, "seed")
            print(f"forget  {path}")
    for path, (text, tags, imp) in ENTRIES.items():
        r = s.write(path, text, tags, imp, author="seed")
        print(f"{r['op']:7s} {path}  ({len(text)} caratteri)")
    print(s.stats())
