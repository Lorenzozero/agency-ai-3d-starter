"""CRM di Agency AI in SOLA LETTURA (Postgres su Neon).

Garanzie: transazione read-only lato server, solo SELECT/WITH, timeout, risultati limitati e dati di contatto
mascherati finche' non decidi cosa puo' uscire verso il modello (HLAB_CRM_REDACT). Nomi di tabelle e colonne
da apps/api/src/db/schema.ts (Drizzle, snake_case).
"""
from __future__ import annotations

import os
import re
from typing import Protocol

from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

from .. import config
from .base import I, S, err, js, schema

CRM_NAMES = ["list_clients", "get_client", "list_leads", "money_summary"]                       # gruppo crm_read
PROJECT_NAMES = ["list_projects", "list_deadlines", "project_overview", "stale_projects"]       # gruppo projects_read
NAMES = CRM_NAMES + PROJECT_NAMES
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_SAFE_SQL = re.compile(r"^\s*(select|with)\b", re.I)


class Db(Protocol):
    def query(self, sql: str, params: tuple = ()) -> list[dict]: ...


class PgDb:
    """Connessione pigra, read-only, con timeout. La password arriva solo dall'ambiente."""

    def __init__(self):
        self._c = None

    def _connect(self):
        import psycopg2
        dsn = os.environ.get("HLAB_CRM_DSN")
        if dsn:
            c = psycopg2.connect(dsn, connect_timeout=8)
        else:
            c = psycopg2.connect(host=os.environ.get("CHF_PG_HOST", "ep-snowy-star-b7nt23aw-pooler.c-13.us-east-1.aws.neon.tech"),
                                 dbname=os.environ.get("CHF_PG_DB", "agency_ai"),
                                 user=os.environ.get("CHF_PG_USER", "neondb_owner"),
                                 password=os.environ["CHF_PG_PASSWORD"], sslmode="require", connect_timeout=8)
        c.set_session(readonly=True, autocommit=True)
        return c

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        if not _SAFE_SQL.match(sql):
            raise PermissionError("solo SELECT")
        if self._c is None or self._c.closed:
            self._c = self._connect()
        with self._c.cursor() as cur:
            cur.execute("SET statement_timeout = 8000")
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---- mascheramento --------------------------------------------------------------------
def mask_email(v):
    return v if not v or "@" not in v else v[0] + "***@" + v.split("@", 1)[1]


def mask_tail(v):
    return v if not v else "***" + v[-3:]


def redact(row: dict) -> dict:
    if not config.CRM_REDACT:
        return row
    r = dict(row)
    for k in ("contact_email", "email"):
        if k in r:
            r[k] = mask_email(r[k])
    for k in ("contact_phone", "phone", "vat_number"):
        if k in r:
            r[k] = mask_tail(r[k])
    return r


def _lim(v, default, hi):
    try:
        return max(1, min(int(v), hi))
    except (TypeError, ValueError):
        return default


def build(db: Db | None = None, group: str = "crm_read"):
    """Un server per gruppo: `crm` (clienti, lead, incassi) e `projects` (progetti, scadenze). Chi non ha il gruppo non lo vede."""
    names, server = (CRM_NAMES, "crm") if group == "crm_read" else (PROJECT_NAMES, "projects")
    from .. import scopes
    return create_sdk_mcp_server(name=server, version="1.0.0",
                                 tools=scopes.harden(server, [t for t in make_tools(db or PgDb()) if t.name in names]))


