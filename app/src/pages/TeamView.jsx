import React, { useState, useEffect, useMemo } from 'react'
import { useApp } from '../App.jsx'
import { db } from '../firebase.js'
import { collection, query, where, onSnapshot, doc, updateDoc, deleteDoc } from 'firebase/firestore'
import { todayKey } from '../utils/worklog.js'
import Icon from '../components/Icons.jsx'
import AgentModal, { AgentAvatar } from '../components/AgentModal.jsx'
import CATALOGO from '../../../roles/catalogo.json'

function fmtSeconds(s) {
  if (!s || s <= 0) return null
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  return [h, m, sec].map(n => String(n).padStart(2, '0')).join(':')
}

function fmtTokens(n) {
  if (!n || n <= 0) return '0'
  if (n >= 1000000) return `${(n / 1000000).toFixed(2)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1).replace(/\.0$/, '')}k`
  return String(n)
}

function calcMemberTokens(memberId, isAgent, item, tasks, allAgents) {
  let used = Number(item?.tokensUsed || item?.tokens || 0)
  const actions = Number(item?.actionsCount || 0)
  if (!used) {
    const memberTasks = tasks.filter(t => t.asignado === memberId)
    for (const t of memberTasks) {
      if (t.tokens || t.tokenCount) used += Number(t.tokens || t.tokenCount)
    }
  }
  const teamTotal = Number(item?.teamTotalTokens || 0)
    || (allAgents || []).reduce((acc, a) => acc + Number(a?.tokensUsed || 0), 0)
    || 1
  const maxAgent = Math.max(1, ...(allAgents || []).map(a => Number(a?.tokensUsed || 0)))
  const sharePct = used > 0 ? Math.max(1, Math.round((used / teamTotal) * 100)) : 0
  const barPct = used > 0 ? Math.min(100, Math.max(6, Math.round(Math.sqrt(used / maxAgent) * 100))) : 0
  return { used, actions, teamTotal, sharePct, pct: barPct }
}

function LiveTimer({ timerStartedAt, totalTimeSeconds }) {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!timerStartedAt) { setElapsed(totalTimeSeconds || 0); return }
    const base = totalTimeSeconds || 0
    const startMs = timerStartedAt.toMillis ? timerStartedAt.toMillis() : timerStartedAt.seconds * 1000
    const tick = () => setElapsed(base + Math.floor((Date.now() - startMs) / 1000))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [timerStartedAt, totalTimeSeconds])

  const fmt = fmtSeconds(elapsed)
  if (!fmt) return null
  return (
    <span className="timer-display" style={{ fontSize: 12 }}>
      {timerStartedAt && <span className="timer-dot" />}
      {fmt}
    </span>
  )
}

