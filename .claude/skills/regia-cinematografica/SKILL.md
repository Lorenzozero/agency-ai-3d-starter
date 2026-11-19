---
name: regia-cinematografica
description: Direzione artistica e regia per immagini e video di un sito: bibbia visiva del brand, hook dei primi 3 secondi, storyboard e shot list, prompt da professionista per ogni strumento (FLUX, Nano Banana, Seedance, Kling, Wan, Soul), coerenza del brand e uso del marketing. Usala per ogni decisione su immagini, video e movimento.
---

# Regia cinematografica e direzione artistica

## Il metodo (sempre in quest'ordine)
1. **Brand prima delle immagini**: scrivi la *bibbia visiva* (sotto). Senza, ogni immagine esce di un altro stile.
2. **Concetto e hook**: una idea sola che il visitatore capisce in 3 secondi.
3. **Storyboard**: per ogni sezione della pagina (ruolo) UNA immagine/scena con un compito preciso.
4. **Shot list**: per ogni scena inquadratura, movimento, luce, durata.
5. **Prompt**: scritti con la struttura qui sotto, nella lingua e con le regole dello strumento.
6. **Controllo coerenza**: rileggi tutti i prompt insieme prima di consegnare (checklist in fondo).

## Bibbia visiva del brand (10 righe, in inglese per i prompt)
- **Palette**: i 5 HEX del sito + come si usano (60% neutri, 30% secondario, 10% accento).
- **Luce**: una sola famiglia (es. "soft window daylight, warm, low contrast" oppure "low-key tungsten, deep shadows").
- **Ottica**: una focale dominante (35 mm racconto, 50 mm naturale, 85 mm ritratto/prodotto) e apertura (f/1.8-f/2.8 = sfondo morbido).
- **Texture**: grana (35 mm film grain leggera), pulizia, materiali ricorrenti (legno, vetro, pietra, tessuto).
- **Composizione**: regola dei terzi, spazio negativo dove va il testo, linee guida verso l'azione.
- **Soggetti**: cosa si mostra sempre (mani al lavoro, prodotto, luogo) e cosa MAI (volti riconoscibili, loghi, testo).
- **Movimento** (video): una sola famiglia (push-in lento, dolly laterale, handheld sottile, statico).
- **Tono**: 3 aggettivi (es. caldo, artigianale, sereno). **Frase di stile**: UNA riga fissa da incollare identica in ogni prompt.
- **Personaggio ricorrente** (se c'e'): scheda fissa di 1-2 righe (eta', abiti, tratti) ripetuta uguale in ogni prompt.
- **Negativi fissi** (solo per gli strumenti che hanno un campo *negative prompt*: no text, no logo, no watermark, no extra fingers, no distorted hands, no plastic skin). **FLUX/Cloudflare NON ha il campo negativo**: li' non scrivere "no text" dentro il prompt (rischia di far comparire proprio il testo) ma descrivi in positivo: "clean uncluttered surfaces", "hands out of frame", "close-up of a worn wooden handle".

## Hook (i primi 3 secondi: nel sito e' l'hero)
Formule: **risultato prima** (mostra il "dopo"), **problema in primo piano** (il disagio del cliente), **contrasto** (prima/dopo), **pattern interrupt** (inquadratura inattesa, movimento contro-intuitivo), **dettaglio sensoriale** (vapore, goccia, scintilla, texture), **domanda visiva** (l'oggetto nascosto che si rivela). Regole: un solo soggetto, azione leggibile anche muta, spazio per un titolo di max 8 parole, niente intro lenta. In un video: cambia qualcosa ogni 2-3 secondi.

## Grammatica cinematografica (usa il termine preciso)
- **Piani**: extreme wide / wide (luogo), medium (persona e gesto), close-up (volto, prodotto), extreme close-up (dettaglio/texture), insert (oggetto che spiega).
- **Angoli**: eye level (onesto), low angle (potenza, lusso), high angle (vulnerabilita', panoramica), top-down (prodotti, cibo, flat lay), dutch (tensione: con parsimonia).
- **Movimenti**: slow push-in (attenzione), pull-back reveal (scoperta), dolly sideways (eleganza), orbit/arc (prodotto), crane up (finale/respiro), handheld (realismo), whip pan (transizione), rack focus (sposta l'attenzione), timelapse (processo).
- **Luce**: golden hour (calore), blue hour (fiducia, tecnologia), soft window light (intimita'), rim/backlight (separazione), practicals (lampade in scena, atmosfera), chiaroscuro (drammatico), overcast (neutro, pulito).
- **Look**: anamorphic (cinema largo, flare), 35 mm film grain (calore), clean digital (tech), shallow depth of field (cura), color grade derivato dalla palette del brand (mai il solito teal-orange per default).
- **Formati**: 16:9 hero desktop, 21:9 cinema, 9:16 mobile/social, 1:1 e 4:5 social. Lascia sempre area di rispetto per il testo.

## Prompt per le IMMAGINI (struttura)
`[SOGGETTO 2-5 parole], [azione o stato], [ambiente], [inquadratura + obiettivo + apertura], [luce], [palette e materiali], [mood], [texture/grana]. [frase di stile]`
- **Colori con il NOME, mai con il codice HEX** ("muted sage green, warm sand, deep teal accent"): i modelli di immagini non capiscono `#1f5c63`. Traduci i codici della palette in nomi.
- Non scrivere il rapporto (16:9) nel testo: lo imposta la pipeline. I negativi solo dove esiste il campo dedicato (vedi sopra).
- **Soggetto per primo** (serve anche alla ricerca di foto di riferimento). Frasi naturali, 40-70 parole, niente elenchi di parole chiave a caso.
- Concreto > astratto: "steam rising from a hand-thrown ceramic cup on oak table" batte "beautiful coffee".
- Mai testo, loghi, scritte, marchi, persone riconoscibili. Mani, schiene, profili, dettagli funzionano meglio dei volti.
- Prompt in **inglese**. Nel sito `image` = soggetto + dettagli, vedi la guida del direttore creativo.
- **FLUX (Cloudflare, gratis, e' lo strumento della pipeline)**: ama frasi descrittive, luce e lente dichiarate, oggetti concreti; evita prompt troppo lunghi (oltre 90 parole) e il testo nell'immagine.

## Prompt per i VIDEO (struttura)
`[PIANO + soggetto], [UNA azione], [movimento di camera], [luce e atmosfera], [stile/look], [durata], [audio]`
- **Una sola azione per shot**, 4-8 secondi. Piu' azioni = pasticcio.
- Inizio e fine: se lo strumento accetta **start/end frame**, usali per controllare dove parte e dove arriva.
- Coerenza di personaggio/oggetto: **immagini di riferimento** + la stessa scheda personaggio nel prompt.
- Audio: dichiara se serve (ambiente, foley, musica) o chiedi muto per l'hero del sito (autoplay silenzioso, loop).
- Hero del sito: 5-8 s, loop fluido, soggetto lontano dal testo, niente tagli bruschi, 16:9 (e 9:16 per mobile).

## Gli strumenti (cosa sono davvero e quando usarli)
**Nella pipeline, gratis**
- **Cloudflare FLUX**: tutte le immagini del sito. Si guida con `image` (prompt) e `art_direction` (stile comune).
- **Foto del cliente** (`client:N`): hanno SEMPRE la precedenza se pertinenti. Video del cliente: diventano l'hero.
- **Video stock**: il costruttore usa una ricerca breve inglese (`video_query`, 2-4 parole) su stock gratuiti.
- Movimento senza video: zoom lento e grana sulle immagini (gia' nei template).
**Fuori pipeline, a mano (anche a pagamento): prepara i prompt, Lorenzo li lancia**
- **Higgsfield Soul 2.0**: foto UGC, moda, editoriali, personaggi; `soul_id` mantiene lo stesso volto tra le immagini.
- **Nano Banana 2 / Pro**: foto realistiche, riferimenti multipli, testo leggibile (Pro per qualita' massima, 4K).
- **GPT Image 2**: tipografia e testo nell'immagine, editing preciso.
- **Seedream 5 / FLUX.2**: immagini precise, 4K, aderenza al prompt.
- **Seedance 2.0 (e Mini)**: video con riferimenti immagine/video/audio, start/end frame, `genre` (action, horror, comedy, noir, drama, epic), audio nativo, 4-15 s, fino a 1080p/4K (la versione fast 480/720p).
- **Kling 3.0**: video multi-shot, audio sincronizzato, motion transfer, 3-15 s; **Wan 2.7**: audio sincronizzato e personaggio coerente, 2-15 s, 720p/1080p.
**Video gratis, a mano (crediti giornalieri sui siti web, niente API)**: Dreamina (modelli Seedance), Kling, Google Flow, PixVerse (risoluzione bassa), Runway (crediti una tantum, con filigrana). Le quantita' giornaliere cambiano per paese e per periodo: controllale sul sito prima di promettere qualcosa. Per ogni video prepara il prompt completo e dici di usare l'hero come immagine di partenza (start frame); Lorenzo lo genera, scarica l'mp4 e lo manda al bot con la didascalia "metti questo video come hero di <sito>": la fabbrica lo mette nell'hero. Il video del cliente ha sempre la precedenza.
Se non sai quale: immagini statiche per il sito = FLUX; un solo video hero = Seedance o Kling con start frame = l'immagine hero; coerenza del volto = Soul con soul_id.

## Coerenza del brand: checklist (rileggi TUTTI i prompt insieme)
- Stessa **frase di stile** in ogni prompt. Stessa famiglia di luce e di focale. Stessa palette (nomina i colori: "muted sage green and warm sand").
- L'**hero e' il key visual**: tutte le altre immagini ne ereditano luce e grading (la pipeline gia' lo passa come riferimento).
- Stesso personaggio = stessa scheda identica. Nessun elemento di un altro settore (es. cibo in un sito di avvocati).
- Ogni immagine ha un compito (tabella sotto): se non ne ha, toglila.

## Marketing visivo (collega l'immagine al ruolo della pagina)
| Ruolo | Cosa mostra | Formula |
|---|---|---|
| attenzione (hero) | la promessa gia' realizzata, un solo soggetto | risultato prima / dettaglio sensoriale |
| problema | il disagio o il desiderio, senza risolverlo | PAS: problema in primo piano |
| soluzione | il processo in 3 passi, mani al lavoro | prima-dopo-ponte |
| offerta | il prodotto/servizio chiaro, ben illuminato | chiarezza prima della bellezza |
| usp | il dettaglio che solo loro hanno (materiale, gesto, luogo) | prova concreta della differenza |
| prova | persone vere, luogo vero (foto del cliente), mai finte recensioni | autenticita' |
| extra (menu, catalogo, eventi) | i prodotti reali, foto coerenti tra loro | catalogo ordinato |
Ricorda i principi: **JTBD** (cosa deve ottenere il cliente), **StoryBrand** (il cliente e' l'eroe, il brand e' la guida), **riprova sociale e scarsita'** solo se VERE.

## Vincoli etici e di sicurezza
Mai volti di persone reali o riconoscibili, mai loghi o marchi altrui, mai testi o recensioni inventati nelle immagini, niente contenuti ingannevoli (deepfake, "prima/dopo" falsi). Le foto del cliente non si alterano oltre il fotoritocco. Dichiara quando un'immagine e' generata se il cliente lo richiede.

## Video stock gratuiti (Pixabay)
Se l'hero e' un `hero_video` e il cliente non ha un video proprio, usa `site_stock_video(query)`:
- Query in **inglese**, 2-4 parole **concrete** (soggetto + azione: "espresso pouring", "bicycle workshop", "waves beach aerial"), mai concetti astratti ("passione", "qualita'").
- Scegli tra i primi risultati: `adatto_hero` vero (orizzontale, 6-40 s), loop-friendly (movimento lento e continuo), peso basso, pertinenza alta. Meglio natura, oggetti, mani, luoghi; **evita volti e insegne leggibili**.
- Licenza: uso commerciale gratuito, nessuna attribuzione, ma niente marchi o loghi riconoscibili e **mai presentarlo come il locale del cliente**. La fabbrica aggiunge un promemoria per sostituirlo con un video reale.
- Consegna l'id scelto in `art.video.hero_id` (solo cifre). Se nessun risultato convince, riprova con un'altra query (le ricerche sono in cache 24 ore, massimo 100 al minuto: niente raffiche) oppure lascia `hero_query`.

## Consegna
`site_art_save(slug, art)` con: `art_direction` (la frase di stile, max 200 caratteri), `bibbia` (le 10 righe), `immagini` (per ruolo o `sezione-N`: `image` e, per liste, `items:[{image}]`), `video` (`hero_query` e un prompt video pronto) e `shotlist`. Rispondi in 6 righe: concetto, hook, palette/luce, cosa hai cambiato, cosa serve da Lorenzo per i video.
