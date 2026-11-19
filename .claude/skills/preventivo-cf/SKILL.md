---
name: preventivo-cf
description: Come preparare un preventivo di Agency AI dal listino, con acconto 50%, saldo, bolli e controlli. Usala per ogni richiesta di preventivo o di prezzo.
---

# Preventivo

## Passi
1. **Regole e storico**: `shared_search` ("listino", "acconto", "fiscale", "valute") e `memory_search` con il nome del cliente.
2. **Cliente o lead**: `get_client` (o `list_leads` se e' un lead). Valuta: cliente americano = USD e documenti in inglese; altrimenti EUR.
3. **Tipo di progetto**: landing, webapp, mobile, manutenzione, altro. `price_list` per prezzo di partenza e voci suggerite.
4. **Voci**: una riga per ciascuna parte (nome, `price_cents` in centesimi, `monthly` true solo per i canoni). Parti dal listino e dalle voci
   suggerite; se il lavoro e' piu' ricco del listino aumenta le voci, non inventare sconti.
5. **Calcolo**: `compute_quote`. Poi `check_quote`: leggi gli errori (bloccano) e le avvertenze (le riferisci).
6. **Bozza**: `save_quote_draft` con client, tipo, valuta, voci e una nota (a chi e per cosa). Non parte verso nessuno.

## Come riferire
```
Preventivo per <cliente> — <tipo> — <valuta>
Una tantum: <totale> | Canone: <mensile o "nessuno">
Acconto 50%: <importo> | Saldo: <importo>  (con bollo se previsto)
Avvertenze: <elenco da check_quote>
Da confermare da Lorenzo: <prezzo sotto listino, tipo "altro", questioni fiscali segnalate>
Bozza #<id> in attesa di approvazione (non inviata)
```

## Da non fare
- Calcolare importi a mano o arrotondare a mente.
- Convertire tra EUR e USD.
- Salvare una bozza con errori di `check_quote`.
- Promettere date di consegna, sconti o clausole non presenti nelle regole condivise.
