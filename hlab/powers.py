"""Poteri con revisione: GitHub e Netlify aziendali, a LIVELLI, con controllo delle quote gratuite.

Livello 0  leggere: stato delle quote, elenco delle modifiche proposte. Libero.
Livello 1  proporre ed eseguire in ANTEPRIMA: l'agente prepara una modifica (`changes`); dopo la TUA approvazione l'esecutore apre una pull request
           su GitHub o una anteprima (draft deploy) su Netlify con la credenziale della cassaforte. Niente va in produzione.
Livello 2  pubblicare: merge della pull request / pubblicazione dell'anteprima. Richiede una SECONDA approvazione esplicita, dopo che hai visto
           l'anteprima (`approve-change <id> --publish`). Nessun agente puo' farlo da solo.
Gli agenti non vedono mai i segreti: l'esecutore li prende dalla cassaforte (vault.py). Prima di ogni azione si controllano le quote gratuite
(quotas.yaml + intestazioni in tempo reale di GitHub): sopra la soglia l'azione si rifiuta e te lo dice.
"""
from __future__ import annotations

import base64
import io
import json
import re
import time
import zipfile
from pathlib import Path

import httpx
import yaml

from . import config
from .events import Events
from .vault import Vault

LEVELS = {"propose": 1, "publish": 2}
DEFAULT_QUOTAS = {
    # SOGLIE DI PRUDENZA modificabili in quotas.yaml: vanno verificate sul piano reale dell'account (i piani cambiano).
    "github": {"api_remaining_min": 200, "prs_per_day": 15, "files_per_change": 60, "bytes_per_change": 5_000_000},
    "netlify": {"deploys_per_month": 100, "deploys_per_day": 10, "reserve_pct": 20, "bytes_per_change": 50_000_000,
                "credits_per_deploy": 15},      # piano a crediti: ogni deploy ne costa ~15 (stima di prudenza, non letta dall'API); il tetto vero viene da netlify_plan()
}
SAFE_PATH = re.compile(r"^(?!/)(?!.*\.\.)[\w./@ -]{1,200}$")
BLOCKED = re.compile(r"(^|/)(\.env[^/]*|.*\.pem|.*\.key|id_rsa.*|\.git/.*|\.github/workflows/.*)$")     # niente segreti, niente workflow CI da un agente


def _plan_cache() -> Path:
    return config.DATA_DIR / "netlify_plan.json"


