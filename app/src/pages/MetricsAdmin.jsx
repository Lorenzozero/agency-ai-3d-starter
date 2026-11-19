import React, { useMemo } from 'react'
import { useApp } from '../App.jsx'

function fmtHours(seconds) {
  if (!seconds) return '0h'
  const h = (seconds / 3600).toFixed(1)
  return `${h}h`
}

function BarRow({ label, value, max, color = 'var(--purple)', suffix = '' }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0
  return (
    <div className="bar-row">
      <span className="bar-label" title={label}
        style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 120 }}>
        {label}
      </span>
      <div className="bar-track">
        <div className="bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="bar-value">{value}{suffix}</span>
    </div>
  )
}

export default function MetricsAdmin() {
  const { tasks, users } = useApp()

  const weekAgo = Date.now() - 7 * 24 * 3600 * 1000
  const monthAgo = Date.now() - 30 * 24 * 3600 * 1000

  const stats = useMemo(() => {
    const byStatus = {
      pendiente: 0, en_progreso: 0, en_revision: 0, completada: 0
    }
    const byPriority = { urgente: 0, alta: 0, media: 0, baja: 0 }
    const byClient = {}
    const byMember = {}
    const memberTime = {}

    tasks.forEach(t => {
      byStatus[t.estado] = (byStatus[t.estado] || 0) + 1
      byPriority[t.prioridad] = (byPriority[t.prioridad] || 0) + 1
      if (t.cliente) byClient[t.cliente] = (byClient[t.cliente] || 0) + 1
      const uid = t.asignado
      if (uid) {
        byMember[uid] = (byMember[uid] || 0) + 1
        if (t.totalTimeSeconds) {
          memberTime[uid] = (memberTime[uid] || 0) + t.totalTimeSeconds
        }
      }
    })

    const completedWeek = tasks.filter(t => {
      if (t.estado !== 'completada') return false
      const ms = t.fechaEntrega?.toMillis?.() || 0
      return ms > weekAgo
    }).length

    const completedMonth = tasks.filter(t => {
      if (t.estado !== 'completada') return false
      const ms = t.fechaEntrega?.toMillis?.() || 0
      return ms > monthAgo
    }).length

    const withTime = tasks.filter(t => t.totalTimeSeconds > 0)
    const avgTime = withTime.length > 0
      ? withTime.reduce((a, t) => a + t.totalTimeSeconds, 0) / withTime.length
      : 0

    return { byStatus, byPriority, byClient, byMember, memberTime, completedWeek, completedMonth, avgTime }
  }, [tasks])

  const getUserById = (uid) => users.find(u => u.uid === uid)

  const maxClient = Math.max(...Object.values(stats.byClient), 1)
  const maxMember = Math.max(...Object.values(stats.byMember), 1)
  const maxTime = Math.max(...Object.values(stats.memberTime), 1)

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Metriche</h1>
          <p className="page-subtitle">Rendimento della squadra — visibile solo all'amministratore</p>
        </div>
      </div>

      {/* Top stats */}
      <div className="stats-grid" style={{ marginBottom: 28 }}>
        <div className="stat-card">
          <span className="stat-icon">📅</span>
          <span className="stat-value" style={{ color: 'var(--success)' }}>{stats.completedWeek}</span>
          <span className="stat-label">Completati (settimana)</span>
        </div>
        <div className="stat-card">
          <span className="stat-icon">📆</span>
          <span className="stat-value">{stats.completedMonth}</span>
          <span className="stat-label">Completati (mese)</span>
        </div>
        <div className="stat-card">
          <span className="stat-icon">⏱</span>
          <span className="stat-value">{fmtHours(stats.avgTime)}</span>
          <span className="stat-label">Media/task</span>
        </div>
        <div className="stat-card">
          <span className="stat-icon">📋</span>
          <span className="stat-value">{tasks.length}</span>
          <span className="stat-label">Task totali</span>
        </div>
      </div>

      <div className="metrics-grid">
        {/* By Status */}
        <div className="card">
          <p className="section-title">Per stato</p>
          <div className="bar-chart">
            {[
              { id: 'pendiente', label: 'Da fare', color: 'var(--text-3)' },
              { id: 'en_progreso', label: 'In corso', color: 'var(--purple)' },
              { id: 'en_revision', label: 'In revisione', color: 'var(--warning)' },
              { id: 'completada', label: 'Completato', color: 'var(--success)' },
            ].map(s => (
              <BarRow
                key={s.id}
                label={s.label}
                value={stats.byStatus[s.id] || 0}
                max={tasks.length}
                color={s.color}
              />
            ))}
          </div>
        </div>

        {/* By Priority */}
        <div className="card">
          <p className="section-title">Per priorità</p>
          <div className="bar-chart">
            {[
              { id: 'urgente', label: 'Urgente', color: 'var(--danger)' },
              { id: 'alta', label: 'Alta', color: 'var(--warning)' },
              { id: 'media', label: 'Media', color: 'var(--info)' },
              { id: 'baja', label: 'Bassa', color: 'var(--success)' },
            ].map(p => (
              <BarRow
                key={p.id}
                label={p.label}
                value={stats.byPriority[p.id] || 0}
                max={tasks.length}
                color={p.color}
              />
            ))}
          </div>
        </div>

        {/* Carico per membro */}
        <div className="card">
          <p className="section-title">Carico per membro</p>
          <div className="bar-chart">
            {Object.entries(stats.byMember)
              .sort((a, b) => b[1] - a[1])
              .map(([uid, count]) => {
                const u = getUserById(uid)
                return (
                  <BarRow
                    key={uid}
                    label={u?.name?.split(' ')[0] || uid}
                    value={count}
                    max={maxMember}
                  />
                )
              })
            }
          </div>
        </div>

        {/* Tiempo por miembro */}
        <div className="card">
          <p className="section-title">Tempo totale per membro</p>
          <div className="bar-chart">
            {Object.entries(stats.memberTime)
              .sort((a, b) => b[1] - a[1])
              .map(([uid, seconds]) => {
                const u = getUserById(uid)
                return (
                  <BarRow
                    key={uid}
                    label={u?.name?.split(' ')[0] || uid}
                    value={Math.round(seconds / 3600 * 10) / 10}
                    max={Math.round(maxTime / 3600 * 10) / 10}
                    color="var(--purple)"
                    suffix="h"
                  />
                )
              })
            }
          </div>
          {Object.keys(stats.memberTime).length === 0 && (
            <p style={{ fontSize: 13, color: 'var(--text-3)', textAlign: 'center', padding: '12px 0' }}>
              Nessun dato de tiempo aún
            </p>
          )}
        </div>

        {/* Por cliente */}
        <div className="card" style={{ gridColumn: 'span 2' }}>
          <p className="section-title">Task per cliente (primi 10)</p>
          <div className="bar-chart">
            {Object.entries(stats.byClient)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 10)
              .map(([client, count]) => (
                <BarRow
                  key={client}
                  label={client}
                  value={count}
                  max={maxClient}
                  color="var(--info)"
                />
              ))
            }
          </div>
        </div>
      </div>
    </div>
  )
}
