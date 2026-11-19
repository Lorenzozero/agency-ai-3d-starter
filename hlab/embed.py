"""Embedding su CPU: un singolo modello condiviso, caricato al primo uso."""
from __future__ import annotations

import hashlib
import os
import re
import threading

import numpy as np

from . import config

_TOK = re.compile(r"\w+", re.UNICODE)


def _norm(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (m / n).astype(np.float32)


class FakeEmbedder:
    """Hashing di parole: deterministico e senza modello. Solo per i test."""
    name = "fake-hash"
    dim = 256

    def embed_docs(self, texts: list[str]) -> np.ndarray:
        return self.embed(texts)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed([text])[0]

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in _TOK.findall(t.lower()):
                h = int(hashlib.md5(w.encode()).hexdigest(), 16)
                out[i, h % self.dim] += 1.0 if (h >> 8) & 1 else -1.0
        return _norm(out)


class FastEmbedder:
    def __init__(self, model: str = config.EMBED_MODEL, threads: int = config.EMBED_THREADS):
        self.name, self._threads = model, threads
        self._m = None
        self._dim = 0
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._m is None:
                from fastembed import TextEmbedding
                config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
                kw = {} if "potion" in self.name else {"threads": self._threads}   # i modelli statici ignorano i thread
                self._m = TextEmbedding(model_name=self.name, cache_dir=str(config.MODELS_DIR), **kw)
                self._dim = len(next(iter(self._m.passage_embed(["x"]))))
        return self._m

    @property
    def dim(self) -> int:
        self._load()
        return self._dim

    def embed_docs(self, texts: list[str]) -> np.ndarray:
        """Documenti e domande si codificano in modo diverso (i modelli e5 vogliono i prefissi passage/query: ci pensa fastembed)."""
        m = self._load()
        return _norm(np.array(list(m.passage_embed(texts)), dtype=np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        m = self._load()
        return _norm(np.array(list(m.query_embed(text)), dtype=np.float32))[0]

    def embed(self, texts: list[str]) -> np.ndarray:
        return self.embed_docs(texts)


_EMB = None


def get_embedder():
    global _EMB
    if _EMB is None:
        _EMB = FakeEmbedder() if os.environ.get("HLAB_FAKE_EMBED") == "1" else FastEmbedder()
    return _EMB


def set_embedder(e) -> None:
    global _EMB
    _EMB = e
