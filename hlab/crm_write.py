"""Scrittura sul CRM tramite l'API dell'admin (non sul database). Solo proposte GIA' ACCETTATE da Lorenzo (`hlab accept <id>`), una alla volta.

Accesso: un utente con ruolo `staff` creato apposta (non admin: l'API gli nega le cancellazioni), credenziale `crm-staff` nella cassaforte
(servizio `other`, valore `email:password`). L'API e' quella dell'app (cookie + CSRF double-submit), quindi ogni modifica finisce nel suo registro
di audit. Indirizzo in quotas.yaml: `crm: {base_url: https://.../api}`.
"""
from __future__ import annotations

import httpx

from . import powers
from .events import Events
from .vault import Vault

CRED = "crm-staff"
ADMIN_CRED = "crm-admin"            # solo per attivare/disattivare utenti: lo usa l'esecutore, l'agente non lo vede mai
FIELD_MAP = {"is_active": "isActive", "payment_deadline": "paymentDeadline", "is_online": "isOnline", "target_url": "targetUrl", "staging_url": "stagingUrl", "due_date": "dueDate"}
BOOLEAN = {"is_active", "is_online", "done"}
UNSUPPORTED = {"task"}                       # i task stanno sotto /projects/:id/tasks: serve anche l'id del progetto, non e' nella proposta


def route(entity: str, entity_id: str) -> str:
    if entity not in ("project", "lead", "user"):
        raise ValueError(f"'{entity}' non ancora applicabile via API (solo project, lead e user)")
    return f"/{'projects' if entity == 'project' else 'leads' if entity == 'lead' else 'users'}/{entity_id}"


def body(field: str, value: str) -> dict:
    if field in BOOLEAN:
        low = value.strip().lower()
        if low not in ("true", "false", "1", "0", "si", "no"):
            raise ValueError(f"{field}: valore booleano atteso")
        return {FIELD_MAP.get(field, field): low in ("true", "1", "si")}
    return {FIELD_MAP.get(field, field): (value.strip() or None)}


def _user_path(c: httpx.Client, ident: str) -> str:
    """Risolve un'email nell'id utente e rifiuta di toccare un admin: un agente non spegne mai un amministratore."""
    r = c.get("/users")
    r.raise_for_status()
    j = r.json()
    rows = j if isinstance(j, list) else (j.get("items") or j.get("users") or [])
    u = next((x for x in rows if x.get("id") == ident or (x.get("email") or "").lower() == ident.lower()), None)
    if not u:
        raise ValueError(f"utente '{ident}' non trovato")
    if u.get("role") == "admin":
        raise ValueError("non si disattiva un amministratore da qui: va fatto a mano")
    return f"/users/{u['id']}"


def apply(ev: Events, pid: int, vault: Vault | None = None, transport: httpx.BaseTransport | None = None, base_url: str | None = None) -> dict:
    p = ev.get_proposal(pid)
    if not p:
        raise ValueError("proposta non trovata")
    if p["status"] != "accepted":
        raise ValueError(f"la proposta e' '{p['status']}': si applica solo dopo `hlab accept {pid}`")
    base = (base_url or powers.load_quotas().get("crm", {}).get("base_url") or "").rstrip("/")
    if not base:
        raise ValueError("manca l'indirizzo dell'API: in quotas.yaml `crm: {base_url: https://.../api}`")
    if p["entity"] == "user" and p["field"] != "is_active":
        raise ValueError("sugli utenti si puo' solo attivare o disattivare (is_active)")
    path, payload = route(p["entity"], p["entity_id"]), body(p["field"], p["new_value"])
    email, _, password = (vault or Vault()).use(ADMIN_CRED if p["entity"] == "user" else CRED, f"crm proposta #{pid}", who="executor").partition(":")
    with httpx.Client(base_url=base, transport=transport, timeout=30, follow_redirects=False) as c:
        c.get("/auth/me")                                                            # il server imposta il cookie XSRF-TOKEN
        xsrf = {"x-xsrf-token": c.cookies.get("XSRF-TOKEN") or ""}
        c.post("/auth/login", json={"email": email, "password": password}, headers=xsrf).raise_for_status()
        xsrf = {"x-xsrf-token": c.cookies.get("XSRF-TOKEN") or xsrf["x-xsrf-token"]}
        try:
            if p["entity"] == "user":
                path = _user_path(c, p["entity_id"])
            r = c.patch(path, json=payload, headers=xsrf)
            r.raise_for_status()
        finally:
            c.post("/auth/logout", headers=xsrf)                                     # la sessione vive solo il tempo della modifica
    ev.mark_proposal(pid, "applied")
    return {"applicata": pid, "campo": p["field"], "api": f"PATCH {path}", "stato_http": r.status_code}
