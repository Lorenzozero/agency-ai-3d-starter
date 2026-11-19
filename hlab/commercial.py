"""Logica commerciale DETERMINISTICA: listino, tranche, bolli. Gli importi non li calcola mai il modello.

Specchio di packages/shared/src/billing.ts e catalog.ts del repo agency-ai-website (stessi numeri e arrotondamenti):
se cambiano la', cambiano qui. tests/test_commercial.py confronta le costanti col file TypeScript quando il repo e' disponibile.
"""
from __future__ import annotations

import math

CURRENCIES = ("EUR", "USD")
PROJECT_TYPES = ("landing", "webapp", "mobile", "manutenzione", "altro")
TYPE_LABELS = {"landing": "Sito web / landing page", "webapp": "Web app", "mobile": "App mobile",
               "manutenzione": "Manutenzione", "altro": "Altro"}
BASE_PRICE_CENTS = {"landing": 40_000, "webapp": 80_000, "mobile": 120_000, "manutenzione": 2_000, "altro": 0}  # manutenzione: mensile
SUGGESTED_PARTS = {
    "landing": ["Design e sviluppo pagine", "Testi e SEO di base", "Modulo contatti", "Dominio e hosting (anno)", "Pagina aggiuntiva"],
    "webapp": ["Analisi e progettazione", "Area riservata e login", "Pannello di amministrazione", "Integrazioni e API", "Database e backup"],
    "mobile": ["Design delle schermate", "App iOS e Android", "Notifiche push", "Backend e database", "Pubblicazione sugli store"],
    "manutenzione": ["Aggiornamenti e sicurezza", "Backup", "Piccole modifiche", "Supporto prioritario"],
    "altro": ["Consulenza", "Formazione", "Sviluppo su misura"],
}
DEPOSIT_RATE = 0.5
STAMP_DUTY_CENTS = 200
STAMP_DUTY_THRESHOLD_CENTS = 7_747
INVOICE_WITHIN_DAYS = 10
FLAT_RATE_NOTICE = {
    "it": "Operazione senza applicazione dell'IVA, effettuata ai sensi dell'art. 1, commi da 54 a 89, della Legge n. 190/2014 (regime forfettario).",
    "en": "Transaction without Italian VAT under Art. 1, paragraphs 54-89, of Italian Law no. 190/2014 (flat-rate scheme).",
}


def js_round(x: float) -> int:
    """Come Math.round di JavaScript (la meta' va verso l'alto): il round() di Python arrotonda al pari e darebbe centesimi diversi."""
    return math.floor(x + 0.5)


def stamp_duty_for(net_cents: int) -> int:
    return STAMP_DUTY_CENTS if net_cents > STAMP_DUTY_THRESHOLD_CENTS else 0


def document_language(currency: str) -> str:
    return "en" if currency == "USD" else "it"


def money(cents: int, currency: str) -> str:
    v = cents / 100
    return f"${v:,.2f}" if currency == "USD" else f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def compute_quote(items: list[dict]) -> dict:
    """Voci -> totale una tantum, canone mensile, acconto 50% e saldo con i bolli."""
    one_off = sum(max(0, js_round(i["price_cents"])) for i in items if not i.get("monthly"))
    monthly = sum(max(0, js_round(i["price_cents"])) for i in items if i.get("monthly"))
    deposit = js_round(one_off * DEPOSIT_RATE)
    balance = one_off - deposit
    tranches = []
    for name, net in (("acconto", deposit), ("saldo", balance)):
        if net > 0:
            st = stamp_duty_for(net)
            tranches.append({"tranche": name, "net_cents": net, "stamp_duty_cents": st, "total_cents": net + st})
    return {"one_off_cents": one_off, "monthly_cents": monthly, "tranches": tranches}


def check_quote(project_type: str, currency: str, items: list[dict]) -> dict:
    """Errori (bloccano il salvataggio) e avvertenze (vanno detti a Lorenzo)."""
    errors, warnings = [], []
    if currency not in CURRENCIES:
        errors.append(f"valuta {currency!r} non ammessa: solo EUR o USD, mai convertite")
    if project_type not in PROJECT_TYPES:
        errors.append(f"tipo {project_type!r} sconosciuto: usa uno tra {', '.join(PROJECT_TYPES)}")
    if not items:
        errors.append("preventivo senza voci")
    for n, i in enumerate(items, 1):
        if not str(i.get("name", "")).strip():
            errors.append(f"voce {n}: manca il nome")
        p = i.get("price_cents")
        if not isinstance(p, (int, float)) or isinstance(p, bool) or p < 0:
            errors.append(f"voce {n}: price_cents deve essere un numero >= 0 (centesimi)")
    if errors:
        return {"errors": errors, "warnings": warnings}
    q = compute_quote(items)
    base = BASE_PRICE_CENTS.get(project_type, 0)
    if project_type == "altro":
        warnings.append("tipo 'altro': il listino non ha un prezzo di partenza, il prezzo va confermato da Lorenzo")
    elif project_type == "manutenzione":
        if q["monthly_cents"] and q["monthly_cents"] < base:
            warnings.append(f"canone mensile sotto il listino di partenza ({money(base, currency)} al mese)")
    elif q["one_off_cents"] < base:
        warnings.append(f"totale una tantum {money(q['one_off_cents'], currency)} sotto il listino di partenza per "
                        f"'{TYPE_LABELS[project_type]}' ({money(base, currency)}): serve l'ok di Lorenzo")
    if q["one_off_cents"] == 0:
        warnings.append("nessuna voce una tantum: non ci sono acconto e saldo, solo canone")
    if currency == "USD":
        warnings.append("cliente in dollari: documenti in inglese e modulo W-8BEN-E (vedi /regole/fiscale)")
        if any(t["stamp_duty_cents"] for t in q["tranches"]):
            warnings.append("il portale applica il bollo di 200 centesimi anche in USD: confermare col commercialista se e in che importo va addebitato")
    return {"errors": errors, "warnings": warnings}
