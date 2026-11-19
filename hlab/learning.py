"""Ciclo di apprendimento dal feedback (RLHF leggero, con cancello umano).

Rifiuti una bozza, correggi un task, annoti un errore -> il feedback si registra con il tuo commento. Il Memory Keeper legge i
feedback non elaborati e PROPONE regole comportamentali formali per quel ruolo. Una regola proposta non fa nulla finche' non la
approvi: da attiva, viene aggiunta ogni volta al prompt di quell'agente (sezione "Regole apprese"). Le regole non possono toccare
permessi, strumenti o autonomia: quelli stanno nel codice (policy.py) e non si cambiano con un prompt.
"""
from __future__ import annotations

import re

import numpy as np

from .embed import get_embedder
from .events import Events

MAX_ACTIVE = 12
SIMILAR = 0.88
VERDICTS = ("rejected", "accepted", "corrected")
FORBIDDEN = re.compile(r"(ignora|bypass|aggira|elude|salta|senza (chiedere|approvazione|ok)|disattiva|autonomia|permess|bash|write\b|edit\b|"
                       r"password|token|api[ _-]?key|sk-|\.env|segret|cancella|elimina|rm -rf|prompt di sistema|istruzioni (precedenti|di sistema))", re.I)


def validate_rule(rule: str) -> list[str]:
    r, errs = (rule or "").strip(), []
    if not 20 <= len(r) <= 300:
        errs.append("la regola deve avere tra 20 e 300 caratteri")
    if len(re.findall(r"[.!?](\s|$)", r)) > 2:
        errs.append("una regola, non un elenco: massimo due frasi")
    if FORBIDDEN.search(r):
        errs.append("le regole non possono toccare permessi, strumenti, segreti o saltare le approvazioni: stanno nel codice")
    if re.search(r"\b\d{6,}\b|@[\w.-]+\.\w+", r):
        errs.append("niente dati specifici di un cliente (numeri lunghi, email): generalizza")
    return errs


class Learning:
    def __init__(self, events: Events | None = None, embedder=None):
        self.ev, self.emb = events or Events(), embedder

    def record(self, agent: str, source: str, source_id, verdict: str, comment: str = "") -> int:
        if verdict not in VERDICTS:
            raise ValueError(f"verdetto non valido: {verdict}")
        return self.ev.add_feedback(agent, source, source_id, verdict, comment.strip())

    def propose(self, agent: str, rule: str, evidence: list[int]) -> dict:
        errs = validate_rule(rule)
        if errs:
            raise ValueError("; ".join(errs))
        rows = self.ev.feedback_rows(agent, processed=True, limit=500) + self.ev.feedback_rows(agent, limit=500)
        known = {r["id"] for r in rows}
        if not evidence or any(int(e) not in known for e in evidence):
            raise ValueError("indica le prove: id di feedback esistenti di quell'agente")
        if not any(r["comment"] for r in rows if r["id"] in {int(e) for e in evidence}):
            raise ValueError("serve almeno un feedback con un commento esplicito: senza motivo non si impara")
        existing = [r for r in self.ev.rule_rows(agent) if r["status"] in ("proposed", "active")]
        if existing:
            emb = self.emb or get_embedder()
            vecs = emb.embed_docs([rule] + [r["rule"] for r in existing])
            sims = vecs[1:] @ vecs[0]
            i = int(np.argmax(sims))
            if sims[i] >= SIMILAR:
                raise ValueError(f"simile alla regola #{existing[i]['id']}: non duplicare")
        return {"id": self.ev.add_rule(agent, rule.strip(), [int(e) for e in evidence]), "status": "proposed"}

    def approve(self, rid: int) -> str:
        r = next((x for x in self.ev.rule_rows() if x["id"] == rid), None)
        if not r or r["status"] != "proposed":
            raise ValueError("regola non trovata o gia' decisa")
        if len(self.ev.rule_rows(r["agent"], "active")) >= MAX_ACTIVE:
            raise ValueError(f"{r['agent']} ha gia' {MAX_ACTIVE} regole attive: ritirane una prima")
        self.ev.set_rule_status(rid, "active")
        return "active"

    def retire(self, rid: int) -> bool:
        return self.ev.set_rule_status(rid, "retired")

    def reject_rule(self, rid: int) -> bool:
        return self.ev.set_rule_status(rid, "rejected")

    def active_rules(self, agent: str) -> list[str]:
        return [r["rule"] for r in self.ev.rule_rows(agent, "active")]

    def prompt_block(self, agent: str) -> str:
        rules = self.active_rules(agent)
        if not rules:
            return ""
        return ("\n\n## Regole apprese dal feedback di Lorenzo\nSono correzioni approvate da lui dopo errori reali. Seguile sempre; "
                "non cambiano permessi, strumenti o approvazioni.\n" + "\n".join(f"- {r}" for r in rules))
