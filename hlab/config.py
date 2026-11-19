"""Percorsi e limiti del laboratorio, tarati sul VPS Oracle (2 vCPU ARM, ~11 GB RAM, niente GPU)."""
import os
from pathlib import Path

LAB_HOME = Path(os.environ.get("HLAB_HOME") or Path(__file__).resolve().parents[1])
AGENTS_DIR = LAB_HOME / "agents"
SKILLS_DIR = LAB_HOME / ".claude" / "skills"
DATA_DIR = Path(os.environ.get("HLAB_DATA") or LAB_HOME / "data")
MEMORY_DIR = DATA_DIR / "memory"
MODELS_DIR = Path(os.environ.get("HLAB_MODELS") or LAB_HOME / "models")  # fuori da /tmp: sopravvive ai riavvii
EVENTS_DB = DATA_DIR / "events.db"
SHARED_STORE = "_shared"

# Embedding. Misurato sul VPS su 20 domande italiane sulle regole (16 voci): multilingual-e5-large 20/20 al primo posto
# (query 140 ms, ~1,3 s a scrittura, RAM di picco 3,2 GB); potion-multilingual-128M 11/20 ma 11.000 doc/s e meno RAM;
# potion + reranker bge-v2-m3-int8 17/20 e 1,8 s a query. La memoria si scrive e si interroga poco: conta la qualita'.
# Per una macchina piu' piccola: HLAB_EMBED_MODEL=minishlab/potion-multilingual-128M (la migrazione e' automatica).
EMBED_MODEL = os.environ.get("HLAB_EMBED_MODEL", "intfloat/multilingual-e5-large")
EMBED_THREADS = int(os.environ.get("HLAB_EMBED_THREADS", "2"))

# Memoria: file piccoli e mirati (come i memory store di Anthropic, ma 4 kB invece di 100 kB).
MEM_MAX_CHARS = 4000
SEARCH_POOL = 24          # candidati per ramo (testuale e vettoriale) prima della fusione
RRF_K = 60
# Peso della ricerca per parole rispetto ai vettori (1.0). Misurato con e5-large sulle 20 domande + sonda di nomi e numeri esatti:
# 0 -> 20/20 sulle domande ma sbaglia "1.650"; 0.03 -> 19/20 e trova i numeri; >=0.1 -> 18/20 (le parole comuni inquinano). Campione piccolo.
FTS_WEIGHT = float(os.environ.get("HLAB_FTS_WEIGHT", "0.03"))

# Sotto-agenti: su 2 vCPU niente alberi profondi o troppi lavori in parallelo.
MAX_SUBAGENT_DEPTH = os.environ.get("HLAB_MAX_DEPTH", "1")
MAX_CONCURRENT_SUBAGENTS = os.environ.get("HLAB_MAX_CONCURRENT", "3")
RUN_BUDGET_USD = float(os.environ.get("HLAB_RUN_BUDGET_USD", "0.50"))
# Con il router la CLI non conosce i nomi dei pool e stima il costo con il prezzo di un modello Claude, anche se i modelli gratuiti
# costano zero (visto alla prima prova: 0,05 $ stimati finiti in 28 secondi). Qui e' solo una guardia contro cicli infiniti;
# il costo vero lo limitano la quota gratuita e il tetto locale del router (e i crediti OpenRouter per i pool a pagamento).
ROUTER_RUN_BUDGET_USD = float(os.environ.get("HLAB_ROUTER_RUN_BUDGET_USD", "3.00"))

# Gettoni di capacita' per ogni chiamata di strumento (scopes.py): enforce = senza gettone valido lo strumento rifiuta; warn = esegue ma
# registra l'evento `cap_missing`; off = niente controlli. Verificato dal vivo il 2026-11-02 (COO -> Commerciale, 0 eventi cap_missing,
# strumenti eseguiti in modalita' enforce): il default e' enforce.
CAPS_MODE = os.environ.get("HLAB_CAPS_MODE", "enforce")

# Dati dei clienti verso il modello: di default mascherati finche' non decidi cosa puo' uscire.
CRM_REDACT = os.environ.get("HLAB_CRM_REDACT", "1") != "0"
