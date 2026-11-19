import React, { useState, useEffect } from 'react'
import { useApp } from '../App.jsx'
import { db } from '../firebase.js'
import { doc, addDoc, updateDoc, collection, serverTimestamp } from 'firebase/firestore'
import { logWork } from '../utils/worklog.js'
import { sendTaskAssignedEmail } from '../utils/emailNotify.js'
import Icon from './Icons.jsx'

export default function TaskForm() {
  const { user, users, agents, clients, userProfile, editingTask, closeTaskForm, showToast, sendNotification, isAdmin, isSuperAdmin } = useApp()
  const CLIENTES = clients || []
  const isEdit = !!editingTask

  const [form, setForm] = useState({
    tarea: '',
    descripcion: '',
    asignado: user.uid,
    cliente: '',
    prioridad: 'media',
    estado: 'pendiente',
    fechaRequerimiento: '',
    fechaVencimiento: '',
    tiempoEstimadoMin: 0,
    links: '',
    linkTerminada: '',
    feedback: '',
    rating: 0,
  })
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)

  // Populate form for editing
  useEffect(() => {
    if (editingTask) {
      setForm({
        tarea: editingTask.tarea || '',
        descripcion: editingTask.descripcion || '',
        asignado: editingTask.asignado || user.uid,
        cliente: editingTask.cliente || '',
        prioridad: editingTask.prioridad || 'media',
        estado: editingTask.estado || 'pendiente',
        fechaRequerimiento: editingTask.fechaRequerimiento
          ? toDateInputValue(editingTask.fechaRequerimiento)
          : '',
        fechaVencimiento: editingTask.fechaVencimiento
          ? toDateInputValue(editingTask.fechaVencimiento)
          : '',
        tiempoEstimadoMin: editingTask.tiempoEstimadoMin || 0,
        links: editingTask.links || '',
        linkTerminada: editingTask.linkTerminada || '',
        feedback: editingTask.feedback || '',
        rating: editingTask.rating || 0,
      })
    }
  }, [editingTask])

  function toDateInputValue(ts) {
    if (!ts) return ''
    const d = ts.toDate ? ts.toDate() : new Date(ts)
    return d.toISOString().split('T')[0]
  }

  const setField = (k, v) => {
    setForm(f => ({ ...f, [k]: v }))
    setDirty(true)
  }

  const canChangeStatus = isAdmin || (editingTask?.asignado === user.uid)

  const computeTimerUpdates = (oldTask, newStatus) => {
    const updates = {}
    if (!oldTask) return updates

    // Starting timer
    if (newStatus === 'en_progreso' && oldTask.estado !== 'en_progreso') {
      updates.timerStartedAt = serverTimestamp()
      updates.timerPaused = false
      if (!oldTask.fechaInicio) updates.fechaInicio = serverTimestamp()
    }
    // Stopping timer
    if (oldTask.estado === 'en_progreso' && newStatus !== 'en_progreso') {
      if (oldTask.timerStartedAt && !oldTask.timerPaused) {
        const elapsed = Math.floor(
          (Date.now() - (oldTask.timerStartedAt.toMillis?.() || oldTask.timerStartedAt.seconds * 1000)) / 1000
        )
        updates.totalTimeSeconds = (oldTask.totalTimeSeconds || 0) + elapsed
        logWork(oldTask.asignado, elapsed)
      }
      updates.timerStartedAt = null
      updates.timerPaused = false
    }
    // Completing: marca de quién y cuándo
    if (newStatus === 'completada') {
      updates.fechaEntrega = serverTimestamp()
      updates.completedBy = userProfile?.name || user.email
      updates.completedByEmail = user.email
    }
    // Volver a pendiente: el tiempo trabajado se CONSERVA para seguir
    // acumulando al retomarla; solo se limpian las marcas de entrega
    if (newStatus === 'pendiente') {
      updates.fechaEntrega = null
      updates.timerStartedAt = null
      updates.timerPaused = false
      updates.completedBy = null
      updates.completedByEmail = null
    }
    return updates
  }

  const handleSave = async () => {
    if (!form.tarea.trim()) { showToast('Scrivi il nome del task', 'error'); return }
    setSaving(true)
    try {
      if (isEdit) {
        const timerUpdates = computeTimerUpdates(editingTask, form.estado)
        const updates = {
          ...form,
          fechaRequerimiento: form.fechaRequerimiento
            ? new Date(form.fechaRequerimiento + 'T12:00:00')
            : null,
          fechaVencimiento: form.fechaVencimiento
            ? new Date(form.fechaVencimiento + 'T23:59:00')
            : null,
          lastEditedBy: user.email,
          lastEditedAt: serverTimestamp(),
          ...timerUpdates,
        }
        await updateDoc(doc(db, 'tasks', editingTask.id), updates)
        showToast('Task aggiornato')

        // Notify on timer start
        if (form.estado === 'en_progreso' && editingTask.estado !== 'en_progreso') {
          const assignedUser = users.find(u => u.uid === form.asignado)
          if (form.asignado === user.uid) {
            showToast(`⏱️ Timer avviato per "${form.tarea}"!`, 'info')
            sendNotification('⏱️ Il tempo sta scorrendo!', `Task: "${form.tarea}"`)
          }
        }

        // Notify if newly assigned to someone else
        if (form.asignado !== editingTask.asignado) {
          const assignedUser = users.find(u => u.uid === form.asignado)
          if (assignedUser && form.asignado !== user.uid) {
            sendNotification('📋 Nuovo task assegnato', `"${form.tarea}" è stato assegnato a ${assignedUser.name}`)
            // Email al asignado (fire-and-forget)
            if (assignedUser.email) {
              sendTaskAssignedEmail({
                toEmail: assignedUser.email,
                toName: assignedUser.name,
                taskName: form.tarea,
                cliente: form.cliente,
                prioridad: form.prioridad,
                vencimiento: form.fechaVencimiento || '',
                assignedBy: userProfile?.name || user.email,
              })
            }
          }
        }
      } else {
        const newTask = {
          ...form,
          fechaRequerimiento: form.fechaRequerimiento
            ? new Date(form.fechaRequerimiento + 'T12:00:00')
            : null,
          fechaVencimiento: form.fechaVencimiento
            ? new Date(form.fechaVencimiento + 'T23:59:00')
            : null,
          fechaInicio: null,
          fechaEntrega: null,
          timerStartedAt: null,
          totalTimeSeconds: 0,
          createdAt: serverTimestamp(),
          createdBy: user.email,
          lastEditedBy: user.email,
          lastEditedAt: serverTimestamp(),
        }
        if (form.estado === 'en_progreso') {
          newTask.timerStartedAt = serverTimestamp()
          newTask.fechaInicio = serverTimestamp()
        }
        await addDoc(collection(db, 'tasks'), newTask)
        showToast('Task creato ✓')

        // Email si la tarea es para otra persona
        if (form.asignado !== user.uid) {
          const assignedUser = users.find(u => u.uid === form.asignado)
          if (assignedUser?.email) {
            sendTaskAssignedEmail({
              toEmail: assignedUser.email,
              toName: assignedUser.name,
              taskName: form.tarea,
              cliente: form.cliente,
              prioridad: form.prioridad,
              vencimiento: form.fechaVencimiento || '',
              assignedBy: userProfile?.name || user.email,
            })
          }
        }
      }
      closeTaskForm()
    } catch (e) {
      console.error(e)
      const detail = e?.code === 'permission-denied' ? 'sin permisos' : (e?.code || e?.message || '')
      showToast(`Errore nel salvataggio${detail ? ` (${detail})` : ''}`, 'error')
    }
    setSaving(false)
  }

  // Empleados: pueden ver tareas de otros pero no editarlas
  const readOnly = isEdit && !isAdmin && editingTask.asignado !== user.uid

  // Cerrar solo con Cancelar (o la X); si hay cambios sin guardar, confirmar
  const handleCancel = () => {
    if (dirty && !readOnly && !confirm('Hai modifiche non salvate. Scartarle e chiudere?')) return
    closeTaskForm()
  }

  // Tempo lavorato in totale (incluye la sesión en curso si el timer corre)
  const liveSeconds = (() => {
    if (!isEdit) return 0
    const base = editingTask.totalTimeSeconds || 0
    if (editingTask.estado === 'en_progreso' && editingTask.timerStartedAt && !editingTask.timerPaused) {
      const startMs = editingTask.timerStartedAt.toMillis?.() || editingTask.timerStartedAt.seconds * 1000
      return base + Math.floor((Date.now() - startMs) / 1000)
    }
    return base
  })()
  const fmtWorked = (s) => {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
    return h > 0 ? `${h}h ${m}m` : `${m}m`
  }

  return (
    // Clic afuera NO cierra el formulario: evita perder lo que se está escribiendo
    <div className="modal-overlay">
      <div className="modal">
        <div className="modal-header">
          <h2 className="modal-title">{isEdit ? 'Modifica task' : 'Nuovo task'}</h2>
          <button className="icon-btn" onClick={handleCancel} title="Chiudi">✕</button>
        </div>

        {/* Tempo lavorato in totale su questo task */}
        {isEdit && liveSeconds > 0 && (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10,
            padding: '10px 14px', borderRadius: 10, marginBottom: 16,
            background: 'var(--purple-light)', border: '1px solid var(--purple-mid)'
          }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>Tempo lavorato in totale</span>
            <span style={{ fontFamily: 'Space Mono', fontWeight: 700, color: 'var(--purple)' }}>
              {fmtWorked(liveSeconds)}
              {editingTask.tiempoEstimadoMin > 0 && (
                <span style={{ color: 'var(--text-3)', fontWeight: 400 }}> / {fmtWorked(editingTask.tiempoEstimadoMin * 60)} estimado</span>
              )}
            </span>
          </div>
        )}

        {isEdit && <AgentResult task={editingTask} showToast={showToast} />}

        {readOnly ? (
          <ReadOnlyTask task={editingTask} users={users} />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {/* Tarea */}
            <div className="form-group">
              <label className="form-label">Nome del task *</label>
              <input
                className="form-input"
                value={form.tarea}
                onChange={e => setField('tarea', e.target.value)}
                placeholder="Cosa c'è da fare?"
                autoFocus
              />
            </div>

            {/* Link del lavoro finito */}
            <div className="form-group">
              <label className="form-label">Link del lavoro finito</label>
              <input
                className="form-input"
                value={form.linkTerminada}
                onChange={e => setField('linkTerminada', e.target.value)}
                placeholder="https://... (consegna finale)"
              />
            </div>

            {/* Link alle risorse */}
            <div className="form-group">
              <label className="form-label">Link alle risorse</label>
              <input
                className="form-input"
                value={form.links}
                onChange={e => setField('links', e.target.value)}
                placeholder="https://... (materiali, riferimenti)"
              />
            </div>

            {/* Descrizione */}
            <div className="form-group">
              <label className="form-label">Descrizione</label>
              <textarea
                className="form-textarea"
                value={form.descripcion}
                onChange={e => setField('descripcion', e.target.value)}
                placeholder="Dettagli, contesto, istruzioni..."
                rows={3}
              />
            </div>

            <div className="form-row">
              {/* Asignado */}
              <div className="form-group">
                <label className="form-label">Assegnato a</label>
                <select
                  className="form-select"
                  value={form.asignado}
                  onChange={e => setField('asignado', e.target.value)}
                  disabled={!isAdmin}
                >
                  <optgroup label="Squadra">
                    {users.filter(u => u.role !== 'pending').map(u => (
                      <option key={u.uid} value={u.uid}>{u.name}</option>
                    ))}
                  </optgroup>
                  {agents.some(a => a.active || a.id === form.asignado) && (
                    <optgroup label="Agenti IA">
                      {agents.filter(a => a.active || a.id === form.asignado).map(a => (
                        <option key={a.id} value={a.id}>{a.name} · {a.role}</option>
                      ))}
                    </optgroup>
                  )}
                </select>
              </div>

              {/* Cliente */}
              <div className="form-group">
                <label className="form-label">Cliente</label>
                <select
                  className="form-select"
                  value={form.cliente}
                  onChange={e => setField('cliente', e.target.value)}
                >
                  <option value="">— Seleziona —</option>
                  {CLIENTES.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
            </div>

            <div className="form-row">
              {/* Prioridad */}
              <div className="form-group">
                <label className="form-label">Priorità</label>
                <select
                  className="form-select"
                  value={form.prioridad}
                  onChange={e => setField('prioridad', e.target.value)}
                >
                  <option value="urgente">🔴 Urgente</option>
                  <option value="alta">🟠 Alta</option>
                  <option value="media">🔵 Media</option>
                  <option value="baja">🟢 Baja</option>
                </select>
              </div>

              {/* Estado */}
              <div className="form-group">
                <label className="form-label">Stato</label>
                <select
                  className="form-select"
                  value={form.estado}
                  onChange={e => setField('estado', e.target.value)}
                  disabled={!canChangeStatus}
                >
                  <option value="pendiente">⏳ Da fare</option>
                  <option value="en_progreso">⚡ In corso</option>
                  <option value="en_espera">🕓 In attesa di approvazione</option>
                  <option value="en_revision">🔧 Correzioni</option>
                  <option value="completada">✅ Completada</option>
                </select>
              </div>
            </div>

            {/* Fechas */}
            <div className="form-row">
              <div className="form-group">
                <label className="form-label">Data di richiesta</label>
                <input
                  className="form-input"
                  type="date"
                  value={form.fechaRequerimiento}
                  onChange={e => setField('fechaRequerimiento', e.target.value)}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Data di scadenza</label>
                <input
                  className="form-input"
                  type="date"
                  value={form.fechaVencimiento}
                  onChange={e => setField('fechaVencimiento', e.target.value)}
                />
              </div>
            </div>

            {/* Tempo stimato di esecuzione */}
            <div className="form-group">
              <label className="form-label">Tempo stimato di esecuzione</label>
              <select
                className="form-select"
                value={form.tiempoEstimadoMin || 0}
                onChange={e => setField('tiempoEstimadoMin', Number(e.target.value))}
              >
                <option value={0}>Nessun limite</option>
                {[15, 30, 45, 60, 90, 120, 150, 180, 240, 300, 360, 480].map(m => (
                  <option key={m} value={m}>
                    {m < 60 ? `${m} minutos` : `${m / 60} hora${m > 60 ? 's' : ''}${m % 60 ? ` ${m % 60} min` : ''}`}
                  </option>
                ))}
              </select>
              <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                Se il collaboratore supera questo tempo, riceve un avviso: sta impiegando più del previsto.
              </span>
            </div>

            {/* Feedback */}
            <div className="form-group">
              <label className="form-label">Feedback / Note del cliente</label>
              <textarea
                className="form-textarea"
                value={form.feedback}
                onChange={e => setField('feedback', e.target.value)}
                placeholder="Commenti, osservazioni..."
                rows={2}
              />
            </div>

            {/* Valutazione (solo tareas completati; edita solo el Super admin) */}
            {isEdit && (editingTask.estado === 'completada' || form.estado === 'completada') && (
              <div className="form-group">
                <label className="form-label">Valutazione del lavoro</label>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  {[1, 2, 3, 4, 5].map(n => (
                    <button
                      key={n}
                      type="button"
                      disabled={!isSuperAdmin}
                      onClick={() => isSuperAdmin && setField('rating', form.rating === n ? 0 : n)}
                      style={{
                        background: 'transparent', border: 'none', padding: 2,
                        cursor: isSuperAdmin ? 'pointer' : 'default',
                        color: (form.rating || 0) >= n ? 'var(--warning)' : 'var(--text-3)',
                      }}
                      title={isSuperAdmin ? `Valuta con ${n} stell${n > 1 ? 'e' : 'a'}` : 'Solo el Super admin califica'}
                    >
                      <Icon name="star" size={26} filled={(form.rating || 0) >= n} />
                    </button>
                  ))}
                  <span style={{ fontSize: 12.5, color: 'var(--text-3)', marginLeft: 8 }}>
                    {form.rating
                      ? ['', 'Necesita mejorar', 'Regular', 'Bien', 'Muy bien', 'Excelente'][form.rating]
                      : isSuperAdmin ? 'Senza valutazione' : 'Ancora nessuna valutazione'}
                  </span>
                </div>
                {!isSuperAdmin && (
                  <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                    La valutazione la assegna la direzione dopo aver rivisto il tuo lavoro.
                  </span>
                )}
              </div>
            )}

            {/* Edit info */}
            {isEdit && (
              <div style={{ fontSize: 12, color: 'var(--text-3)', paddingTop: 4, borderTop: '1px solid var(--border)', lineHeight: 1.7 }}>
                {editingTask.createdAt?.toDate && (
                  <div>
                    Creada: {editingTask.createdAt.toDate().toLocaleDateString('it-IT', { day: '2-digit', month: 'short', year: 'numeric' })}
                    {' alle '}
                    {editingTask.createdAt.toDate().toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })}
                    {editingTask.createdBy && <> · da {editingTask.createdBy}</>}
                  </div>
                )}
                {editingTask.lastEditedBy && <div>Ultima modifica: {editingTask.lastEditedBy}</div>}
              </div>
            )}
          </div>
        )}

        {!readOnly && (
          <div className="modal-footer">
            <button className="btn btn-secondary" onClick={handleCancel}>Annulla</button>
            <button
              className="btn btn-primary"
              onClick={handleSave}
              disabled={saving}
            >
              {saving ? 'Salvataggio...' : isEdit ? 'Salva modifiche' : 'Crea task'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// Trabajo entregado por un agente de IA (lo escribe el motor de agentes desde el PC del dueño)
function AgentResult({ task, showToast }) {
  const status = task.agentStatus
  if (!status && !task.agentOutput) return null
  const info = {
    running: { text: 'L\'agente sta lavorando a questo task…', color: 'var(--purple)' },
    queued: { text: 'In coda: l\'agente lo prenderà appena finisce quello che sta facendo', color: 'var(--text-3)' },
    needs_info: { text: 'L\'agente ha bisogno di informazioni per continuare', color: 'var(--warning)' },
    error: { text: 'L\'agente ha avuto un problema con questo task', color: 'var(--danger)' },
    done: { text: 'Consegna pronta per la tua approvazione', color: 'var(--success)' },
  }[status] || { text: 'Risultato dell\'agente', color: 'var(--text-2)' }
  const body = status === 'needs_info' ? task.agentQuestion : status === 'error' ? task.agentError : task.agentOutput
  const copy = async () => {
    try { await navigator.clipboard.writeText(body || ''); showToast('Copiato') } catch { showToast('Impossibile copiare', 'error') }
  }
  return (
    <div className="agent-result">
      <div className="agent-result-head">
        <span style={{ color: info.color, fontWeight: 700 }}>{info.text}</span>
        {body && <button type="button" className="btn btn-secondary btn-sm" onClick={copy}>Copia</button>}
      </div>
      {body && <pre className="agent-result-body">{body}</pre>}
      {status === 'done' && (
        <p className="agent-result-hint">
          Se va bene, spostalo in Completato. Se servono modifiche, scrivi i commenti in Feedback e spostalo in Correzioni: l'agente lo rifà.
        </p>
      )}
      {status === 'needs_info' && task.needsFull && (
        <button type="button" className="btn btn-primary btn-sm" style={{ marginTop: 8 }}
          onClick={async () => {
            try {
              await updateDoc(doc(db, 'tasks', task.id), { fullRunOk: true, needsFull: false, estado: 'en_revision' })
              showToast('Esecuzione completa autorizzata: l\'agente parte')
            } catch (e) { showToast('Impossibile autorizzare', 'error') }
          }}>
          Autorizza esecuzione completa
        </button>
      )}
      {status === 'needs_info' && !task.needsFull && (
        <p className="agent-result-hint">
          Rispondi alla domanda nel campo Feedback e spostalo in Correzioni: l'agente riparte con la tua risposta.
        </p>
      )}
      {task.agentRuns > 0 && (
        <p className="agent-result-hint">
          Agente: {task.agentRuns} {task.agentRuns === 1 ? 'esecuzione' : 'esecuzioni'} · {Math.max(1, Math.round((task.totalTimeSeconds || 0) / 60))} min · costo {(task.agentCostUsd || 0).toFixed(2)} $
        </p>
      )}
    </div>
  )
}

function ReadOnlyTask({ task, users }) {
  const u = users.find(u => u.uid === task.asignado)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <h3 style={{ fontSize: 17, fontWeight: 700 }}>{task.tarea}</h3>
      {task.descripcion && <p style={{ fontSize: 14 }}>{task.descripcion}</p>}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <span className={`badge badge-${task.estado}`}>{task.estado.replace('_', ' ')}</span>
        <span className={`badge badge-${task.prioridad}`}>{task.prioridad}</span>
        {task.cliente && <span className="badge" style={{ background: 'var(--card-2)', color: 'var(--text-2)' }}>{task.cliente}</span>}
      </div>
      {u && (
        <div style={{ fontSize: 13, color: 'var(--text-2)' }}>
          Asignada a: <strong>{u.name}</strong>
        </div>
      )}
      {task.feedback && (
        <div>
          <p className="form-label">Feedback</p>
          <p style={{ fontSize: 13 }}>{task.feedback}</p>
        </div>
      )}
    </div>
  )
}
