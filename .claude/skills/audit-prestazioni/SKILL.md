---
name: audit-prestazioni
description: Come misurare e riferire le prestazioni di un sito cliente: soglie dei Core Web Vitals, cosa significano le misure di rete, come ordinare i rimedi. Usala per ogni richiesta sulla velocita' di un sito.
---

# Audit prestazioni

## Strumento
`perf_check(name, strategy)`: misure di rete proprie (tempo al primo byte, peso, compressione, cache, redirect, HTTP/2, script e immagini) + Lighthouse via PageSpeed Insights.
Il nome viene da `list_audit_sites`. Mobile di default: Google giudica prima il mobile. Puo' richiedere fino a un minuto. Se PageSpeed risponde con un errore o col limite, dillo e usa le sole misure proprie.

## Soglie (Core Web Vitals e affini, "buono")
- **LCP** (contenuto principale visibile) <= 2,5 s; oltre 4 s e' scarso.
- **CLS** (spostamenti di layout) <= 0,1; oltre 0,25 e' scarso.
- **INP** (reattivita') <= 200 ms. **TBT** (proxy di laboratorio) <= 200 ms. **FCP** <= 1,8 s.
- **Tempo al primo byte** < 800 ms: e' il server, non il frontend. Oltre 1,8 s e' grave.
- **Punteggio Lighthouse** 90-100 buono, 50-89 da migliorare, sotto 50 scarso. Il laboratorio non e' l'esperienza reale: se c'e' `esperienza_reale_utenti`, e' il dato che conta di piu'.

## Dove si perde di solito (in ordine di effetto)
1. Server lento o senza cache: CDN, cache della pagina, hosting.
2. Immagini pesanti: formati moderni (WebP/AVIF), dimensioni giuste, `loading="lazy"` sotto la piega, `width` e `height` per evitare CLS.
3. JavaScript: script nell'head senza `defer`/`async`, librerie inutilizzate, framework per una pagina statica.
4. Nessuna compressione (gzip/brotli), nessun HTTP/2, troppi redirect.
5. Font: `font-display: swap`, pochi pesi, precaricare quello principale.

## Come riferire
Una riga di voto ("Lento sul mobile: LCP 4,8 s, Lighthouse 52"), poi le TRE cose piu' utili con il guadagno atteso, poi il confronto con l'ultima misura in memoria
(`memory_search`). Salva nella tua memoria data, sito, punteggio e metriche principali (`memory_write`) per poter dire "e' migliorato / peggiorato".
Distingui quello che hai MISURATO da quello che SUPPONI. Se il sito e' di staging con password o protetto, le misure sono quelle della pagina di accesso: dillo.

## Strumenti piu' profondi (per una persona, non per te)
sitespeed.io, Lighthouse CI (misure ripetute in CI, con soglie che bloccano le regressioni), WebPageTest (prove da luoghi e reti diversi). Vedi la skill strumenti-open-source.
