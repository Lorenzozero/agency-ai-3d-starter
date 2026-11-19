---
name: estrai-regole
description: Come trasformare feedback e rifiuti di Lorenzo in regole comportamentali formali per un agente. Usala quando elabori feedback non letti.
---

# Estrarre regole dal feedback

## Quando nasce una regola
- Lo stesso errore compare in 2 o piu' feedback, oppure
- un solo feedback ha un commento chiaro e l'errore e' generale (non legato a un solo cliente).
Senza commento esplicito non si propone nulla: si segnala.

## Come si scrive
- Italiano, imperativa, una o due frasi, sotto i 300 caratteri.
- Generale: niente nomi di clienti, importi o date. "Quando il cliente e' in dollari, mostra importi e condizioni in inglese" si'; "Per Officina Rapida scrivi in inglese" no.
- Verificabile: chi legge deve poter dire se l'ha rispettata.
- Una regola per causa. Prima di proporre, `rules_for`: se esiste una regola simile, non duplicare.

## Cosa NON puo' essere una regola
Permessi, strumenti, livelli di autonomia, approvazioni, segreti, "ignora/salta/bypassa". Quelle cose stanno nel codice: se un feedback le tocca,
non proporre una regola, segnalalo a Lorenzo.

## Dopo
`mark_processed` su tutti i feedback letti. Rapporto: regole proposte con prove, feedback senza motivo, azioni per Lorenzo.
