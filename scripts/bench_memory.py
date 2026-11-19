"""Prova della memoria con il modello VERO, sul VPS: python scripts/bench_memory.py
Riempie uno store temporaneo con 12 voci italiane, misura scrittura e ricerca ibrida, controlla top-1."""
import os
import resource
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.pop("HLAB_FAKE_EMBED", None)

from hlab import config  # noqa: E402
from hlab.memory import MemoryStore  # noqa: E402

DOCS = {
    "/clienti/ristorante-aurora.md": "Ristorante Aurora: landing 3D sensoriale bordeaux e avorio, progetto chiuso, lead convertito a 2.050 euro.",
    "/clienti/studio-nova.md": "Studio Nova: salone di toelettatura a Piove di Sacco, hero con doppio scrub cane naturale e toelettato.",
    "/clienti/bottega-artigiana.md": "Bottega Artigiana: cono 3D con colatura e ordine vaschette su WhatsApp, in pausa dal 14 ottobre.",
    "/clienti/studio-fenio.md": "Studio dentistico Fenio a Sant'Angelo di Piove di Sacco: nessun sito, solo AlfaDocs, punteggio lead 98.",
    "/regole/anti-chargeback.md": "Clausola anti-chargeback: acconto non rimborsabile e silenzio-assenso dopo dieci giorni dalla consegna.",
    "/regole/iva-usa.md": "Esenzione IVA per servizi B2B verso clienti USA, articolo 7-ter, modulo W-8BEN-E con ritenuta zero.",
    "/regole/domini-ssl.md": "Il dominio va rinnovato ogni anno; il certificato SSL scade dopo novanta giorni se non automatico.",
    "/clienti/atelier.md": "Atelier Milano: app Flutter di prenotazioni con backend Cloudflare Worker, D1 e R2 per le foto.",
    "/clienti/prima-scelta.md": "Pizzeria Prima Scelta: asporto e domicilio a zero commissioni tramite WhatsApp, landing sulla porta 8085.",
    "/clienti/crossbay.md": "Crossbay Car Wash a Queens New York: possibile vendita da 3.000 euro non ancora chiusa.",
    "/regole/listino.md": "Preventivo: listino base 59 euro al mese oppure 199 euro piu' 39 per l'abbonamento Smart Proximity.",
    "/regole/segreti.md": "Il token del bot Telegram non deve mai finire nei log ne' nel repository pubblico.",
}
QS = [("quale cliente ha un sito con colori bordeaux e avorio?", "/clienti/ristorante-aurora.md"),
      ("chi fa la toelettatura per cani?", "/clienti/studio-nova.md"),
      ("progetto sospeso con il gelato", "/clienti/bottega-artigiana.md"),
      ("lead senza sito web con punteggio altissimo, dentista", "/clienti/studio-fenio.md"),
      ("cosa succede se il cliente chiede un rimborso dell'anticipo?", "/regole/anti-chargeback.md"),
      ("tasse sulle fatture per aziende americane", "/regole/iva-usa.md"),
      ("quando scade il certificato del sito?", "/regole/domini-ssl.md"),
      ("app per prenotare dal parrucchiere", "/clienti/atelier.md"),
      ("ordini da asporto senza commissioni", "/clienti/prima-scelta.md"),
      ("trattativa aperta a New York", "/clienti/crossbay.md"),
      ("quanto costa l'abbonamento mensile?", "/regole/listino.md"),
      ("dove vanno custodite le chiavi dei bot?", "/regole/segreti.md")]

with tempfile.TemporaryDirectory() as d:
    config.MEMORY_DIR = Path(d)
    t0 = time.time()
    m = MemoryStore("bench")
    print(f"modello {m.emb.name} dim={m.dim}, pronto in {time.time() - t0:.1f}s")
    t0 = time.time()
    for p, t in DOCS.items():
        m.write(p, t, author="bench")
    print(f"scrittura di {len(DOCS)} voci: {(time.time() - t0) * 1000:.0f} ms ({(time.time() - t0) * 1000 / len(DOCS):.0f} ms l'una)")
    hits = 0
    t0 = time.time()
    for q, want in QS:
        r = m.search(q, k=3)
        hits += bool(r) and r[0]["path"] == want
    dt = (time.time() - t0) / len(QS)
    print(f"ricerca ibrida: top-1 {hits}/{len(QS)} | {dt * 1000:.1f} ms a query | RSS picco {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB")
    print(m.stats())
