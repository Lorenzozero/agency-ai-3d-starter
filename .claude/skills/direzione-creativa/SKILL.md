---
name: direzione-creativa
description: Come progettare lo spec di un sito cliente (marca, palette, testi, sezioni) per la fabbrica di siti. Usala per ogni richiesta di un nuovo sito o di un rifacimento.
---

# Direzione creativa

## PRIMA DI TUTTO: nuovo sito o modifica?
1. `site_existing_list`: i siti gia' pubblicati. Confronta con il cliente (nome del progetto, indirizzo nel CRM: un sito su github.io con lo stesso nome o lo stesso indirizzo e' LUI).
2. Il cliente ha gia' un sito -> e' una **MODIFICA**: `site_open(repo)`, lavora sullo spec scaricato e salvalo con `site_spec_save` (resta sullo stesso repo e sullo stesso indirizzo). **Mai un secondo sito per lo stesso cliente** se Lorenzo non lo chiede esplicitamente.
3. In una modifica cambi SOLO cio' che e' stato chiesto: lascia invariati marca, palette, font, testi e i prompt delle immagini (con prompt identico l'immagine viene riusata, senza costi). Se la richiesta e' adeguare il sito allo scheletro, aggiungi `role` a tutte le sezioni e le sezioni che mancano (offerta, prova, faq...) mantenendo quelle che gia' funzionano.
4. Ritocco piccolo (un colore, un telefono, una frase)? `ritocco: true` e lo scheletro non viene imposto.
5. Dubbio su quale sito sia? Chiedi, non indovinare. Nessun sito esistente per quel cliente -> sito nuovo, con lo scheletro.

## Prima di scrivere
0. Se esiste, leggi `site_context(slug)`: contiene la richiesta con le risposte del titolare, i dati letti dai link e le foto/video del cliente (`client:N`). Le domande le ha gia' fatte il consulente: non farne altre, usa quello che c'e'.
1. Raccogli il cliente: `get_client`, `project_overview`, `site_profile` (se ha gia' un sito: contatti, colori, caratteri, tecnologia). Se mancano dati essenziali (attivita', zona, servizi) fai UNA domanda precisa e fermati.
2. Leggi `site_guide`: contiene lo schema dello spec, le sezioni disponibili, i font e le palette curati e le regole. Non inventare sezioni o campi che non sono nel manuale.

## Lo scheletro (obbligatorio)
Ogni pagina segue lo schema della lavagna: **attenzione** (hero) -> **problema** -> **soluzione** -> **offerta** -> **usp** -> **prova** -> (extra) -> **cta** -> **faq**. Ogni sezione dello spec ha `"role"`; `site_guide` spiega per ogni ruolo quali tipi usare.
Le sezioni **extra** (max 4, `"role": "extra"` + `"extra_why"`) sono su misura: menu, catalogo, listino, galleria lavori, area servita, orari... Proponile SOLO se un servizio o prodotto reale del cliente le giustifica.
Tu scrivi una BOZZA dei testi (serve perche' lo spec sia valido); i testi definitivi li scrive il Copywriter.

## Come scrivi lo spec
- Solo JSON, nessun codice. Scegli la palette dalle curate e adattala al marchio; un solo colore d'accento.
- Testi in italiano, concreti, senza clichè. Non citare la tecnologia (3D, sito, IA) nel copy.
- NON inventare contatti, recensioni, premi, prezzi o numeri: se non li hai, lascia vuoto e scrivi la voce in `todo` ("da far confermare al cliente").
- Prompt delle immagini in inglese: prima il soggetto (2-5 parole), poi i dettagli. Mai persone riconoscibili, mai testo.
- Scegli `experience` con criterio (classic per le attivita' sobrie, cinema per ospitalita'/food, scene3d per prodotti iconici).

## Consegna
Salva con `site_spec_save(slug, spec)` (slug: minuscole, numeri, trattini, es. `pizzeria-da-mario`). Poi riferisci in 5 righe: marca, palette, sezioni, cosa manca (`todo`). Non costruisci e non pubblichi: il passo dopo e' del costruttore.
