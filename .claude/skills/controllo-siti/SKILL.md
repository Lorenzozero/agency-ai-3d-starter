---
name: controllo-siti
description: Come leggere lo stato dei siti dei clienti e riferirlo a Lorenzo: giu', lento, protetto, certificato in scadenza. Usala per ogni domanda sul fatto che un sito funzioni.
---

# Controllo siti

## Come leggere i risultati
- **Giu'**: due controlli di fila falliti (nessuna risposta o errore HTTP). Un solo errore non e' un guasto.
- **Protetto (401/403)**: il server risponde ma chiede accesso. E' normale per gli staging con password: non e' un guasto.
- **Lento**: oltre 6 secondi. Dillo, non allarmare.
- **Certificato**: avvisa sotto i 14 giorni; sotto i 3 e' urgente.

## Passi
1. `site_health` per lo stato dell'ultimo controllo (ogni 15 minuti).
2. Un sito preciso o un dato vecchio: `check_site` con il nome. Non accetta indirizzi nuovi.
3. `memory_search` sul nome: un guasto ripetuto va segnalato come tale.
4. Se un sito e' giu', proponi di coinvolgere il Deployer; tu non riavvii ne' pubblichi nulla.

## Come riferire
Prima cio' che non va, poi quanti sono a posto. Una riga per sito: nome, stato, tempo, giorni al certificato. Dici cosa vedi (codice HTTP, nessuna risposta), mai la causa supposta.
Se non ci sono siti monitorati dillo: non e' un guasto, i siti entrano quando sono online nel CRM o in `quotas.yaml`.
