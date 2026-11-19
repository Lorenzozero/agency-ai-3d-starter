---
name: delega-coo
description: Come il COO assegna lavoro agli specialisti e controlla il risultato. Usala quando devi delegare un compito o riassumere il lavoro di piu' agenti.
---

# Delegare bene

Lo specialista parte da zero: vede solo il messaggio con cui lo chiami. Scrivi quindi un incarico completo.

## Modello di incarico
1. **Obiettivo**: cosa serve e perche', in una frase.
2. **Contesto**: nome del cliente o del progetto, date, cio' che gia' sai e che gli risparmia lavoro.
3. **Risultato atteso**: forma e lunghezza ("elenco di scadenze entro 14 giorni, una riga ciascuna").
4. **Limiti**: cosa non deve fare (es. non scrivere a nessuno), quanti strumenti usare.

## Chi fa cosa
- `account-crm`: clienti, contatti (mascherati), lead, incassi, scheda cliente; propone aggiornamenti di stato/note dei lead.
- `project-manager`: progetti, task, scadenze, progetti fermi, briefing, proposte di aggiornamento stato.
- `commerciale`: listino, preventivi in bozza (acconto, saldo, bolli), condizioni e regole fiscali segnalate. Le bozze le approva Lorenzo.
- `ops-uptime`: i siti dei clienti sono online? tempi di risposta, certificati in scadenza. Sola lettura: se un sito e' giu' e va sistemato, coinvolgi `deployer`.
- `deployer`: porta modifiche su GitHub (PR) e Netlify (anteprima), controlla le quote. Merge e pubblicazione non sono suoi: li decide Lorenzo.
- `memory-keeper`: trasforma i feedback di Lorenzo in regole proposte; riordina la memoria condivisa. Non lo usi per rispondere a domande di business.
- Domande che toccano soldi e scadenze insieme (es. "chi deve ancora pagare e cosa scade?"): entrambi, in parallelo, poi tu unisci.

## Quanti agenti
- Domanda su un cliente o un dato: UN agente.
- Confronto o attivita' divisibile (piu' clienti, piu' fonti indipendenti): 2-3 in parallelo.
- Lavori interdipendenti (uno usa il risultato dell'altro): in sequenza, mai in parallelo.

## Dopo
Leggi la risposta con occhio critico: dati mancanti, numeri senza fonte, contraddizioni tra agenti. Se qualcosa non torna, richiedi
la verifica con una domanda mirata. Nel rapporto a Lorenzo separa i fatti dalle azioni che aspettano la sua approvazione.
