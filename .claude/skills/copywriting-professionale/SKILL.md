---
name: copywriting-professionale
description: Linee guida da copywriter professionista per le pagine dei clienti: schema attenzione-problema-soluzione-offerta-USP-prova-CTA-FAQ, formule per titoli, offerta, CTA e domande frequenti, tono di voce e cose vietate. Usala per ogni testo di un sito.
---

# Copywriting professionale

## Regola madre
Scrivi per UNA persona: il cliente finale dell'attivita', non il titolare. Parti da cio' che vuole o teme, non da cio' che l'azienda e' brava a fare. Ogni frase deve rispondere a "e quindi a me cosa cambia?".

## Lo schema della pagina, ruolo per ruolo
| Ruolo | Scopo | Come scriverlo |
|---|---|---|
| **attenzione** (hero) | fermare lo scroll in 3 secondi | Titolo = promessa concreta (risultato + per chi, max 8 parole). Sottotitolo = come/perche' ci si puo' fidare, 1 riga. Una sola azione. |
| **problema** | farsi riconoscere | Descrivi la situazione di chi legge con le sue parole (frustrazione, rischio, tempo perso). Niente soluzioni qui. 35-60 parole. |
| **soluzione** (transizione) | passare dal problema alla risposta | "Ecco come lo risolviamo" in 3 passi semplici (verbo all'inizio di ogni passo). Mostra il percorso, non la tecnica. |
| **offerta** | rendere chiaro cosa si compra | Cosa e' incluso (3-6 punti, in termini di risultati), a che condizioni, prezzo o "su preventivo", cosa succede dopo la richiesta. Riduci il rischio (garanzia reale, preventivo gratuito se e' vero). |
| **usp** | perche' proprio loro | 2-4 differenze VERE e verificabili, non "qualita' e passione". Formula: "Unici perche' [fatto]. Quindi [beneficio per il cliente]". |
| **prova** | credibilita' | Solo recensioni, numeri o garanzie reali forniti dal cliente. Mai inventati. Se mancano: garanzie verificabili e una voce in `todo` per raccoglierle. |
| **cta** | far agire | UN invito, verbo + beneficio ("Prenota la prima consulenza gratuita"). Mai "Invia", "Clicca qui". Sotto: cosa succede dopo (es. "Ti rispondiamo entro 24 ore"), solo se vero. |
| **faq** | togliere le obiezioni | Le vere domande che bloccano l'acquisto: prezzo, tempi, come funziona, garanzie, cosa serve da parte del cliente, cosa succede se non va bene. Risposta in 1-3 frasi, la prima frase risponde subito. |

Per le sezioni **extra** (menu, catalogo, listino, area servita...): testo di servizio, chiaro e scansionabile, nomi reali dei prodotti, mai prezzi inventati.

## Formule che funzionano
- Titolo: *[Risultato desiderato] senza [ostacolo]* · *Da [situazione brutta] a [situazione buona] in [tempo]* · *[Numero vero] [cosa] per [chi]*.
- PAS (problema-agitazione-soluzione) per problema+soluzione; AIDA (attenzione-interesse-desiderio-azione) per tutta la pagina; Prima-Dopo-Ponte per la soluzione.
- Specifico batte generico: "Consegna in 48 ore a Bologna" > "Consegne rapide".

## Tono di voce
- Italiano naturale, frasi brevi, verbi attivi, concretezza. Scegli UN registro (tu/lei) in base al cliente e tienilo ovunque.
- Titoli max 8 parole; racchiudi UNA parola chiave tra *asterischi* (diventa corsivo nel colore d'accento).
- Rispetta i limiti: titolo 110 caratteri, sottotitolo 220, testo 480 (manifesto 35-60 parole), voci di elenco 70/260, risposta FAQ 520, meta description max 155.

## Vietato
- Cliche: "leader del settore", "soluzioni innovative", "a 360 gradi", "il nostro team di professionisti", "passione e dedizione", "qualita' al giusto prezzo", "siamo qui per te".
- Inventare numeri, anni di esperienza, premi, recensioni, nomi di clienti, prezzi, certificazioni. Se manca il dato: lascialo fuori e aggiungi una voce in `todo`.
- Parlare di tecnologia (3D, sito, IA, animazioni) nel testo: si parla del cliente e dei suoi clienti.
- Promesse che l'attivita' non puo' mantenere o superlativi assoluti non dimostrabili ("il migliore").

## SEO nei testi
Il titolo della pagina e la meta description contengono attivita' + zona/beneficio in modo naturale. Niente ripetizione forzata di parole chiave. Le FAQ usano le domande come le farebbe una persona.

## Come consegni
1. Leggi lo spec (`site_spec_load`): ruoli, sezioni extra e tipo di ogni sezione. Se manca, fermati: lo scrive il direttore creativo.
2. Leggi i fatti sul cliente (CRM, `site_profile` per il tono e i testi attuali). Se mancano fatti essenziali, UNA domanda precisa.
   Se il sito e' una modifica di uno gia' pubblicato (`modifica_di_sito_pubblicato` in `site_list`) e non ha i ruoli, indirizza i testi per posizione: chiavi `sezione-1`, `sezione-2`... Cambia solo i testi richiesti: gli altri restano uguali.
3. Scrivi tutto in un colpo con `site_copy_save(slug, copy)`: chiavi = ruoli, `extra-1`..., `meta`. Dove non hai dati veri: campo vuoto + voce in `todo`.
4. Riferisci in 6 righe: promessa dell'hero, l'USP scelta, cosa manca da confermare col cliente. Non costruisci e non pubblichi.

## Controllo finale
Una promessa chiara in alto? Il problema e' riconoscibile? L'offerta dice cosa, quanto, e cosa succede dopo? Ogni USP e' verificabile? Nessun dato inventato? Un solo CTA? Le FAQ rispondono alle vere obiezioni? Nessun cliche?
