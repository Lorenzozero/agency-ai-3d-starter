---
name: salute-sistema
description: Come leggere lo stato del sistema (server, servizi, router, fornitori, backup, canali) e riferirlo: cosa e' un problema vero, cosa e' normale, quali soglie contano. Usala per ogni domanda sulla salute o sui limiti.
---

# Salute del sistema

## Da dove parti
1. `system_status(only_problems=true)`: se e' vuoto, il sistema sta bene e lo dici in una riga.
2. Se c'e' un problema, `system_status(group=...)` sul gruppo interessato e `system_history` per vedere da quando.
3. Mai concludere da una sola misura: confronta con `sentinel_log` (riavvii, annullamenti) e con `router_stats`.

## Come leggere le voci
- **Stato**: `ok`, `warn` (attenzione), `down` (non funziona), `off` (fornitore spento: manca la chiave, non e' un guasto), `info` (voce solo documentata).
- **Misurato o documentato**: `measured: true` = letto adesso dal fornitore o dal registro del router. `false` = limite dichiarato dal fornitore, con data di verifica: non e' una misura, dillo.
- **Quote**: da 75% attenzione, da 95% critico. OpenRouter :free e' 50 richieste al giorno finche' i crediti acquistati sono sotto 10 $; il router si ferma da solo a 45.
- **Router**: un fornitore con tanti errori (oltre il 60% con almeno 10 richieste) va guardato con `router_stats`; un livello «chiuso» e' un limite giornaliero raggiunto e si riapre a mezzanotte UTC.
- **«Mai pagare»**: la voce `Protezione «mai pagare»` deve restare a zero. Se segna una spesa e' un problema grave: avvisa Lorenzo subito.
- **Server**: carico medio oltre il numero di CPU, memoria oltre l'85%, disco oltre l'85% sono da segnalare. Il backup piu' vecchio di 26 ore e' un problema.
- **Servizi**: se uno e' fermo, i controlli automatici lo riavviano dopo due giri falliti (solo hlab-api, hlab-worker, hlab-router, hermes). Tu non riavvii nulla.

## Cosa non e' un problema
Un fornitore `off` per scelta (Cerebras, Mistral: richiedono pagamento), il limite giornaliero raggiunto di un fornitore gratuito (e' previsto, il router passa al successivo), un singolo 429.

## Come riferisci
Una riga per problema: cosa, quanto, da quando. Poi quello che e' a posto, in una riga sola. Se serve una decisione di Lorenzo, la metti in coda con una domanda sola.
