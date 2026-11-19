"""Strumenti dell'agente per i poteri (GitHub, Netlify, credenziali). L'agente PROPONE e legge i metadati; non esegue e non vede segreti."""
from __future__ import annotations

from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

from .. import powers
from ..events import Events
from ..registry import AgentSpec
from ..vault import Vault
from .base import S, err, js, ok, schema

NAMES = ["quota_status", "propose_github_pr", "propose_netlify_preview", "propose_site_launch", "my_changes", "cred_list", "cred_request_add", "cred_request_delete"]
RISK = {"quota_status": "read", "my_changes": "read", "cred_list": "read",
        "propose_github_pr": "reversible", "propose_netlify_preview": "reversible", "propose_site_launch": "reversible", "cred_request_add": "reversible", "cred_request_delete": "reversible"}
FILES = {"type": "object", "description": "file della modifica: {percorso relativo: contenuto testuale}", "additionalProperties": {"type": "string"}}


def server_name(spec: AgentSpec) -> str:
    return "pow_" + spec.name.replace("-", "_")


def make_tools(spec: AgentSpec, events: Events | None = None, vault: Vault | None = None) -> list:
    ev = events or Events()
    ro = ToolAnnotations(readOnlyHint=True)
    V = lambda: vault or Vault()          # noqa: E731 - cassaforte aperta solo se serve

    def auto(cid: int, kind: str) -> str | None:
        """Livello 1 senza attesa se Lorenzo l'ha concesso a questo agente; un errore lascia la modifica in attesa."""
        if not powers.auto_allowed(spec.name, kind):
            return None
        try:
            res = powers.Powers(ev, V()).approve(cid)
        except Exception as e:                                    # quota esaurita, rete, permessi: resta proposta, la decide lui
            return f"modifica #{cid} proposta; esecuzione automatica non riuscita ({type(e).__name__}): resta in attesa di Lorenzo."
        return f"modifica #{cid} eseguita (livello 1, automatico): {res.get('pr') or res.get('anteprima')}. Merge e pubblicazione restano a Lorenzo."

    @tool("quota_status", "Quote gratuite di GitHub e Netlify usate oggi e nel mese rispetto alle soglie di prudenza. Controllale prima di proporre.",
          schema({}), annotations=ro)
    async def quota_status(a):
        return js(powers.Powers(ev, V()).quota_report())

    @tool("propose_github_pr",
          "Proponi una pull request su un repo GitHub aziendale (owner/repo). NON la apre: la approva Lorenzo, che vede i file. Niente .env, chiavi o workflow CI.",
          schema({"repo": S, "title": S, "files": FILES, "body": S}, ["repo", "title", "files"]))
    async def propose_github_pr(a):
        try:
            cid = powers.propose(ev, spec.name, "github_pr", a["repo"], a["title"], {"files": a["files"], "body": a.get("body", "")})
        except ValueError as e:
            return err(str(e))
        done = auto(cid, "github_pr")
        return ok(done or f"modifica #{cid} proposta (PR su {a['repo']}): in attesa dell'approvazione di Lorenzo. Non e' stato toccato nulla.")

    @tool("propose_netlify_preview",
          "Proponi un'ANTEPRIMA (draft deploy) su un sito Netlify aziendale. La approva Lorenzo; la pubblicazione e' un secondo passo, solo suo.",
          schema({"site": S, "title": S, "files": FILES}, ["site", "title", "files"]))
    async def propose_netlify_preview(a):
        try:
            cid = powers.propose(ev, spec.name, "netlify_preview", a["site"], a["title"], {"files": a["files"]})
        except ValueError as e:
            return err(str(e))
        done = auto(cid, "netlify_preview")
        return ok(done or f"modifica #{cid} proposta (anteprima su {a['site']}): in attesa dell'approvazione di Lorenzo.")

    @tool("propose_site_launch",
          "Proponi la PUBBLICAZIONE di un sito costruito dalla fabbrica (slug) su GitHub Pages; se e' una modifica di un sito gia' pubblicato aggiorna quello stesso sito. Il sito deve essere costruito e senza errori nel browser. "
          "NON pubblica: la approva Lorenzo, che vede il rapporto. Controlla prima quota_status.",
          schema({"slug": S, "title": S}, ["slug", "title"]))
    async def propose_site_launch(a):
        try:
            cid = powers.propose(ev, spec.name, "site_publish", a["slug"], a["title"], {})
        except ValueError as e:
            return err(str(e))
        return ok(f"pubblicazione #{cid} del sito {a['slug']} proposta: in attesa dell'approvazione di Lorenzo. Non e' stato pubblicato nulla.")

    @tool("my_changes", "Le tue modifiche proposte e il loro stato (proposed, previewed, published, rejected).", schema({}), annotations=ro)
    async def my_changes(a):
        return js([c for c in powers.listing(ev) if c["agent"] == spec.name])

    @tool("cred_list", "Elenco delle credenziali: SOLO metadati (nome, servizio, ambito, ultimo uso). Il segreto non e' leggibile da qui.", schema({}), annotations=ro)
    async def cred_list(a):
        return js([{"nome": c["name"], "servizio": c["service"], "ambito": c["scope"], "usi": c["uses"], "ultimo_uso": c["last_used"]} for c in V().list()])

    @tool("cred_request_add", "Chiedi di INSERIRE una credenziale (es. un token): descrivi servizio, ambito e perche'. Il valore lo inserisce Lorenzo con `hlab cred add`.",
          schema({"name": S, "service": S, "reason": S}, ["name", "service", "reason"]))
    async def cred_request_add(a):
        if len((a.get("reason") or "").strip()) < 10:
            return err("serve un motivo chiaro")
        pid = ev.add_proposal(spec.name, "credential", a["name"], "add", a["service"], a["reason"])
        return ok(f"richiesta #{pid}: Lorenzo inserira' il valore con `hlab cred add {a['name']} {a['service']}`. Tu non vedrai mai il segreto.")

    @tool("cred_request_delete", "Chiedi di CANCELLARE una credenziale (cancellazione logica, recuperabile 30 giorni). La decide Lorenzo.",
          schema({"name": S, "reason": S}, ["name", "reason"]))
    async def cred_request_delete(a):
        if len((a.get("reason") or "").strip()) < 10:
            return err("serve un motivo chiaro")
        pid = ev.add_proposal(spec.name, "credential", a["name"], "delete", "", a["reason"])
        return ok(f"richiesta #{pid} di cancellare '{a['name']}' in attesa di Lorenzo (`hlab proposals`, poi `hlab cred delete {a['name']}`).")

    return [quota_status, propose_github_pr, propose_netlify_preview, propose_site_launch, my_changes, cred_list, cred_request_add, cred_request_delete]


def build(spec: AgentSpec):
    from .. import scopes
    return create_sdk_mcp_server(name=server_name(spec), version="1.0.0", tools=scopes.harden(server_name(spec), make_tools(spec)))
