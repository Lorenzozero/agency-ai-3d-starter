---
name: audit-sicurezza
description: Come eseguire e riferire un controllo di sicurezza PASSIVO su un sito cliente: intestazioni, certificato, cookie, file esposti. Include i limiti legali: niente scanner attivi. Usala per ogni richiesta sulla sicurezza di un sito.
---

# Audit sicurezza (passivo)

## Regola che viene prima di tutto
Fai SOLO il controllo passivo di `security_check`: richieste di lettura verso un sito GIA' registrato. NON lanci, non suggerisci di lanciare "al volo" e non simuli scanner attivi
(nuclei, OWASP ZAP, nikto, wapiti), prove di password, iniezioni o test di carico. Provarli su un sito senza autorizzazione scritta puo' essere reato. Sono lavoro di una persona,
solo su siti dell'agenzia o con autorizzazione scritta del cliente: se serve, dillo a Lorenzo e fermati.

## Cosa controlla `security_check`
- **Intestazioni** (criteri dell'OWASP Secure Headers Project): HSTS, Content-Security-Policy, X-Content-Type-Options, X-Frame-Options o frame-ancestors, Referrer-Policy, Permissions-Policy, versione del server esposta.
- **Cookie**: Secure, HttpOnly, SameSite.
- **Certificato e TLS**: validita', scadenza, protocollo (TLS 1.2/1.3), HTTP/2.
- **http -> https**: redirect permanente. **Contenuto misto**: risorse http dentro una pagina https.
- **Esposizioni note**: `/.git/HEAD`, `/.git/config`, `/.env`, `/.DS_Store`, `phpinfo.php`, `/server-status`. **CORS** che riflette origini estranee con le credenziali.
Voto da A a F: 100 meno 15 per problema alto, 7 per medio, 3 per basso.

## Priorita'
1. **URGENTE**: file esposti (.git, .env), certificato scaduto o non valido, sito solo http, CORS aperto con credenziali. Aprili nel rapporto. Se c'e' un `.env` o un `.git` esposto,
   ricorda che le chiavi e le password che contenevano vanno CAMBIATE, non basta nascondere il file.
2. **Importante**: HSTS e CSP mancanti, cookie senza flag, clickjacking, redirect https mancante.
3. **Miglioramenti**: Referrer-Policy, Permissions-Policy, versione del server visibile, security.txt.

## Come riferire
Voto A-F in una riga; i problemi gravi per primi, ognuno con il rimedio in una riga; poi cosa e' a posto. Dici sempre che e' un controllo PASSIVO e che non sostituisce un test completo.
Confronta con l'ultimo controllo (`memory_search`); salva data, sito, voto e problemi aperti (`memory_write`). Non scrivere in memoria condivisa i dettagli sfruttabili.
Uno staging protetto da password mostra le intestazioni della pagina di accesso: dillo.

## Strumenti piu' profondi (per una persona, non per te)
OWASP ZAP (Apache-2.0), nuclei (MIT, con template aggiornati), nikto, wapiti, testssl.sh per il TLS. Vedi strumenti-open-source per le regole d'uso.