export default function TeamView() {
  const { users, agents, tasks, isAdmin, isSuperAdmin, openEditTask, user, showToast } = useApp()
  const [filter, setFilter] = useState('all') // 'all', 'agents', 'users', 'active', 'reserve'
  const [editing, setEditing] = useState(null) // null | 'new' | agent
  const [detailItem, setDetailItem] = useState(null) // null | { type, data, stats, ... }

  // Worklog ore attive di oggi per collaboratori umani (Super admin)
  const [todayLogs, setTodayLogs] = useState({})
  useEffect(() => {
    if (!isSuperAdmin) return
    const q = query(collection(db, 'worklog'), where('date', '==', todayKey()))
    const unsub = onSnapshot(q, snap => {
      const logs = {}
      snap.docs.forEach(d => { const v = d.data(); logs[v.uid] = v })
      setTodayLogs(logs)
    })
    return unsub
  }, [isSuperAdmin])

  // Statistiche task per membro (umano o agente)
  const taskStats = useMemo(() => {
    const m = {}
    for (const t of tasks) {
      if (!t.asignado) continue
      const s = (m[t.asignado] ||= { pendientes: 0, en_progreso: 0, revision: 0, completadas: 0, activeTask: null })
      if (t.estado === 'completada') s.completadas++
      else if (t.estado === 'en_revision') s.revision++
      else if (t.estado === 'en_progreso') {
        s.en_progreso++
        if (!s.activeTask) s.activeTask = t
      } else s.pendientes++
    }
    return m
  }, [tasks])

  const toggleAgent = async (a) => {
    try {
      await updateDoc(doc(db, 'agents', a.id), { active: a.active === false })
      showToast(a.active === false ? `${a.name} è ora attivo` : `${a.name} è passato in riserva`)
    } catch { showToast('Impossibile aggiornare lo stato', 'error') }
  }

  const removeAgent = async (a) => {
    const open = (taskStats[a.id]?.pendientes || 0) + (taskStats[a.id]?.en_progreso || 0)
    if (!confirm(`Eliminare l'agente ${a.name}?${open ? ` Ha ${open} task aperti che rimarranno senza assegnatario.` : ''}`)) return
    try {
      await deleteDoc(doc(db, 'agents', a.id))
      showToast(`Agente ${a.name} eliminato`)
    } catch { showToast('Impossibile eliminare', 'error') }
  }

  // Lista unificata
  const totalCount = users.length + agents.length
  const activeAgentsCount = agents.filter(a => a.active !== false).length
  const reserveAgentsCount = agents.filter(a => a.active === false).length

  // Filtro
  const showUsers = filter === 'all' || filter === 'users'
  const showAgents = filter === 'all' || filter === 'agents' || filter === 'active' || filter === 'reserve'

  const filteredAgents = agents
    .slice()
    .sort((a, b) => (b.active ? 1 : 0) - (a.active ? 1 : 0) || (a.order ?? 0) - (b.order ?? 0))
    .filter(a => {
      if (filter === 'active') return a.active !== false
      if (filter === 'reserve') return a.active === false
      return true
    })

  const sortedUsers = [...users].sort((a, b) => {
    const aActive = tasks.some(t => t.asignado === a.uid && t.estado === 'en_progreso')
    const bActive = tasks.some(t => t.asignado === b.uid && t.estado === 'en_progreso')
    if (aActive && !bActive) return -1
    if (!aActive && bActive) return 1
    return (a.name || '').localeCompare(b.name || '')
  })

  return (
    <div>
      {/* Action Bar Unificata */}
      <div className="page-header" style={{ marginBottom: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div className="page-actions btn-row" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', justifyContent: 'center' }}>
          <button
            className={`btn btn-sm ${filter === 'all' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setFilter('all')}
          >
            Tutti ({totalCount})
          </button>
          <button
            className={`btn btn-sm ${filter === 'agents' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setFilter('agents')}
          >
            Agenti IA ({agents.length})
          </button>
          <button
            className={`btn btn-sm ${filter === 'users' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setFilter('users')}
          >
            Collaboratori ({users.length})
          </button>
        </div>
      </div>

      {/* Griglia Membri Unificata (3 per riga, compatta) */}
      <div className="team-grid">
        {/* Collaboratori Umani */}
        {showUsers && sortedUsers.map(member => {
          const s = taskStats[member.uid] || { pendientes: 0, en_progreso: 0, revision: 0, completadas: 0, activeTask: null }
          const isOnline = member.lastSeen &&
            (Date.now() - (member.lastSeen.toMillis?.() || member.lastSeen.seconds * 1000)) < 5 * 60 * 1000
          const totalH = todayLogs[member.uid]?.hours
            ? ((todayLogs[member.uid].onlineSeconds || 0) / 3600).toFixed(1)
            : null
          const tokens = calcMemberTokens(member.uid, false, member, tasks, agents)

          return (
            <div
              className="team-card"
              key={member.uid}
              onClick={() => setDetailItem({ type: 'user', data: member, stats: s, isOnline, todayHours: totalH, tokens })}
              title="Clicca per aprire i dettagli"
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <div style={{ position: 'relative', flexShrink: 0 }}>
                  {member.photo ? (
                    <img src={member.photo} alt="" style={{ width: 28, height: 28, borderRadius: '50%', objectFit: 'cover' }} />
                  ) : (
                    <div style={{ width: 28, height: 28, borderRadius: '50%', background: member.color || 'var(--purple)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11.5, fontWeight: 700, color: '#fff' }}>
                      {(member.name || '?')[0].toUpperCase()}
                    </div>
                  )}
                  <div
                    style={{
                      position: 'absolute', bottom: -1, right: -1,
                      width: 7.5, height: 7.5, borderRadius: '50%',
                      background: isOnline ? 'var(--success)' : 'var(--text-3)',
                      border: '1.5px solid var(--card)'
                    }}
                  />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ color: '#f0f0ff', fontWeight: 700, fontSize: 12, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {member.name}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--text-2)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {member.position || member.email}
                  </div>
                </div>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 5, fontSize: 10 }}>
                {s.activeTask ? (
                  <span style={{ color: '#a78bfa', background: 'var(--purple-light)', padding: '1px 5px', borderRadius: 4, fontWeight: 700 }}>
                    ⚡ In corso
                  </span>
                ) : (
                  <span style={{ color: 'var(--text-3)' }}>
                    {s.completadas > 0 ? `✅ ${s.completadas}` : (isOnline ? 'Online' : 'Offline')}
                  </span>
                )}
                <span style={{ color: 'var(--text-3)', fontSize: 9.5 }}>Dettagli ›</span>
              </div>

              {/* Barra progressiva consumo token reale */}
              <div style={{ marginTop: 5 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 9.5, marginBottom: 2 }}>
                  <span style={{ color: 'var(--text-3)' }}>Token</span>
                  <span style={{ fontFamily: 'Space Mono', color: '#a78bfa', fontWeight: 700 }}>
                    {fmtTokens(tokens.used)}
                  </span>
                </div>
                <div style={{ width: '100%', height: 3.5, background: 'rgba(255,255,255,0.08)', borderRadius: 99, overflow: 'hidden' }}>
                  <div style={{
                    width: `${tokens.pct}%`, height: '100%', borderRadius: 99,
                    background: 'linear-gradient(90deg, #7c3aed, #a78bfa)'
                  }} />
                </div>
              </div>
            </div>
          )
        })}

        {/* Agenti IA */}
        {showAgents && filteredAgents.map(a => {
          const s = taskStats[a.id] || { pendientes: 0, en_progreso: 0, revision: 0, completadas: 0, activeTask: null }
          const isOn = a.active !== false
          const tokens = calcMemberTokens(a.id, true, a, tasks, agents)

          return (
            <div
              className={`team-card ${!isOn ? 'is-off' : ''}`}
              key={a.id}
              style={{ opacity: isOn ? 1 : 0.65 }}
              onClick={() => setDetailItem({ type: 'agent', data: a, stats: s, isOn, tokens })}
              title="Clicca per aprire i dettagli"
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <div style={{ position: 'relative', flexShrink: 0 }}>
                  <AgentAvatar agent={a} size={28} />
                  <div
                    style={{
                      position: 'absolute', bottom: -1, right: -1,
                      width: 7.5, height: 7.5, borderRadius: '50%',
                      background: isOn ? 'var(--success)' : 'var(--text-3)',
                      border: '1.5px solid var(--card)'
                    }}
                  />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ color: '#f0f0ff', fontWeight: 700, fontSize: 12, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {a.name}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--text-2)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {a.role}
                  </div>
                </div>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 5, fontSize: 10 }}>
                {s.activeTask ? (
                  <span style={{ color: '#a78bfa', background: 'var(--purple-light)', padding: '1px 5px', borderRadius: 4, fontWeight: 700 }}>
                    ⚡ Al lavoro
                  </span>
                ) : (
                  <span style={{ color: 'var(--text-3)' }}>
                    {tokens.actions > 0 ? `⚡ ${tokens.actions} az.` : (s.completadas > 0 ? `✅ ${s.completadas}` : (isOn ? 'Pronto' : 'In riserva'))}
                  </span>
                )}
                <span style={{ color: 'var(--text-3)', fontSize: 9.5 }}>Dettagli ›</span>
              </div>

              {/* Barra progressiva consumo token reale */}
              <div style={{ marginTop: 5 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 9.5, marginBottom: 2 }}>
                  <span style={{ color: 'var(--text-3)' }}>Token</span>
                  <span style={{ fontFamily: 'Space Mono', color: tokens.sharePct > 40 ? '#f59e0b' : '#a78bfa', fontWeight: 700 }}>
                    {fmtTokens(tokens.used)}{tokens.sharePct > 0 ? ` (${tokens.sharePct}%)` : ''}
                  </span>
                </div>
                <div style={{ width: '100%', height: 3.5, background: 'rgba(255,255,255,0.08)', borderRadius: 99, overflow: 'hidden' }}>
                  <div style={{
                    width: `${tokens.pct}%`, height: '100%', borderRadius: 99,
                    background: tokens.sharePct > 40 ? 'linear-gradient(90deg, #a78bfa, #f59e0b)' : 'linear-gradient(90deg, #7c3aed, #a78bfa)'
                  }} />
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Modale Dettagli Membro (Descrizione, Capacità, Task, Azioni) */}
      {detailItem && (
        <MemberDetailModal
          detail={detailItem}
          onClose={() => setDetailItem(null)}
          onEditAgent={(ag) => { setDetailItem(null); setEditing(ag) }}
          onToggleAgent={(ag) => toggleAgent(ag)}
          onRemoveAgent={(ag) => removeAgent(ag)}
          onOpenTask={openEditTask}
          isAdmin={isAdmin}
          isSuperAdmin={isSuperAdmin}
        />
      )}

      {/* Modale Modifica / Creazione Agente */}
      {editing && (
        <AgentModal
          agent={editing === 'new' ? null : editing}
          nextOrder={agents.length}
          onClose={() => setEditing(null)}
          showToast={showToast}
          userEmail={user.email}
        />
      )}
    </div>
  )
}

function MemberDetailModal({
  detail,
  onClose,
  onEditAgent,
  onToggleAgent,
  onRemoveAgent,
  onOpenTask,
  isAdmin,
  isSuperAdmin
}) {
  if (!detail) return null
  const { type, data: item, stats, isOnline, todayHours, isOn, tokens } = detail
  const isAgent = type === 'agent'
  const cat = isAgent && item.rol ? CATALOGO?.roles?.[item.rol] : null
  const herramientas = (Array.isArray(item.toolsList) && item.toolsList.length > 0) ? item.toolsList : (cat?.herramientas || [])
  const conectores = (Array.isArray(item.skillsList) && item.skillsList.length > 0) ? item.skillsList : (cat?.conectores || [])

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 1200,
        background: 'rgba(0, 0, 0, 0.75)', backdropFilter: 'blur(6px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16
      }}
      onClick={onClose}
    >
      <div
        className="card"
        onClick={e => e.stopPropagation()}
        style={{
          maxWidth: 480, width: '100%', maxHeight: '90vh', overflowY: 'auto',
          padding: 20, borderRadius: 16, background: 'var(--card)', border: '1px solid var(--purple-mid)',
          boxShadow: '0 12px 40px rgba(0,0,0,0.6)'
        }}
      >
        {/* Header con Avatar, Nome e Chiudi */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, marginBottom: 16 }}>
          <div style={{ flexShrink: 0 }}>
            {isAgent ? (
              <AgentAvatar agent={item} size={52} />
            ) : item.photo ? (
              <img src={item.photo} alt="" style={{ width: 52, height: 52, borderRadius: '50%', objectFit: 'cover' }} />
            ) : (
              <div style={{
                width: 52, height: 52, borderRadius: '50%',
                background: item.color || 'var(--purple)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 20, fontWeight: 700, color: '#fff'
              }}>
                {(item.name || '?')[0].toUpperCase()}
              </div>
            )}
          </div>

          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
              <h3 style={{ margin: 0, fontSize: 17, fontWeight: 800, color: '#f0f0ff' }}>{item.name}</h3>
              <button className="icon-btn" onClick={onClose} title="Chiudi">
                <Icon name="x" size={15} />
              </button>
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-2)', marginTop: 2 }}>
              {isAgent ? item.role : (item.position || item.email)}
            </div>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 6, flexWrap: 'wrap' }}>
              <span style={{
                fontSize: 10.5, fontWeight: 700, padding: '2px 8px', borderRadius: 99,
                background: (isAgent ? isOn : isOnline) ? 'rgba(34,197,94,0.18)' : 'rgba(255,255,255,0.08)',
                color: (isAgent ? isOn : isOnline) ? '#22c55e' : 'var(--text-3)'
              }}>
                {isAgent ? (isOn ? '🟢 Attivo' : '⚪ In riserva') : (isOnline ? '🟢 Online' : '⚫ Offline')}
              </span>
              <span style={{ fontSize: 10.5, color: '#a78bfa', background: 'var(--purple-light)', padding: '2px 8px', borderRadius: 99, fontWeight: 600 }}>
                {isAgent ? 'Agente IA' : (item.role || 'Collaboratore')}
              </span>
            </div>
          </div>
        </div>

        {/* Descrizione / Bio */}
        {(item.bio || item.position) && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 4 }}>
              Descrizione
            </div>
            <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.5, color: '#f0f0ff', background: 'rgba(255,255,255,0.03)', padding: '10px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.08)' }}>
              {item.bio || item.position}
            </p>
          </div>
        )}

        {/* Missione (se Agente) */}
        {isAgent && item.mission && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 4 }}>
              Missione & Scopo
            </div>
            <p style={{ margin: 0, fontSize: 12, lineHeight: 1.5, color: '#d1d1f0', background: 'rgba(255,255,255,0.03)', padding: '10px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.08)' }}>
              {item.mission}
            </p>
          </div>
        )}

        {/* Capacità & Competenze / Strumenti */}
        {isAgent && (herramientas.length > 0 || conectores.length > 0) && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 6 }}>
              Capacità & Strumenti
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {herramientas.map(h => (
                <span key={h} style={{ fontSize: 11, fontWeight: 600, padding: '3px 8px', borderRadius: 6, background: 'rgba(71,0,224,0.18)', color: '#c4b5fd', border: '1px solid rgba(167,139,250,0.25)' }}>
                  ⚡ {h.replace(/_/g, ' ')}
                </span>
              ))}
              {conectores.map(c => (
                <span key={c} style={{ fontSize: 11, fontWeight: 600, padding: '3px 8px', borderRadius: 6, background: 'rgba(34,197,94,0.12)', color: '#86efac', border: '1px solid rgba(34,197,94,0.25)' }}>
                  🔗 {c}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Consumo Token Reale (events.db) */}
        {tokens && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>Consumo Token Reali</span>
              <span style={{ fontFamily: 'Space Mono', color: tokens.sharePct > 40 ? '#f59e0b' : '#a78bfa', fontSize: 11.5 }}>
                {tokens.used.toLocaleString('it-IT')} tok ({tokens.sharePct}% squadra)
              </span>
            </div>
            <div style={{ width: '100%', height: 7, background: 'rgba(255,255,255,0.08)', borderRadius: 99, overflow: 'hidden' }}>
              <div style={{
                width: `${tokens.pct}%`, height: '100%', borderRadius: 99,
                background: tokens.sharePct > 40 ? 'linear-gradient(90deg, #a78bfa, #f59e0b)' : 'linear-gradient(90deg, #7c3aed, #a78bfa)',
                boxShadow: '0 0 8px rgba(167,139,250,0.5)',
                transition: 'width 0.4s ease'
              }} />
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10.5, color: 'var(--text-3)', marginTop: 4 }}>
              <span>Esecuzioni / Tool: {tokens.actions || 0} azioni reali</span>
              <span>Totale squadra: {fmtTokens(tokens.teamTotal)} tok</span>
            </div>
          </div>
        )}

        {/* Statistiche Task */}
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 6 }}>
            Carico di Lavoro & Task
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, textAlign: 'center' }}>
            <div style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.09)', borderRadius: 8, padding: '7px 4px' }}>
              <div style={{ fontSize: 16, fontWeight: 800, color: 'var(--text-3)' }}>{stats?.pendientes || 0}</div>
              <div style={{ fontSize: 10.5, color: 'var(--text-2)' }}>Da fare</div>
            </div>
            <div style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.09)', borderRadius: 8, padding: '7px 4px' }}>
              <div style={{ fontSize: 16, fontWeight: 800, color: 'var(--purple)' }}>{stats?.en_progreso || 0}</div>
              <div style={{ fontSize: 10.5, color: 'var(--text-2)' }}>In corso</div>
            </div>
            <div style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.09)', borderRadius: 8, padding: '7px 4px' }}>
              <div style={{ fontSize: 16, fontWeight: 800, color: 'var(--success)' }}>{stats?.completadas || 0}</div>
              <div style={{ fontSize: 10.5, color: 'var(--text-2)' }}>Completati</div>
            </div>
          </div>
        </div>

        {/* Task attivo in corso */}
        {stats?.activeTask && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 6 }}>
              Task Attivo
            </div>
            <div
              className="team-task-row"
              style={{ background: 'var(--purple-light)', border: '1px solid var(--purple-mid)', borderRadius: 8, cursor: 'pointer', padding: '9px 12px' }}
              onClick={() => { onClose(); onOpenTask(stats.activeTask) }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--purple)' }}>⚡ AL LAVORO</div>
                <div className="task-name" style={{ color: '#f0f0ff', fontSize: 12.5, fontWeight: 600 }}>{stats.activeTask.tarea}</div>
              </div>
              <LiveTimer timerStartedAt={stats.activeTask.timerStartedAt} totalTimeSeconds={stats.activeTask.totalTimeSeconds} />
            </div>
          </div>
        )}

        {/* Ore attive oggi (Collaboratori) */}
        {!isAgent && todayHours && (
          <div style={{ background: 'var(--bg-2)', borderRadius: 8, padding: '8px 12px', marginBottom: 14, display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12 }}>
            <span style={{ color: 'var(--text-3)' }}>Tempo connesso oggi:</span>
            <span style={{ fontFamily: 'Space Mono', fontWeight: 700, color: 'var(--success)' }}>{todayHours}h</span>
          </div>
        )}

        {/* Azioni operative */}
        <div style={{ display: 'flex', gap: 6, borderTop: '1px solid var(--border)', paddingTop: 12, marginTop: 14, flexWrap: 'wrap' }}>
          {isAgent && isAdmin && (
            <>
              <button
                className="btn btn-secondary btn-sm"
                style={{ flex: 1, justifyContent: 'center', fontSize: 12 }}
                onClick={() => { onClose(); onEditAgent(item) }}
              >
                <Icon name="edit" size={12} /> Modifica
              </button>
              <button
                className="btn btn-ghost btn-sm"
                style={{ flex: 1, justifyContent: 'center', fontSize: 12 }}
                onClick={() => onToggleAgent(item)}
              >
                <Icon name={isOn ? 'pause' : 'play'} size={12} /> {isOn ? 'Metti in riserva' : 'Attiva'}
              </button>
              {isSuperAdmin && (
                <button
                  className="btn btn-ghost btn-sm"
                  style={{ color: 'var(--danger)', padding: '5px 8px' }}
                  title="Elimina agente"
                  onClick={() => { onClose(); onRemoveAgent(item) }}
                >
                  <Icon name="trash" size={13} />
                </button>
              )}
            </>
          )}
          <button className="btn btn-primary btn-sm" style={{ marginLeft: 'auto', padding: '5px 14px' }} onClick={onClose}>
            Chiudi
          </button>
        </div>
      </div>
    </div>
  )
}

function TaskCount({ label, value, color }) {
  return (
    <div style={{
      flex: 1, textAlign: 'center',
      background: 'var(--bg-2)',
      borderRadius: 6, padding: '5px 3px'
    }}>
      <div style={{ fontSize: 16, fontWeight: 700, fontFamily: 'Space Mono', color }}>{value}</div>
      <div style={{ fontSize: 9.5, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</div>
    </div>
  )
}
