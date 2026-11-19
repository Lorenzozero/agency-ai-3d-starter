---
name: audit-seo
description: Come controllare e riferire la SEO tecnica e on-page di un sito cliente: cosa guardare, cosa pesa di piu', cosa NON puoi sapere. Usala per ogni richiesta sulla SEO di un sito.
---

# Audit SEO

## Strumento
`seo_check(name)`: pagina principale + un campione (al massimo 6 pagine dalla sitemap), robots.txt, sitemap, duplicati di titoli e descrizioni, 10 link interni controllati.
Ritorna punteggio 0-100, voto, problemi con gravita' (alta, media, bassa) e la correzione. E' un CAMPIONE: dillo, non presentarlo come l'analisi di tutto il sito.

## Cosa pesa di piu' (priorita')
1. **Indicizzazione**: `noindex` per errore, `Disallow: /` in robots.txt, sitemap assente o rotta. Se il sito non si puo' indicizzare, tutto il resto e' secondario.
2. **Titolo e H1**: ogni pagina un titolo unico di 30-60 caratteri con la parola chiave, un solo H1.
3. **Meta description** di 70-160 caratteri (non e' un fattore di posizione, ma decide il clic).
4. **Mobile**: meta viewport e pagina leggibile da telefono.
5. **Link rotti** interni (404): sprecano il "budget di scansione" e rovinano l'esperienza.
6. **Canonical**, lingua (`lang`, hreflang se multilingua), immagini con `alt`, Open Graph per le anteprime social, dati strutturati (schema.org: LocalBusiness, Organization, FAQ, Product).
7. **Contenuti**: pagine con pochissimo testo difficilmente si posizionano.

## Cosa NON sai (e non inventi)
Posizioni su Google, parole chiave, traffico, backlink, autorevolezza: non hai quei dati. Se servono, di' che serve Search Console o uno strumento dedicato.
Le prestazioni sono un fattore SEO: rimanda all'agente Performance per quelle.

## Come riferire
Punteggio e voto in una riga; i problemi ALTI per primi, ognuno con la correzione in una riga; poi un massimo di tre migliorie di valore. Confronta con l'ultima analisi
(`memory_search`) e salva data, sito, punteggio e problemi aperti (`memory_write`). Se il sito e' dietro password o in costruzione (staging), molti problemi sono previsti: dillo.

## Strumenti piu' profondi (per una persona, non per te)
SEOnaut (audit open source con interfaccia), python-seo-analyzer (analisi e crawl da script), linkinator (link rotti su tutto il sito), Lighthouse (categoria SEO). Vedi strumenti-open-source.
