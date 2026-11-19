---
name: costruzione-siti
description: Come costruire, provare e correggere un sito dallo spec con la fabbrica, e quando dichiararlo pronto. Usala dopo che il direttore creativo ha salvato lo spec.
---

# Costruzione dei siti

## Flusso
1. `site_list` e `site_spec_load(slug)`: verifica che lo spec esista. Se manca, dillo e fermati: lo scrive il direttore creativo.
2. `site_build(slug)`: costruisce dai template, genera le immagini, prova il sito nel browser (desktop e mobile) e applica l'auto-correzione. Richiede minuti; c'e' un tetto di costruzioni al giorno, non ripetere alla cieca.
3. Leggi il rapporto (`site_report`): verdetto, errori nel browser, immagini rotte o segnaposto, richieste fallite.

## Cosa fare col verdetto
- **pronto**: riferisci sezioni, font, palette e le cose da confermare col cliente. Il passo dopo e' del Deployer (propone la pubblicazione).
- **da rivedere** (segnaposto o richieste fallite): spiega la causa piu' probabile (chiavi immagini mancanti, risorse esterne). Puoi pubblicare solo se Lorenzo lo accetta.
- **bloccato** (errori nel browser, immagini rotte): NON e' pubblicabile. Correggi lo SPEC (non il codice: non puoi toccarlo), ritenta UNA volta, poi riferisci a Lorenzo con gli errori esatti.

## Siti in modifica
Se `site_list` mostra `modifica_di_sito_pubblicato: true`, quel lavoro e' la modifica di un sito gia' online: `site_build` lo ricostruisce sullo stesso repo e riusa le immagini gia' generate. Nel rapporto `modifica: true` e `immagini.riusate` lo confermano. Se il sito era un ritocco (`ritocco: true`) lo scheletro non e' richiesto: riferiscilo.

## Scheletro
Il rapporto elenca i `ruoli` della pagina costruita. Se manca una sezione dello scheletro (di solito perche' i suoi testi erano troppo scarni e il costruttore l'ha scartata) il verdetto e' 'bloccato': chiedi al Copywriter di completare i testi di quel ruolo e ricostruisci.

## Regole
- Non scrivi HTML, CSS o JavaScript: il sito nasce solo dai template e dallo spec.
- Non pubblichi: la pubblicazione e' una proposta del Deployer approvata da Lorenzo.
- Dati del cliente non verificati restano in "da confermare col cliente": dillo sempre.
