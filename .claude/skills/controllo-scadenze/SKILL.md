---
name: controllo-scadenze
description: Come ordinare scadenze e progetti per urgenza e preparare il briefing del Project Manager. Usala per "cosa scade", "cosa e' in ritardo", "cosa e' fermo" e per il briefing mattutino.
---

# Controllo scadenze

## Dati da raccogliere
1. `list_deadlines` con 30 giorni: include anche cio' che e' gia' scaduto.
2. `stale_projects` con 14 giorni: progetti fermi.
3. Per ogni progetto citato, `project_overview` solo se serve capire il perche' (task aperti, documenti in bozza).

## Ordine di urgenza
1. **Scaduto** (data passata, non chiuso): da piu' vecchio a piu' recente.
2. **Oggi o entro 3 giorni**.
3. **Entro 14 giorni**.
4. **Entro 30 giorni**.
5. **Fermi** (nessun aggiornamento da 14+ giorni, anche senza scadenza).

## Formato del briefing
```
URGENTE (n)
- <Cliente> / <Progetto>: <cosa> — <data> (<n> giorni di ritardo | tra <n> giorni)
QUESTA SETTIMANA / PROSSIMI 30 GIORNI
- ...
FERMI
- <Progetto>: nessun aggiornamento da <n> giorni, stato <stato>
PROPOSTE REGISTRATE: #id breve descrizione (in attesa di Lorenzo)
```
Una riga per voce, niente giri di parole. Se non c'e' nulla di urgente dillo in una riga.

## Quando proporre una modifica
Solo con un motivo chiaro e verificabile nei dati: progetto con tutti i task fatti ma stato non chiuso, scadenza passata da oltre 7 giorni
senza task aperti, task segnato non fatto con documento approvato. Una proposta per problema, mai in blocco.