def netlify_plan(vault: Vault | None = None, transport=None, max_age: float = 900, cred: str = "netlify-azienda") -> dict | None:
    """Piano e crediti letti da Netlify (sola lettura), con cache. Se la rete o la credenziale mancano ritorna l'ultimo valore noto, o None."""
    try:
        cached = json.loads(_plan_cache().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = None
    if cached and time.time() - cached["ts"] < max_age:
        return cached
    try:
        token = (vault or Vault()).use(cred, "piano e crediti (sola lettura)", who="monitor")
        with httpx.Client(base_url="https://api.netlify.com/api/v1", transport=transport, timeout=20, headers={"authorization": f"Bearer {token}"}) as c:
            r = c.get("/accounts")
            r.raise_for_status()
            acc = r.json()[0]
            bs = c.get(f"/{acc['slug']}/builds/status")
            minutes = bs.json().get("minutes", {}) if bs.status_code == 200 else {}
        caps = acc.get("capabilities", {})
        plan = {"ts": time.time(), "piano": acc.get("type_name"), "crediti_inclusi": caps.get("credits", {}).get("included"),
                "crediti_usati": caps.get("credits", {}).get("used"), "minuti_build": minutes.get("current"), "fine_periodo": minutes.get("period_end_date"),
                "blocca_al_limite": caps.get("block_builds_when_usage_exceeded", {}).get("included")}
        _plan_cache().parent.mkdir(parents=True, exist_ok=True)
        _plan_cache().write_text(json.dumps(plan), encoding="utf-8")
        return plan
    except Exception:
        return cached


def load_quotas(path: Path | None = None) -> dict:
    q = json.loads(json.dumps(DEFAULT_QUOTAS))
    p = path or config.LAB_HOME / "quotas.yaml"
    if p.exists():
        for k, v in (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).items():
            q.setdefault(k, {}).update(v or {})
    return q


# ---- coda delle modifiche ---------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS changes(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, agent TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL, title TEXT NOT NULL,
  payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed', result TEXT NOT NULL DEFAULT '{}', why TEXT NOT NULL DEFAULT '', decided_ts REAL);
"""


def init(ev: Events) -> None:
    with ev._conn() as c:
        c.executescript(SCHEMA)


def validate_files(files: dict, q: dict) -> list[str]:
    errs = []
    if not files or not isinstance(files, dict):
        return ["nessun file"]
    if len(files) > q["github"]["files_per_change"]:
        errs.append(f"troppi file ({len(files)} > {q['github']['files_per_change']})")
    if sum(len(str(v)) for v in files.values()) > q["github"]["bytes_per_change"]:
        errs.append("modifica troppo grande")
    for p in files:
        if not SAFE_PATH.match(p):
            errs.append(f"percorso non ammesso: {p}")
        elif BLOCKED.search(p):
            errs.append(f"file riservato (segreti o workflow CI): {p}")
    return errs


def auto_allowed(agent: str, kind: str, q: dict | None = None) -> bool:
    """Approvazione automatica SOLO di livello 1 (PR, anteprima), concessa per agente in quotas.yaml: `auto: {deployer: [github_pr, netlify_preview]}`.
    Merge, pubblicazione e cancellazioni non passano mai da qui."""
    return kind in ((q or load_quotas()).get("auto", {}).get(agent) or [])


def propose(ev: Events, agent: str, kind: str, target: str, title: str, payload: dict) -> int:
    """Registra una modifica PROPOSTA. kind: github_pr | netlify_preview. Non esegue nulla."""
    init(ev)
    q = load_quotas()
    if kind not in ("github_pr", "netlify_preview", "site_publish"):
        raise ValueError("tipo non ammesso: github_pr, netlify_preview o site_publish")
    if kind == "site_publish":                                      # un sito costruito dalla fabbrica: nessun file nel payload, si pubblica la cartella gia' provata
        from . import sitefactory
        sitefactory.check_slug(target)
        if len(title.strip()) < 5:
            raise ValueError("serve un titolo chiaro")
        rep = sitefactory.report(target)
        if rep is None:
            raise ValueError("sito non costruito: prima il costruttore (site_build)")
        if rep["verdetto"].startswith("bloccato"):
            raise ValueError("il controllo nel browser ha trovato errori: ricostruire prima di proporre la pubblicazione")
        with ev._conn() as c:
            return c.execute("INSERT INTO changes(ts,agent,kind,target,title,payload) VALUES(?,?,?,?,?,?)",
                             (time.time(), agent, kind, target, title.strip()[:120], json.dumps({"verdetto": rep["verdetto"], "sezioni": rep["sezioni"], "modifica": sitefactory.is_edit(target)}, ensure_ascii=False))).lastrowid
    if kind == "github_pr" and not re.fullmatch(r"[\w.-]+/[\w.-]+", target):
        raise ValueError("target github: owner/repo")
    if kind == "netlify_preview" and not re.fullmatch(r"[\w.-]{3,80}", target):
        raise ValueError("target netlify: id o nome del sito")
    errs = validate_files(payload.get("files"), q)
    if errs:
        raise ValueError("; ".join(errs))
    if len(title.strip()) < 5:
        raise ValueError("serve un titolo chiaro")
    with ev._conn() as c:
        return c.execute("INSERT INTO changes(ts,agent,kind,target,title,payload) VALUES(?,?,?,?,?,?)",
                         (time.time(), agent, kind, target, title.strip()[:120], json.dumps(payload, ensure_ascii=False))).lastrowid


def get(ev: Events, cid: int) -> dict | None:
    init(ev)
    with ev._conn() as c:
        r = c.execute("SELECT * FROM changes WHERE id=?", (cid,)).fetchone()
    if not r:
        return None
    return {**dict(r), "payload": json.loads(r["payload"]), "result": json.loads(r["result"])}


def listing(ev: Events, status: str | None = None) -> list[dict]:
    init(ev)
    with ev._conn() as c:
        rows = c.execute("SELECT id,ts,agent,kind,target,title,status,result FROM changes" + (" WHERE status=?" if status else "") + " ORDER BY id DESC LIMIT 30",
                         (status,) if status else ()).fetchall()
    return [{**dict(r), "result": json.loads(r["result"])} for r in rows]


def _set(ev: Events, cid: int, status: str, result: dict | None = None, why: str = "") -> None:
    with ev._conn() as c:
        c.execute("UPDATE changes SET status=?, result=COALESCE(?, result), why=CASE WHEN ?<>'' THEN ? ELSE why END, decided_ts=? WHERE id=?",
                  (status, json.dumps(result) if result is not None else None, why, why, time.time(), cid))


def reject(ev: Events, cid: int, why: str = "") -> bool:
    ch = get(ev, cid)
    if not ch or ch["status"] not in ("proposed", "previewed"):
        return False
    _set(ev, cid, "rejected", why=why)
    from .learning import Learning
    Learning(ev).record(ch["agent"], "change", cid, "rejected", why)            # il rifiuto alimenta il ciclo di apprendimento
    return True


# ---- esecutori --------------------------------------------------------------------------------
class QuotaError(Exception):
    pass


class Powers:
    def __init__(self, ev: Events | None = None, vault: Vault | None = None, transport=None, quotas: dict | None = None):
        self.ev, self.vault, self.q = ev or Events(), vault or Vault(), quotas or load_quotas()
        self.transport = transport
        init(self.ev)

    def _count(self, kind_prefix: str, since: float) -> int:
        with self.ev._conn() as c:
            return c.execute("SELECT count(*) FROM changes WHERE kind=? AND status IN ('previewed','published') AND decided_ts>?", (kind_prefix, since)).fetchone()[0]

    def quota_report(self) -> dict:
        day, month = time.time() - 86400, time.time() - 30 * 86400
        g, n = self.q["github"], self.q["netlify"]
        plan = netlify_plan(self.vault, self.transport, max_age=10 ** 9) if _plan_cache().exists() else None      # solo la copia in cache: niente rete qui
        real = int(plan["crediti_inclusi"] // n["credits_per_deploy"]) if plan and plan.get("crediti_inclusi") else None
        return {"github": {"pr_oggi": self._count("github_pr", day), "pr_max_giorno": g["prs_per_day"], "soglia_api": g["api_remaining_min"]},
                "netlify": {"anteprime_oggi": self._count("netlify_preview", day), "max_giorno": n["deploys_per_day"],
                            "anteprime_30gg": self._count("netlify_preview", month), "max_mese": min(n["deploys_per_month"], real) if real else n["deploys_per_month"],
                            "riserva_pct": n["reserve_pct"], "piano": {k: plan[k] for k in ("piano", "crediti_inclusi", "crediti_usati", "minuti_build", "fine_periodo")} if plan else None}}

    def _check_quota(self, kind: str, github_remaining: int | None = None) -> None:
        rep = self.quota_report()
        if kind == "github_pr":
            g = rep["github"]
            if g["pr_oggi"] >= g["pr_max_giorno"]:
                raise QuotaError(f"quota giornaliera di PR raggiunta ({g['pr_oggi']}/{g['pr_max_giorno']})")
            if github_remaining is not None and github_remaining < g["soglia_api"]:
                raise QuotaError(f"limite API GitHub quasi finito ({github_remaining} richieste restanti)")
        else:
            n = rep["netlify"]
            if n["anteprime_oggi"] >= n["max_giorno"]:
                raise QuotaError(f"anteprime Netlify di oggi al limite ({n['anteprime_oggi']}/{n['max_giorno']})")
            if n["anteprime_30gg"] >= n["max_mese"] * (100 - n["riserva_pct"]) / 100:
                raise QuotaError(f"quota mensile Netlify oltre la soglia di prudenza ({n['anteprime_30gg']}/{n['max_mese']}, riserva {n['riserva_pct']}%)")

    def _check_credits(self) -> None:
        """Con i crediti veri letti da Netlify: niente anteprima se il consumo del mese + un deploy supera il budget (crediti inclusi meno la riserva)."""
        n, plan = self.q["netlify"], netlify_plan(self.vault, self.transport, max_age=300)
        if not plan or not plan.get("crediti_inclusi"):
            return
        budget = plan["crediti_inclusi"] * (100 - n["reserve_pct"]) / 100
        if (plan.get("crediti_usati") or 0) + n["credits_per_deploy"] > budget:
            raise QuotaError(f"crediti Netlify quasi finiti: usati {plan.get('crediti_usati')}/{plan['crediti_inclusi']} (budget {budget:.0f}, riserva {n['reserve_pct']}%)")

    # ---- GitHub: livello 1 (PR) e 2 (merge) ----
    def _gh(self, token: str) -> httpx.Client:
        return httpx.Client(base_url="https://api.github.com", transport=self.transport, timeout=60,
                            headers={"authorization": f"Bearer {token}", "accept": "application/vnd.github+json", "x-github-api-version": "2022-11-28"})

    def github_pr(self, cid: int, cred: str = "github-azienda") -> dict:
        ch = get(self.ev, cid)
        if ch["kind"] != "github_pr" or ch["status"] != "proposed":
            raise ValueError("la modifica non e' una PR in attesa di approvazione")
        token = self.vault.use(cred, f"PR change #{cid}")
        owner_repo, files = ch["target"], ch["payload"]["files"]
        self._check_quota("github_pr")                                           # prima i conteggi locali: nessuna chiamata di rete se gia' al limite
        with self._gh(token) as g:
            rl = g.get("/rate_limit")
            self._check_quota("github_pr", rl.json()["resources"]["core"]["remaining"] if rl.status_code == 200 else None)
            repo = g.get(f"/repos/{owner_repo}")
            repo.raise_for_status()
            base = repo.json()["default_branch"]
            sha = g.get(f"/repos/{owner_repo}/git/ref/heads/{base}").json()["object"]["sha"]
            branch = f"hermes/change-{cid}"
            r = g.post(f"/repos/{owner_repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})
            if r.status_code not in (201, 422):                                 # 422: il ramo esiste gia' (ripresa dopo un errore)
                r.raise_for_status()
            for path, content in files.items():
                cur = g.get(f"/repos/{owner_repo}/contents/{path}", params={"ref": branch})
                body = {"message": f"{ch['title']} ({path})", "content": base64.b64encode(str(content).encode()).decode(), "branch": branch}
                if cur.status_code == 200:
                    body["sha"] = cur.json()["sha"]
                g.put(f"/repos/{owner_repo}/contents/{path}", json=body).raise_for_status()
            pr = g.post(f"/repos/{owner_repo}/pulls", json={"title": ch["title"], "head": branch, "base": base,
                                                              "body": ch["payload"].get("body", "") + f"\n\nProposta dell'agente `{ch['agent']}` (change #{cid}), approvata da Lorenzo per l'anteprima."})
            pr.raise_for_status()
        res = {"pr": pr.json()["html_url"], "numero": pr.json()["number"], "ramo": branch, "base": base}
        _set(self.ev, cid, "previewed", res)
        return res

    def github_merge(self, cid: int, cred: str = "github-azienda") -> dict:
        ch = get(self.ev, cid)
        if ch["kind"] != "github_pr" or ch["status"] != "previewed":
            raise ValueError("il merge richiede una PR gia' aperta e rivista (stato 'previewed')")
        token = self.vault.use(cred, f"merge change #{cid}")
        with self._gh(token) as g:
            r = g.put(f"/repos/{ch['target']}/pulls/{ch['result']['numero']}/merge", json={"merge_method": "squash", "commit_title": ch["title"]})
            r.raise_for_status()
        res = {**ch["result"], "merge": r.json().get("sha")}
        _set(self.ev, cid, "published", res)
        return res

    # ---- Netlify: livello 1 (anteprima draft) e 2 (pubblicazione) ----
    def _nf(self, token: str) -> httpx.Client:
        return httpx.Client(base_url="https://api.netlify.com/api/v1", transport=self.transport, timeout=120, headers={"authorization": f"Bearer {token}"})

    @staticmethod
    def _site_id(n: httpx.Client, target: str) -> str:
        """L'API vuole l'id del sito: un nome (es. 'vetreria-alfa') si risolve cercandolo tra i siti dell'account."""
        if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", target):
            return target
        r = n.get("/sites", params={"filter": "all", "per_page": 100})
        r.raise_for_status()
        for s in r.json():
            if target in (s.get("name"), s.get("id")) or (s.get("url") or "").endswith(f"//{target}.netlify.app"):
                return s["id"]
        raise ValueError(f"sito Netlify '{target}' non trovato tra quelli visibili al token")

    def netlify_preview(self, cid: int, cred: str = "netlify-azienda") -> dict:
        ch = get(self.ev, cid)
        if ch["kind"] != "netlify_preview" or ch["status"] != "proposed":
            raise ValueError("la modifica non e' un'anteprima Netlify in attesa di approvazione")
        self._check_quota("netlify_preview")
        token = self.vault.use(cred, f"netlify preview #{cid}")
        self._check_credits()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for path, content in ch["payload"]["files"].items():
                z.writestr(path, str(content))
        if buf.tell() > self.q["netlify"]["bytes_per_change"]:
            raise QuotaError("archivio troppo grande per la soglia di prudenza")
        with self._nf(token) as n:
            r = n.post(f"/sites/{self._site_id(n, ch['target'])}/deploys", params={"draft": "true", "title": ch["title"][:80]}, content=buf.getvalue(),
                       headers={"content-type": "application/zip"})
            r.raise_for_status()
        d = r.json()
        res = {"deploy_id": d["id"], "anteprima": d.get("deploy_ssl_url") or d.get("deploy_url"), "sito": ch["target"]}
        _set(self.ev, cid, "previewed", res)
        return res

    def netlify_publish(self, cid: int, cred: str = "netlify-azienda") -> dict:
        ch = get(self.ev, cid)
        if ch["kind"] != "netlify_preview" or ch["status"] != "previewed":
            raise ValueError("la pubblicazione richiede un'anteprima gia' creata e rivista (stato 'previewed')")
        token = self.vault.use(cred, f"netlify publish #{cid}")
        with self._nf(token) as n:
            n.post(f"/deploys/{ch['result']['deploy_id']}/restore").raise_for_status()
        res = {**ch["result"], "pubblicato": True}
        _set(self.ev, cid, "published", res)
        return res

    # ---- sito costruito dalla fabbrica: una sola approvazione, poi va online (GitHub Pages) ----
    def site_publish(self, cid: int) -> dict:
        from . import sitefactory
        ch = get(self.ev, cid)
        if ch["kind"] != "site_publish" or ch["status"] != "proposed":
            raise ValueError("la modifica non e' una pubblicazione di sito in attesa di approvazione")
        self._check_quota("github_pr")
        res = sitefactory.publish(ch["target"])
        pages = res.get("pages", "sconosciuto")
        out = {"anteprima": res["url"], "repo": res.get("repo"), "pubblicato": pages == "built", "pages": pages}
        _set(self.ev, cid, "published", out)
        return out

    # ---- approvazione a livelli ----
    def approve(self, cid: int, publish: bool = False) -> dict:
        ch = get(self.ev, cid)
        if not ch:
            raise ValueError("modifica non trovata")
        if ch["kind"] == "site_publish":
            return self.site_publish(cid)
        if publish:                                           # livello 2: solo dopo l'anteprima, con approvazione esplicita
            return self.github_merge(cid) if ch["kind"] == "github_pr" else self.netlify_publish(cid)
        return self.github_pr(cid) if ch["kind"] == "github_pr" else self.netlify_preview(cid)
