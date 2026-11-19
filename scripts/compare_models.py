"""Confronta modelli di embedding (e un reranker) sulle regole condivise, su TUTTE le 20 domande.
Solo vettori puri e in memoria: misura la qualita' del modello, non del motore. python scripts/compare_models.py <modello> [...]
Il nome 'rerank:<modello>' usa il reranker sui 6 migliori candidati del primo modello della lista."""
import resource
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_rules import HELD_OUT, TUNING  # noqa: E402,F401  (esegue anche la valutazione dello store: ignorare l'output)
from seed_shared import ENTRIES  # noqa: E402

from hlab import config  # noqa: E402
from hlab.memory import embed_text  # noqa: E402

QS = TUNING + HELD_OUT
PATHS = list(ENTRIES)
DOCS = [embed_text(p, ENTRIES[p][0]) for p in PATHS]


def score(order_fn, label, t_doc, t_q):
    t1 = t3 = 0
    for q, want in QS:
        top = [PATHS[i] for i in order_fn(q)[:3]]
        t1 += top[0] in want
        t3 += any(t in want for t in top)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"[{label:55s}] top-1 {t1:2d}/20  top-3 {t3:2d}/20 | docs {t_doc:5.1f}s | query {t_q * 1000:5.0f} ms | RSS {rss:.0f} MB", flush=True)


def run_embed(name):
    from fastembed import TextEmbedding
    m = TextEmbedding(model_name=name, cache_dir=str(config.MODELS_DIR))
    e5 = "e5" in name
    t0 = time.time()
    d = np.array(list(m.passage_embed(DOCS) if e5 else m.embed(DOCS)))
    t_doc = time.time() - t0
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    cache = {}
    t0 = time.time()
    for q, _ in QS:
        v = next(iter(m.query_embed(q) if e5 else m.embed([q])))
        cache[q] = v / np.linalg.norm(v)
    t_q = (time.time() - t0) / len(QS)
    score(lambda q: list(np.argsort(-(d @ cache[q]))), name.split("/")[-1], t_doc, t_q)
    return lambda q: list(np.argsort(-(d @ cache[q])))


def run_rerank(name, first):
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    r = TextCrossEncoder(model_name=name, cache_dir=str(config.MODELS_DIR))
    t0 = time.time()
    order = {}
    for q, _ in QS:
        cand = first(q)[:6]
        s = list(r.rerank(q, [DOCS[i] for i in cand]))
        order[q] = [cand[i] for i in np.argsort(-np.array(s))] + first(q)[6:]
    score(lambda q: order[q], "rerank:" + name.split("/")[-1], 0, (time.time() - t0) / len(QS))


if __name__ == "__main__":
    first = None
    for n in sys.argv[1:]:
        try:
            if n.startswith("rerank:"):
                run_rerank(n[7:], first)
            else:
                fn = run_embed(n)
                first = first or fn
        except Exception as e:
            print(f"[{n}] non eseguibile: {type(e).__name__}: {str(e)[:120]}")
