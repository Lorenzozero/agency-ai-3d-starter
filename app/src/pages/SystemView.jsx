import React, { useEffect, useMemo, useState } from 'react'
import { db } from '../firebase.js'
import { doc, onSnapshot } from 'firebase/firestore'
import Icon from '../components/Icons.jsx'

// Pagina Sistema: da cosa dipende il sistema (router, fornitori con piani e limiti, server, siti), con barre di avanzamento reali,
// e la cronologia di esecuzioni e token. I dati arrivano dai documenti config/system e config/systemHistory, aggiornati dal backend ogni 2 minuti.
const COLORS = { ok: '#22c55e', warn: '#f59e0b', down: '#ef4444', off: '#64748b', info: '#38bdf8' }
const LABELS = { ok: 'A posto', warn: 'Attenzione', down: 'Non funziona', off: 'Spento', info: 'Da verificare' }

const num = (n) => (n === null || n === undefined ? '—' : Number(n).toLocaleString('it-IT'))
const ago = (sec) => {
  if (!sec) return '—'
  const d = Math.max(0, Math.round(Date.now() / 1000 - sec))
  if (d < 90) return `${d} s fa`
  if (d < 5400) return `${Math.round(d / 60)} min fa`
  if (d < 172800) return `${Math.round(d / 3600)} ore fa`
  return `${Math.round(d / 86400)} giorni fa`
}

function Bar({ pct, status }) {
  const p = pct === null || pct === undefined ? null : Math.max(0, Math.min(100, pct))
  return (
    <div className="sys-bar" title={p === null ? 'nessun limite misurabile' : `${p}%`}>
      <div className="sys-bar-fill" style={{ width: `${p === null ? 0 : p}%`, background: COLORS[status] || COLORS.ok }} />
    </div>
  )
}