def make_tools(db: Db) -> list:
    ro = ToolAnnotations(readOnlyHint=True)

    @tool("list_clients", "Elenca i clienti (nome, contatto, ultimo aggiornamento). Filtro opzionale sul nome.",
          schema({"q": S, "limit": I}), annotations=ro)
    async def list_clients(a):
        lim = _lim(a.get("limit"), 20, 50)
        q = (a.get("q") or "").strip()
        sql = "select id, name, contact_name, updated_at from clients" + (" where name ilike %s" if q else "") + " order by name limit %s"
        return js([redact(r) for r in db.query(sql, ((f"%{q}%", lim) if q else (lim,)))])

    @tool("get_client", "Scheda completa di un cliente (per id o nome): anagrafica, progetti, incassi, task aperti.",
          schema({"client": S}, ["client"]), annotations=ro)
    async def get_client(a):
        key = (a.get("client") or "").strip()
        if not key:
            return err("indica id o nome del cliente")
        if UUID_RE.match(key):
            found = db.query("select * from clients where id = %s", (key,))
        else:
            found = db.query("select * from clients where name ilike %s order by name limit 5", (f"%{key}%",))
        if not found:
            return err(f"nessun cliente per '{key}'")
        if len(found) > 1:
            return js({"ambiguo": True, "candidati": [{"id": r["id"], "name": r["name"]} for r in found]})
        c = found[0]
        keep = ("id", "name", "vat_number", "address", "opening_hours", "contact_name", "contact_email", "contact_phone",
                "old_website", "notes", "created_at", "updated_at")
        projects = db.query(
            "select id, name, status::text as status, type::text as type, currency, is_online, target_url, deadline, "
            "payment_deadline, paid_amount_cents, archived_at from projects where client_id = %s order by updated_at desc limit 20",
            (c["id"],))
        pay = db.query("select status::text as status, currency, count(*) as n, sum(amount_cents) as cents from payments "
                       "where client_id = %s group by 1, 2", (c["id"],))
        open_tasks = db.query("select count(*) as n from tasks t join projects p on p.id = t.project_id "
                              "where p.client_id = %s and not t.done", (c["id"],))
        return js({"cliente": redact({k: c.get(k) for k in keep}), "progetti": projects, "incassi": pay,
                   "task_aperti": open_tasks[0]["n"] if open_tasks else 0})

    @tool("list_projects", "Elenca i progetti (stato, scadenza, online). Filtri opzionali: status, nome cliente.",
          schema({"status": S, "client": S, "limit": I}), annotations=ro)
    async def list_projects(a):
        lim = _lim(a.get("limit"), 30, 60)
        where, params = ["p.archived_at is null"], []
        if a.get("status"):
            where.append("p.status::text = %s")
            params.append(a["status"])
        if a.get("client"):
            where.append("c.name ilike %s")
            params.append(f"%{a['client']}%")
        sql = ("select p.id, p.name, c.name as client, p.status::text as status, p.type::text as type, p.is_online, p.deadline, "
               "p.payment_deadline, p.currency, p.target_url from projects p join clients c on c.id = p.client_id "
               "where " + " and ".join(where) + " order by p.updated_at desc limit %s")
        return js(db.query(sql, tuple(params) + (lim,)))

    @tool("list_deadlines", "Scadenze di progetti e task entro N giorni (default 30), con il cliente.",
          schema({"days": I}), annotations=ro)
    async def list_deadlines(a):
        days = _lim(a.get("days"), 30, 365)
        sql = ("select 'progetto' as kind, p.name as title, c.name as client, p.deadline as due, p.status::text as status "
               "from projects p join clients c on c.id = p.client_id "
               "where p.archived_at is null and p.deadline is not null and p.deadline <= current_date + %s "
               "union all "
               "select 'task', t.title, c.name, t.due_date, p.name from tasks t join projects p on p.id = t.project_id "
               "join clients c on c.id = p.client_id "
               "where not t.done and t.due_date is not null and t.due_date <= current_date + %s "
               "order by 4 limit 80")
        return js(db.query(sql, (days, days)))

    @tool("list_leads", "Elenca i lead (stato, punteggio, categoria). Filtri opzionali: status, min_score.",
          schema({"status": S, "min_score": I, "limit": I}), annotations=ro)
    async def list_leads(a):
        lim = _lim(a.get("limit"), 20, 50)
        where, params = ["true"], []
        if a.get("status"):
            where.append("status::text = %s")
            params.append(a["status"])
        if a.get("min_score") is not None:
            where.append("score >= %s")
            params.append(int(a["min_score"]))
        sql = ("select id, name, company, category, service::text as service, status::text as status, score, website, price_cents, "
               "contacted_at, created_at from leads where " + " and ".join(where) + " order by coalesce(score, 0) desc, created_at desc limit %s")
        return js(db.query(sql, tuple(params) + (lim,)))

    @tool("money_summary", "Incassi raggruppati per stato e valuta, su tutto o su un progetto (id).",
          schema({"project_id": S}), annotations=ro)
    async def money_summary(a):
        pid = (a.get("project_id") or "").strip()
        if pid and not UUID_RE.match(pid):
            return err("project_id deve essere un uuid")
        sql = ("select status::text as status, currency, count(*) as n, sum(amount_cents) as cents from payments"
               + (" where project_id = %s" if pid else "") + " group by 1, 2 order by 1")
        return js(db.query(sql, (pid,) if pid else ()))

    @tool("project_overview", "Quadro completo di UN progetto (id): stato, scadenze, preventivo, task con avanzamento, documenti, incassi.",
          schema({"project_id": S}, ["project_id"]), annotations=ro)
    async def project_overview(a):
        pid = (a.get("project_id") or "").strip()
        if not UUID_RE.match(pid):
            return err("project_id deve essere un uuid (lo trovi con list_projects)")
        p = db.query("select p.id, p.name, c.name as client, p.status::text as status, p.type::text as type, p.currency, p.is_online, "
                     "p.indexing_status::text as indexing_status, p.target_url, p.staging_url, p.repository_url, p.start_date, p.deadline, "
                     "p.payment_deadline, p.paid_amount_cents, p.quote_items, p.marketing_enabled, p.archived_at, p.updated_at "
                     "from projects p join clients c on c.id = p.client_id where p.id = %s", (pid,))
        if not p:
            return err("progetto non trovato")
        tasks = db.query("select title, phase::text as phase, done, due_date, completed_at from tasks where project_id = %s "
                         "order by position limit 60", (pid,))
        docs = db.query("select type, title, status::text as status, version, updated_at from documents where project_id = %s "
                        "order by position limit 30", (pid,))
        pay = db.query("select status::text as status, currency, count(*) as n, sum(amount_cents) as cents from payments "
                       "where project_id = %s group by 1, 2", (pid,))
        done = sum(1 for t in tasks if t.get("done"))
        return js({"progetto": p[0], "task": {"totale": len(tasks), "fatti": done, "elenco": tasks},
                   "documenti": docs, "incassi": pay})

    @tool("stale_projects", "Progetti non aggiornati da N giorni (default 14), non archiviati: sono quelli a rischio di essere dimenticati.",
          schema({"days": I}), annotations=ro)
    async def stale_projects(a):
        days = _lim(a.get("days"), 14, 365)
        return js(db.query("select p.id, p.name, c.name as client, p.status::text as status, p.deadline, p.updated_at, "
                           "(current_date - p.updated_at::date) as days_idle from projects p join clients c on c.id = p.client_id "
                           "where p.archived_at is null and (current_date - p.updated_at::date) >= %s "
                           "order by p.updated_at limit 40", (days,)))

    return [list_clients, get_client, list_leads, money_summary, list_projects, list_deadlines, project_overview, stale_projects]
