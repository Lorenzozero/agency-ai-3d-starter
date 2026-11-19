import React from 'react'
import { useApp } from '../App.jsx'
import Icon from './Icons.jsx'

const STATUS_LABELS = {
  pendiente:   { label: 'Da fare',    color: '#a78bfa', bg: 'rgba(167,139,250,0.15)' },
  en_progreso: { label: 'In corso',   color: '#7c3aed', bg: 'rgba(124,58,237,0.2)' },
  en_espera:   { label: 'In attesa',  color: '#38bdf8', bg: 'rgba(56,189,248,0.18)' },
  en_revision: { label: 'Correzioni', color: '#f59e0b', bg: 'rgba(245,158,11,0.18)' },
  completada:  { label: 'Completato', color: '#22c55e', bg: 'rgba(34,197,94,0.18)' },
}

export default function TaskForm() {
  const { users, agents, editingTask, closeTaskForm } = useApp()
  if (!editingTask) return null

  const st = STATUS_LABELS[editingTask.estado] || STATUS_LABELS.pendiente
  const assignee = users.find(u => u.uid === editingTask.asignado)
    || agents.find(a => a.id === editingTask.asignado)
  const link = editingTask.linkTerminada || editingTask.links || ''

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 1200,
        background: 'rgba(0, 0, 0, 0.75)', backdropFilter: 'blur(6px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16
      }}
      onClick={closeTaskForm}
    >
      <div
        className="card"
        onClick={e => e.stopPropagation()}
        style={{
          maxWidth: 500, width: '100%', maxHeight: '90vh', overflowY: 'auto',
          padding: 20, borderRadius: 16, background: 'var(--card)', border: '1px solid var(--purple-mid)',
          boxShadow: '0 12px 40px rgba(0,0,0,0.65)'
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, marginBottom: 14 }}>
          <div>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginBottom: 6 }}>
              <span style={{
                fontSize: 10.5, fontWeight: 700, padding: '2px 9px', borderRadius: 99,
                color: st.color, background: st.bg
              }}>
                {st.label}
              </span>
              {editingTask.prioridad && (
                <span className={`badge badge-${editingTask.prioridad}`} style={{ fontSize: 10.5, padding: '2px 8px', borderRadius: 99 }}>
                  {editingTask.prioridad}
                </span>
              )}
              {editingTask.cliente && (
                <span style={{ fontSize: 10.5, color: 'var(--text-2)', background: 'rgba(255,255,255,0.06)', padding: '2px 8px', borderRadius: 99 }}>
                  {editingTask.cliente}
                </span>
              )}
            </div>
            <h3 style={{ margin: 0, fontSize: 16.5, fontWeight: 800, color: '#f0f0ff', lineHeight: 1.35 }}>
              {editingTask.tarea}
            </h3>
          </div>
          <button className="icon-btn" onClick={closeTaskForm} title="Chiudi">
            <Icon name="x" size={15} />
          </button>
        </div>

        {/* Assegnatario */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 8, padding: '8px 12px', marginBottom: 14, fontSize: 12.5 }}>
          <span style={{ color: 'var(--text-3)' }}>Assegnato a:</span>
          <span style={{ fontWeight: 700, color: '#a78bfa' }}>{assignee?.name || editingTask.asignado || '—'}</span>
        </div>

        {/* Descrizione */}
        {editingTask.descripcion && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 4 }}>
              Dettagli
            </div>
            <div style={{
              margin: 0, fontSize: 12.5, lineHeight: 1.55, color: '#f0f0ff', whiteSpace: 'pre-wrap',
              background: 'rgba(255,255,255,0.03)', padding: '10px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.08)'
            }}>
              {editingTask.descripcion}
            </div>
          </div>
        )}

        {/* Risultato / Output Agente */}
        {editingTask.agentOutput && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#22c55e', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 4 }}>
              Risultato Agente
            </div>
            <div style={{
              margin: 0, fontSize: 12, lineHeight: 1.55, color: '#d1d1f0', whiteSpace: 'pre-wrap',
              background: 'rgba(34,197,94,0.06)', padding: '10px 12px', borderRadius: 8, border: '1px solid rgba(34,197,94,0.2)'
            }}>
              {editingTask.agentOutput}
            </div>
          </div>
        )}

        {/* Link eventuale */}
        {link && (
          <div style={{ marginBottom: 14 }}>
            <a
              href={link}
              target="_blank"
              rel="noreferrer"
              className="btn btn-secondary btn-sm"
              style={{ width: '100%', justifyContent: 'center', fontSize: 12 }}
            >
              🔗 Apri link / anteprima
            </a>
          </div>
        )}

        {/* Info Telegram Only */}
        <div style={{
          fontSize: 11.5, color: 'var(--text-2)', background: 'rgba(124,58,237,0.1)',
          border: '1px solid rgba(167,139,250,0.25)', borderRadius: 8, padding: '8px 12px',
          textAlign: 'center', marginBottom: 14
        }}>
          💬 Gli ordini agli agenti e le approvazioni si inviano esclusivamente via <strong>Telegram</strong>.
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <button className="btn btn-primary btn-sm" style={{ padding: '6px 18px' }} onClick={closeTaskForm}>
            Chiudi
          </button>
        </div>
      </div>
    </div>
  )
}