function Row({ it, depth = 0, open, setOpen }) {
  const kids = it.children || []
  const isOpen = open[it.id]
  const quota = it.limit ? `${num(it.used)} / ${num(it.limit)} ${it.unit || ''}` : (it.used !== null && it.used !== undefined ? `${num(it.used)} ${it.unit || ''}` : '')
  return (
    <>
      <div className="sys-row" style={{ paddingLeft: 14 + depth * 22 }}>
        <span className="sys-dot" style={{ background: COLORS[it.status] || COLORS.info }} title={LABELS[it.status]} />
        <div className="sys-main">
          <div className="sys-name">
            {kids.length > 0 && (
              <button className="sys-toggle" onClick={() => setOpen({ ...open, [it.id]: !isOpen })} aria-label="Mostra dettagli">
                <Icon name={isOpen ? 'chevronDown' : 'chevronR'} size={14} />
              </button>
            )}
            <strong>{it.name}</strong>
            {it.plan ? <span className="sys-plan">{it.plan}</span> : null}
            <span className={`sys-tag ${it.measured ? 'sys-tag-m' : 'sys-tag-d'}`} title={it.measured ? 'Letto adesso dal fornitore o dal registro del router' : `Limite dichiarato dal fornitore, verificato il ${it.verified}`}>
              {it.measured ? 'misurato' : 'documentato'}
            </span>
          </div>
          {it.detail ? <div className="sys-detail">{it.detail}</div> : null}
          {(it.models || []).length > 0 && isOpen && (
            <div className="sys-models">
              {it.models.map((m) => (
                <span key={m.model} className="sys-model" title={`${m.ok} riusciti, ${m.fail} errori oggi`}>
                  {m.model.split('/').pop()} <em>{m.ok}/{m.ok + m.fail}</em>
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="sys-quota">
          <Bar pct={it.pct} status={it.status} />
          <div className="sys-quota-text">
            {quota}{it.pct !== null && it.pct !== undefined ? ` · ${it.pct}%` : ''}
            {it.resets ? <span className="sys-resets"> · si azzera: {it.resets}</span> : null}
          </div>
        </div>
      </div>
      {isOpen && kids.map((k) => <Row key={k.id} it={k} depth={depth + 1} open={open} setOpen={setOpen} />)}
    </>
  )
}

function History({ h }) {
  if (!h) return null
  const days = h.days || []
  const max = Math.max(1, ...days.map((d) => d.n))
  return (
    <div className="sys-history">
      <div className="card sys-card">
        <div className="section-title">Richieste ai modelli e token (ultimi 7 giorni)</div>
        {days.length === 0 ? <p>Nessuna richiesta registrata.</p> : days.map((d) => (
          <div key={d.day} className="sys-day">
            <span className="sys-day-label">{d.day.slice(5)}</span>
            <div className="sys-bar sys-bar-lg"><div className="sys-bar-fill" style={{ width: `${(d.n / max) * 100}%`, background: d.fail > d.ok ? COLORS.warn : '#7c5cff' }} /></div>
            <span className="sys-day-text">{num(d.n)} richieste · {num(d.ok)} ok · {num(d.fail)} errori · ~{num(d.tokens)} token · {d.usd ? `${d.usd.toFixed(3)} $` : '0 $'}</span>
          </div>
        ))}
        <p className="sys-note">I token sono una stima dal registro del router (solo le richieste riuscite). Costo a pagamento: deve restare 0.</p>
      </div>

      <div className="card sys-card">
        <div className="section-title">Esecuzioni recenti (lavori)</div>
        {(h.jobs || []).length === 0 ? <p>Nessun lavoro.</p> : (h.jobs || []).map((j) => (
          <div key={j.id} className="sys-line">
            <span className="sys-dot" style={{ background: j.status === 'done' ? COLORS.ok : j.status === 'failed' || j.status === 'dead' ? COLORS.down : COLORS.warn }} />
            <span>#{j.id} {j.kind}</span><em>{j.status}{j.attempts > 1 ? ` · ${j.attempts} tentativi` : ''}</em><span className="sys-when">{ago(j.ts)}</span>
          </div>
        ))}
      </div>

      <div className="card sys-card">
        <div className="section-title">Attività degli agenti e della sentinella</div>
        {(h.events || []).length === 0 ? <p>Nessun evento.</p> : (h.events || []).slice(0, 25).map((e, i) => (
          <div key={i} className="sys-line">
            <span className="sys-dot" style={{ background: e.ok === false ? COLORS.down : e.kind === 'sentinel' ? '#7c5cff' : COLORS.off }} />
            <span><b>{e.agent || e.kind}</b> {e.tool} {e.detail}</span><span className="sys-when">{ago(e.ts)}</span>
          </div>
        ))}
      </div>

      {(h.lean || []).length > 0 && (
        <div className="card sys-card">
          <div className="section-title">Token per percorso (assistente a regole)</div>
          {h.lean.map((l, i) => <div key={i} className="sys-line"><span>{l.day} · {l.path}</span><em>{num(l.n)} richieste</em><span className="sys-when">{num(l.tokens)} token</span></div>)}
        </div>
      )}
    </div>
  )
}

export default function SystemView() {
  const [sys, setSys] = useState(null)
  const [hist, setHist] = useState(null)
  const [open, setOpen] = useState({ router: true })
  const [onlyProblems, setOnlyProblems] = useState(false)
  const [, tick] = useState(0)

  useEffect(() => {
    const u1 = onSnapshot(doc(db, 'config', 'system'), (s) => setSys(s.exists() ? s.data() : null))
    const u2 = onSnapshot(doc(db, 'config', 'systemHistory'), (s) => setHist(s.exists() ? s.data() : null))
    const t = setInterval(() => tick((n) => n + 1), 15000)
    return () => { u1(); u2(); clearInterval(t) }
  }, [])

  const groups = useMemo(() => {
    if (!sys) return []
    const bad = (i) => i.status === 'warn' || i.status === 'down' || (i.children || []).some((c) => c.status === 'warn' || c.status === 'down')
    return (sys.groups || []).map((g) => ({ ...g, items: onlyProblems ? g.items.filter(bad) : g.items })).filter((g) => g.items.length > 0)
  }, [sys, onlyProblems])

  if (!sys) return <div className="card sys-card"><p>Sto leggendo lo stato del sistema… (il primo aggiornamento arriva entro un paio di minuti dall'avvio).</p></div>
  const c = sys.counts || {}
  const total = (c.ok || 0) + (c.warn || 0) + (c.down || 0)
  const okShare = total ? Math.round(((c.ok || 0) / total) * 100) : 0
  const stale = Date.now() / 1000 - (sys.updated || 0) > 600

  return (
    <div>
      <div className="page-header">
        <div>
          <span className="page-eyebrow">Amministrazione</span>
          <h1 className="page-title">Sistema</h1>
          <p className="page-subtitle">Da cosa dipende il sistema: router, fornitori con piani e limiti, server, siti · aggiornato {ago(sys.updated)}{stale ? ' ⚠ dati vecchi: il backend non aggiorna' : ''}</p>
        </div>
        <button className={`btn ${onlyProblems ? 'btn-primary' : ''}`} onClick={() => setOnlyProblems(!onlyProblems)}>{onlyProblems ? 'Mostra tutto' : 'Solo problemi'}</button>
      </div>

      <div className="card sys-card">
        <div className="sys-summary">
          {['ok', 'warn', 'down', 'off', 'info'].map((s) => (
            <div key={s} className="sys-count"><span className="sys-dot" style={{ background: COLORS[s] }} /><b>{c[s] || 0}</b><span>{LABELS[s]}</span></div>
          ))}
        </div>
        <Bar pct={okShare} status={sys.overall === 'ok' ? 'ok' : sys.overall} />
        <div className="sys-quota-text">{okShare}% dei controlli attivi è a posto · «misurato» = letto adesso dal fornitore o dal router; «documentato» = limite dichiarato dal fornitore (può cambiare)</div>
      </div>

      {groups.map((g) => (
        <div key={g.id} className="card sys-card">
          <div className="section-title">{g.label}</div>
          {g.items.map((it) => <Row key={it.id} it={it} open={open} setOpen={setOpen} />)}
        </div>
      ))}

      <History h={hist} />
    </div>
  )
}
