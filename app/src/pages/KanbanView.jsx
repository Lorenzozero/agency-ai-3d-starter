import React, { useState, useEffect, useMemo, useRef } from 'react'
import { useApp } from '../App.jsx'
import { db } from '../firebase.js'
import { doc, updateDoc, deleteDoc, addDoc, collection, serverTimestamp } from 'firebase/firestore'
import { logWork } from '../utils/worklog.js'
import Icon from '../components/Icons.jsx'

const COLUMNS = [
  { id: 'pendiente',   label: '📥 Triage / Da fare', icon: 'inbox',     color: '#a855f7' },
  { id: 'en_progreso', label: '⚡ In corso',         icon: 'timer',     color: 'var(--purple)' },
  { id: 'en_espera',   label: '⏳ In attesa',        icon: 'clock',     color: 'var(--info)' },
  { id: 'en_revision', label: '🛠️ Revisione',        icon: 'wrench',    color: 'var(--warning)' },
  { id: 'completada',  label: '✅ Completato',       icon: 'check',     color: 'var(--success)' },
]

const PAGE_SIZE = 20

// Máximo de tareas simultáneas In corso por empleado
export const WIP_LIMIT = 2

// ¿El empleado ya alcanzó su límite de tareas en progreso?
export function wipLimitReached(allTasks, uid, excludeTaskId = null) {
  return allTasks.filter(t =>
    t.asignado === uid &&
    t.estado === 'en_progreso' &&
    t.id !== excludeTaskId
  ).length >= WIP_LIMIT
}

// Duplicar una tarea como plantilla: copia todo el contenido,
// resetea la ejecución (estado, timer, calificación, marcas)
export async function duplicateTask(task, byEmail) {
  const copy = {
    tarea: `${task.tarea} (copia)`,
    descripcion: task.descripcion || '',
    asignado: task.asignado || '',
    cliente: task.cliente || '',
    prioridad: task.prioridad || 'media',
    estado: 'pendiente',
    fechaRequerimiento: task.fechaRequerimiento || null,
    fechaVencimiento: task.fechaVencimiento || null,
    tiempoEstimadoMin: task.tiempoEstimadoMin || 0,
    links: task.links || '',
    linkTerminada: '', // el entregable pertenece a la ejecución original
    feedback: task.feedback || '',
    fechaInicio: null,
    fechaEntrega: null,
    timerStartedAt: null,
    timerPaused: false,
    pendingAcceptance: false,
    totalTimeSeconds: 0,
    rating: 0,
    completedBy: null,
    completedByEmail: null,
    createdAt: serverTimestamp(),
    createdBy: byEmail,
    lastEditedBy: byEmail,
    lastEditedAt: serverTimestamp(),
  }
  await addDoc(collection(db, 'tasks'), copy)
}

// Filtro por periodo sobre createdAt
export function inPeriod(task, period, monthVal, fromDate, toDate) {
  if (period === 'all') return true
  const raw = task.createdAt
  const ms = raw?.toMillis?.()
    || (typeof raw?.__ts === 'number' ? raw.__ts : 0)
    || (typeof raw?.seconds === 'number' ? raw.seconds * 1000 : 0)
    || (typeof raw === 'number' ? raw : 0)
    || (typeof raw === 'string' ? new Date(raw).getTime() : 0)
    || (task.fechaRequerimiento ? new Date(task.fechaRequerimiento + 'T12:00:00').getTime() : 0)
  if (!ms) return true
  const d = new Date(ms)
  const now = new Date()
  if (period === 'today') {
    const todayStart = new Date(now); todayStart.setHours(0,0,0,0)
    return ms >= todayStart.getTime()
  }
  if (period === 'week') {
    const weekStart = new Date(now); weekStart.setDate(now.getDate() - now.getDay()); weekStart.setHours(0,0,0,0)
    return ms >= weekStart.getTime()
  }
  if (period === 'month') {
    return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()
  }
  if (period === 'pickMonth' && monthVal) {
    const [y, m] = monthVal.split('-').map(Number)
    return d.getFullYear() === y && d.getMonth() === m - 1
  }
  if (period === 'range') {
    if (fromDate && ms < new Date(fromDate + 'T00:00:00').getTime()) return false
    if (toDate && ms > new Date(toDate + 'T23:59:59').getTime()) return false
    return true
  }
  return true
}

// Selector de periodo reutilizable (Toggle in riga senza tendine)
export function PeriodFilter({ period, setPeriod }) {
  return (
    <div className="period-toggle-bar btn-row">
      {[
        { id: 'today', label: 'Oggi' },
        { id: 'week',  label: 'Settimana' },
        { id: 'month', label: 'Mese' },
        { id: 'all',   label: 'Sempre' },
      ].map(p => (
        <button
          key={p.id}
          type="button"
          className={`period-toggle-btn ${period === p.id ? 'active' : ''}`}
          onClick={() => setPeriod(p.id)}
        >
          {p.label}
        </button>
      ))}
    </div>
  )
}

// Orden: prioridad más alta primero; a igual prioridad, la asignada primero (más vieja arriba)
const PRIORITY_ORDER = { urgente: 0, alta: 1, media: 2, baja: 3 }
export function sortByPriorityAndAge(list) {
  return [...list].sort((a, b) => {
    const pa = PRIORITY_ORDER[a.prioridad] ?? 4
    const pb = PRIORITY_ORDER[b.prioridad] ?? 4
    if (pa !== pb) return pa - pb
    const ca = a.createdAt?.toMillis?.() || 0
    const cb = b.createdAt?.toMillis?.() || 0
    return ca - cb
  })
}

function fmtStamp(ts) {
  if (!ts) return ''
  const d = ts.toDate ? ts.toDate() : new Date(ts)
  return d.toLocaleDateString('it-IT', { day: '2-digit', month: 'short', year: '2-digit' }) +
    ' · ' + d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })
}

function fmtDuration(seconds) {
  if (!seconds) return '0m'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

export function fmtDate(ts) {
  if (!ts) return '—'
  const d = ts.toDate ? ts.toDate() : new Date(ts)
  return d.toLocaleDateString('it-IT', { day: '2-digit', month: 'short', year: '2-digit' })
}

function SortTh({ label, col, sortBy, sortDir, onSort }) {
  const active = sortBy === col
  return (
    <th
      style={{ cursor: 'pointer', userSelect: 'none' }}
      onClick={() => onSort(col)}
    >
      {label} {active ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
    </th>
  )
}

// Urgencia por fecha de vencimiento — mismos colores que las prioridades
export function dueInfo(task) {
  if (!task.fechaVencimiento || task.estado === 'completada') return null
  let ms = 0
  if (typeof task.fechaVencimiento.toMillis === 'function') {
    ms = task.fechaVencimiento.toMillis()
  } else if (typeof task.fechaVencimiento.seconds === 'number') {
    ms = task.fechaVencimiento.seconds * 1000
  } else if (typeof task.fechaVencimiento === 'string' || typeof task.fechaVencimiento === 'number') {
    ms = new Date(task.fechaVencimiento).getTime()
  }
  if (!ms || isNaN(ms)) return null
  const days = (ms - Date.now()) / 86400000
  if (days < 0)  return { label: 'Scaduto', color: 'var(--danger)', bg: 'rgba(239,68,68,0.12)' }
  if (days <= 1) return { label: 'Scade oggi', color: 'var(--danger)', bg: 'rgba(239,68,68,0.12)' }
  if (days <= 3) return { label: `Scade tra ${Math.ceil(days)} gg`, color: 'var(--warning)', bg: 'rgba(245,158,11,0.12)' }
  if (days <= 7) return { label: `Scade tra ${Math.ceil(days)} gg`, color: 'var(--info)', bg: 'rgba(59,130,246,0.12)' }
  return { label: `Scade tra ${Math.ceil(days)} gg`, color: 'var(--success)', bg: 'rgba(34,197,94,0.12)' }
}

export default function KanbanView() {
  const { tasks, users, agents, clients: CLIENTES = [], isAdmin, user, userProfile, showToast, openEditTask, openNewTask, sendNotification } = useApp()
  // Vista: 'board' (Pipeline Canvas) o 'table' (Elenco Tabellare)
  const [viewMode, setViewMode] = useState(() => localStorage.getItem('hermes_task_view') || 'board')
  const [dragOver, setDragOver] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  // Filtri condivisi
  const [filterUid, setFilterUid] = useState(isAdmin ? '' : user.uid)
  const [filterClient, setFilterClient] = useState('')
  const [filterPriority, setFilterPriority] = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  // Ordinamento tabella
  const [sortBy, setSortBy] = useState('createdAt')
  const [sortDir, setSortDir] = useState('desc')
  // Filtro per periodo
  const [period, setPeriod] = useState('all')
  const [monthVal, setMonthVal] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  // "Carica altri" por columna
  const [visibleCounts, setVisibleCounts] = useState({})

  // Las tareas pueden estar asignadas a un empleado o a un agente de IA
  const getUserById = (uid) => users.find(u => u.uid === uid)
    || ((a) => a && { uid: a.id, name: `${a.name} (agente)`, color: a.look?.torso, photo: a.photo || '' })(agents.find(a => a.id === uid))
  const visibleTasks = tasks
    .filter(t => !filterUid || t.asignado === filterUid)
    .filter(t => inPeriod(t, period, monthVal, fromDate, toDate))
    .filter(t => !filterClient || t.cliente === filterClient)
    .filter(t => !filterPriority || t.prioridad === filterPriority)

  const sortedTableTasks = useMemo(() => {
    let list = visibleTasks
    if (filterStatus) list = list.filter(t => t.estado === filterStatus)
    return [...list].sort((a, b) => {
      let av = a[sortBy], bv = b[sortBy]
      if (av?.toMillis) av = av.toMillis()
      if (bv?.toMillis) bv = bv.toMillis()
      if (av < bv) return sortDir === 'asc' ? -1 : 1
      if (av > bv) return sortDir === 'asc' ? 1 : -1
      return 0
    })
  }, [visibleTasks, filterStatus, sortBy, sortDir])

  const handleSort = (col) => {
    if (sortBy === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortBy(col); setSortDir('asc') }
  }

  // Esportazione rapida CSV
  const exportCSV = () => {
    const headers = ['Task','Descrizione','Assegnato','Cliente','Priorità','Stato','Inizio','Consegna','Durata (ore)','Creato da']
    const taskSet = viewMode === 'table' ? sortedTableTasks : visibleTasks
    const rows = taskSet.map(t => {
      const u = getUserById(t.asignado)
      const dur = t.totalTimeSeconds ? (t.totalTimeSeconds / 3600).toFixed(2) : ''
      return [
        t.tarea, t.descripcion, u?.name || t.asignado, t.cliente,
        t.prioridad, t.estado,
        fmtDate(t.fechaInicio), fmtDate(t.fechaEntrega),
        dur, t.createdBy
      ].map(v => `"${(v || '').toString().replace(/"/g, '""')}"`).join(',')
    })
    const csv = [headers.join(','), ...rows].join('\n')
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `tasks-${Date.now()}.csv`; a.click()
    URL.revokeObjectURL(url)
    showToast('CSV esportato ✓')
  }

  const canMoveTask = (task) => {
    if (isAdmin) return true
    return task.asignado === user.uid
  }

  const handleDragStart = (e, taskId) => {
    e.dataTransfer.setData('taskId', taskId)
    e.dataTransfer.effectAllowed = 'move'
  }

  const handleDragOver = (e, colId) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    setDragOver(colId)
  }

  const handleDragLeave = () => setDragOver(null)

  const handleDrop = async (e, newStatus) => {
    e.preventDefault()
    setDragOver(null)
    const taskId = e.dataTransfer.getData('taskId')
    if (!taskId) return
    const task = tasks.find(t => t.id === taskId)
    if (!task) return
    await moveTask(task, newStatus)
  }

  // Mover tarea a un nuevo estado (usado por drag & drop Y por los botones ◀ ▶)
  const moveTask = async (task, newStatus) => {
    if (!task || task.estado === newStatus) return

    if (!canMoveTask(task)) {
      showToast('Puoi spostare solo i tuoi task', 'error')
      return
    }

    // Límite de tareas simultáneas: máximo 2 In corso por empleado
    if (newStatus === 'en_progreso' && wipLimitReached(tasks, task.asignado, task.id)) {
      showToast(`Al massimo ${WIP_LIMIT} task In corso. Finiscine uno o rimettilo in Da fare prima di prenderne un altro`, 'error')
      return
    }

    const updates = {
      estado: newStatus,
      lastEditedBy: user.email,
      lastEditedAt: serverTimestamp(),
    }

    // Lógica de timer
    if (newStatus === 'en_progreso' && task.estado !== 'en_progreso') {
      const returnedFromReview = ['en_revision', 'en_espera'].includes(task.estado) && task.asignado !== user.uid
      if (returnedFromReview) {
        // El admin la devuelve desde Revisión: espera aceptación, sin timer
        updates.timerStartedAt = null
        updates.timerPaused = true
        updates.pendingAcceptance = true
        updates.returnedBy = userProfile?.name || user.email
      } else {
        // Cualquier otro caso: el timer arranca de inmediato
        updates.timerStartedAt = serverTimestamp()
        updates.timerPaused = false
        updates.pendingAcceptance = false
      }
      if (!task.fechaInicio) updates.fechaInicio = serverTimestamp()
    }
    if (task.estado === 'en_progreso' && newStatus !== 'en_progreso') {
      if (task.timerStartedAt && !task.timerPaused) {
        const elapsed = Math.floor(
          (Date.now() - (task.timerStartedAt.toMillis?.() || task.timerStartedAt.seconds * 1000)) / 1000
        )
        updates.totalTimeSeconds = (task.totalTimeSeconds || 0) + elapsed
        logWork(task.asignado, elapsed)
      }
      updates.timerStartedAt = null
      updates.timerPaused = false
    }
    // Marca de completado: quién, cuándo, cuánto demoró
    if (newStatus === 'completada') {
      updates.fechaEntrega = serverTimestamp()
      updates.completedBy = userProfile?.name || user.email
      updates.completedByEmail = user.email
    }
    // Volver a pendiente: se conserva el tiempo trabajado para seguir acumulando
    if (newStatus === 'pendiente') {
      updates.fechaEntrega = null
      updates.timerStartedAt = null
      updates.timerPaused = false
      updates.completedBy = null
      updates.completedByEmail = null
    }

    try {
      await updateDoc(doc(db, 'tasks', task.id), updates)
      showToast(`Task spostato in "${COLUMNS.find(c => c.id === newStatus)?.label}"`)
    } catch (err) {
      console.error(err)
      showToast('Impossibile spostare il task — controlla i permessi', 'error')
    }
  }

  // Sposta nella colonna precedente/siguiente con botones (funciona en móvil)
  const moveByStep = (task, dir) => {
    const idx = COLUMNS.findIndex(c => c.id === task.estado)
    const newIdx = idx + dir
    if (newIdx < 0 || newIdx >= COLUMNS.length) return
    moveTask(task, COLUMNS[newIdx].id)
  }

  const handleDelete = async (task) => {
    if (!isAdmin) return
    if (!confirm(`Eliminare "${task.tarea}"? Questa azione non si può annullare.`)) return
    await deleteDoc(doc(db, 'tasks', task.id))
    showToast('Task eliminato', 'info')
  }

  // Auto-asignarse una tarea pendiente de otro (o sin asignar)
  const handleClaim = async (task) => {
    if (task.estado !== 'pendiente') { showToast('Puoi assegnarti solo task da fare', 'error'); return }
    try {
      await updateDoc(doc(db, 'tasks', task.id), {
        asignado: user.uid,
        lastEditedBy: user.email,
        lastEditedAt: serverTimestamp(),
      })
      showToast(`Ti sei assegnato "${task.tarea}"`)
    } catch (e) {
      console.error(e)
      showToast('Impossibile assegnare il task', 'error')
    }
  }

  const handleDuplicate = async (task) => {
    try {
      await duplicateTask(task, user.email)
      showToast(`Task duplicato: "${task.tarea} (copia)" è in Da fare`)
    } catch (e) {
      console.error(e)
      showToast('Impossibile duplicare il task', 'error')
    }
  }

  // Pausar / reanudar timer de una tarea en progreso
  const handlePauseResume = async (task) => {
    if (!task.timerPaused) {
      const startMs = task.timerStartedAt?.toMillis?.() || (task.timerStartedAt?.seconds || 0) * 1000
      const elapsed = startMs ? Math.floor((Date.now() - startMs) / 1000) : 0
      await updateDoc(doc(db, 'tasks', task.id), {
        timerPaused: true,
        timerStartedAt: null,
        totalTimeSeconds: (task.totalTimeSeconds || 0) + elapsed,
        lastEditedBy: user.email,
        lastEditedAt: serverTimestamp(),
      })
      logWork(task.asignado, elapsed)
      showToast('⏸ Timer in pausa')
    } else {
      await updateDoc(doc(db, 'tasks', task.id), {
        timerPaused: false,
        pendingAcceptance: false,
        timerStartedAt: serverTimestamp(),
        lastEditedBy: user.email,
        lastEditedAt: serverTimestamp(),
      })
      showToast('▶ Timer ripreso')
    }
  }

  // Triage: Avvio rapido con 1 click
  const handleStartNow = async (task) => {
    try {
      await updateDoc(doc(db, 'tasks', task.id), {
        estado: 'en_progreso',
        asignado: task.asignado || user.uid,
        timerStartedAt: serverTimestamp(),
        timerPaused: false,
        lastEditedBy: user.email,
        lastEditedAt: serverTimestamp(),
      })
      showToast('🚀 Task avviato in lavorazione!')
    } catch (e) {
      showToast('Errore avvio task', 'error')
    }
  }

  // Figma Canvas Zoom & Pan State
  const [scale, setScale] = useState(1)
  const [triageFilter, setTriageFilter] = useState('all') // 'all', 'urgente', 'alta', 'unassigned', 'due'
  const viewportRef = React.useRef(null)
  const isDraggingCanvas = React.useRef(false)
  const panStart = React.useRef({ x: 0, y: 0, scrollLeft: 0, scrollTop: 0 })

  const handleZoom = (delta) => {
    setScale(s => Math.min(1.6, Math.max(0.4, Math.round((s + delta) * 100) / 100)))
  }

  const handleWheelCanvas = (e) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault()
      handleZoom(e.deltaY < 0 ? 0.12 : -0.12)
    }
  }

  const handleFit = () => {
    if (!viewportRef.current) return
    const vpWidth = viewportRef.current.clientWidth - 24
    const fit = Math.min(1.0, Math.max(0.45, vpWidth / 1500))
    setScale(Math.round(fit * 100) / 100)
    viewportRef.current.scrollTo({ left: 0, top: 0, behavior: 'smooth' })
  }

  const handleMouseDownCanvas = (e) => {
    if (e.target.closest('.kanban-card') || e.target.closest('button') || e.target.closest('select') || e.target.closest('.figma-canvas-toolbar')) return
    isDraggingCanvas.current = true
    viewportRef.current?.classList.add('panning')
    panStart.current = {
      x: e.clientX,
      y: e.clientY,
      scrollLeft: viewportRef.current.scrollLeft,
      scrollTop: viewportRef.current.scrollTop
    }
  }

  const handleMouseMoveCanvas = (e) => {
    if (!isDraggingCanvas.current || !viewportRef.current) return
    const dx = e.clientX - panStart.current.x
    const dy = e.clientY - panStart.current.y
    viewportRef.current.scrollLeft = panStart.current.scrollLeft - dx
    viewportRef.current.scrollTop = panStart.current.scrollTop - dy
  }

  const handleMouseUpCanvas = () => {
    isDraggingCanvas.current = false
    viewportRef.current?.classList.remove('panning')
  }

  const handleTouchStart = (e) => {
    if (e.touches.length !== 1) return
    const t = e.touches[0]
    if (e.target.closest('.kanban-card') || e.target.closest('button') || e.target.closest('select') || e.target.closest('.figma-canvas-toolbar')) return
    isDraggingCanvas.current = true
    panStart.current = {
      x: t.clientX,
      y: t.clientY,
      scrollLeft: viewportRef.current.scrollLeft,
      scrollTop: viewportRef.current.scrollTop
    }
  }

  const handleTouchMove = (e) => {
    if (!isDraggingCanvas.current || !viewportRef.current || e.touches.length !== 1) return
    const t = e.touches[0]
    const dx = t.clientX - panStart.current.x
    const dy = t.clientY - panStart.current.y
    viewportRef.current.scrollLeft = panStart.current.scrollLeft - dx
    viewportRef.current.scrollTop = panStart.current.scrollTop - dy
  }

  const handleTouchEnd = () => {
    isDraggingCanvas.current = false
  }

  const scrollToCol = (idx) => {
    if (!viewportRef.current) return
    const targetX = idx * 260 * scale
    viewportRef.current.scrollTo({ left: targetX, behavior: 'smooth' })
  }

  const getColTasks = (colId) => {
    return sortByPriorityAndAge(visibleTasks.filter(t => t.estado === colId))
  }

  return (
    <div>
      {/* Centered Controls Bar: View Switcher, Filters & Action Buttons */}
      <div className="page-header" style={{ marginBottom: 8, display: 'flex', justifyContent: 'center' }}>
        <div className="page-actions btn-row" style={{ display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'center', flexWrap: 'wrap' }}>
          
          {/* View Mode Toggle: Tavola vs Tabella */}
          <div style={{ display: 'flex', background: 'var(--card-2)', borderRadius: 8, padding: 3, gap: 2 }}>
            <button
              className="btn btn-sm"
              style={{
                background: viewMode === 'board' ? 'var(--purple)' : 'transparent',
                color: viewMode === 'board' ? '#fff' : 'var(--text-2)',
                padding: '5px 12px', fontSize: 12.5, display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600
              }}
              onClick={() => { setViewMode('board'); localStorage.setItem('hermes_task_view', 'board') }}
              title="Vista Tavola (Pipeline)"
            >
              <Icon name="columns" size={13} /> Tavola
            </button>
            <button
              className="btn btn-sm"
              style={{
                background: viewMode === 'table' ? 'var(--purple)' : 'transparent',
                color: viewMode === 'table' ? '#fff' : 'var(--text-2)',
                padding: '5px 12px', fontSize: 12.5, display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600
              }}
              onClick={() => { setViewMode('table'); localStorage.setItem('hermes_task_view', 'table') }}
              title="Vista Tabella (Elenco)"
            >
              <Icon name="list" size={13} /> Tabella
            </button>
          </div>

          {/* Filtro per membro del team */}
          <select
            className="form-select"
            style={{ fontSize: 12.5, padding: '5px 10px', width: 'auto', minWidth: 130, borderRadius: 'var(--radius-sm)' }}
            value={filterUid}
            onChange={e => setFilterUid(e.target.value)}
          >
            <option value="">Tutta la squadra</option>
            <option value={user.uid}>I miei task</option>
            {users
              .filter(u => u.uid !== user.uid && u.role !== 'pending')
              .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
              .map(u => (
                <option key={u.uid} value={u.uid}>{u.name}</option>
              ))
            }
            {agents.length > 0 && (
              <optgroup label="Agenti IA">
                {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
              </optgroup>
            )}
          </select>

          {/* Filtri extra (solo in modalità Tabella per mantenere la Tavola pulita) */}
          {viewMode === 'table' && (
            <>
              <PeriodFilter
                period={period} setPeriod={setPeriod}
                monthVal={monthVal} setMonthVal={setMonthVal}
                fromDate={fromDate} setFromDate={setFromDate}
                toDate={toDate} setToDate={setToDate}
              />

              <button
                className="btn btn-secondary btn-sm"
                onClick={exportCSV}
                title="Esporta i task filtrati in formato CSV"
                style={{ fontSize: 12.5, padding: '5px 11px', display: 'flex', alignItems: 'center', gap: 5 }}
              >
                <Icon name="download" size={13} /> CSV
              </button>
            </>
          )}
        </div>
      </div>

      {viewMode === 'board' ? (
        <>

      {/* Quick Column Jump for Mobile */}
      <div className="mobile-col-pills">
        {COLUMNS.map((col, idx) => (
          <button key={col.id} className="mobile-col-pill" onClick={() => scrollToCol(idx)}>
            <span style={{ color: col.color }}><Icon name={col.icon} size={12} /></span>
            <span>{col.id === 'pendiente' ? 'Triage' : col.id === 'en_progreso' ? 'In corso' : col.id === 'en_espera' ? 'In attesa' : col.id === 'en_revision' ? 'Revisione' : 'Fatto'}</span>
            <span className="col-count-badge">{getColTasks(col.id).length}</span>
          </button>
        ))}
      </div>

      {/* Figma-Style Canvas Viewport */}
      <div
        ref={viewportRef}
        className="figma-canvas-viewport"
        onWheel={handleWheelCanvas}
        onMouseDown={handleMouseDownCanvas}
        onMouseMove={handleMouseMoveCanvas}
        onMouseUp={handleMouseUpCanvas}
        onMouseLeave={handleMouseUpCanvas}
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
      >
        {/* Floating Figma Zoom/Pan Controls */}
        <div className="figma-canvas-toolbar" onClick={e => e.stopPropagation()}>
          <button className="figma-btn" onClick={() => handleZoom(-0.15)} title="Rimpicciolisci tavola (-)">
            <Icon name="minus" size={13} />
          </button>
          <button className="figma-btn" onClick={() => setScale(1)} title="Scala reale 100%">
            {Math.round(scale * 100)}%
          </button>
          <button className="figma-btn" onClick={() => handleZoom(0.15)} title="Ingrandisci tavola (+)">
            <Icon name="plus" size={13} />
          </button>
          <div style={{ width: 1, height: 14, background: 'var(--border)' }} />
          <button className="figma-btn" onClick={handleFit} title="Adatta tavola allo schermo">
            <Icon name="target" size={13} /> Adatta
          </button>
        </div>

        {/* Scalable Board Surface */}
        <div
          className="figma-board-surface"
          style={{ transform: `scale(${scale})`, transformOrigin: 'top center' }}
        >
          <div className="kanban-board">
            {COLUMNS.map(col => {
              const colTasks = getColTasks(col.id)
              return (
                <div
                  key={col.id}
                  className={`kanban-col ${dragOver === col.id ? 'drag-over' : ''}`}
                  onDragOver={e => handleDragOver(e, col.id)}
                  onDragLeave={handleDragLeave}
                  onDrop={e => handleDrop(e, col.id)}
                >
                  <div className="kanban-col-header" style={{ justifyContent: 'center', gap: 8 }}>
                    <span className="kanban-col-title" style={{ color: col.color, display: 'inline-flex', alignItems: 'center', gap: 7, fontWeight: 700 }}>
                      <Icon name={col.icon} size={18} strokeWidth={2.2} /> {col.label}
                    </span>
                    <span className="kanban-count">{colTasks.length}</span>
                  </div>

                  {colTasks.length === 0 && (
                    <div style={{
                      textAlign: 'center', color: 'var(--text-3)', fontSize: 12,
                      padding: '24px 10px', borderRadius: 8,
                      border: '1px dashed var(--border)', marginTop: 4
                    }}>
                      Nessun task
                    </div>
                  )}

                  {colTasks.slice(0, visibleCounts[col.id] || PAGE_SIZE).map(task => (
                    col.id === 'completada' ? (
                      <CompactCard
                        key={task.id}
                        task={task}
                        assignedUser={getUserById(task.asignado)}
                        draggable={canMoveTask(task)}
                        onDragStart={handleDragStart}
                        onEdit={openEditTask}
                        canDelete={isAdmin}
                        onDelete={handleDelete}
                        onDuplicate={handleDuplicate}
                      />
                    ) : (
                      <KanbanCard
                        key={task.id}
                        task={task}
                        assignedUser={getUserById(task.asignado)}
                        draggable={canMoveTask(task)}
                        canEdit={isAdmin || task.asignado === user.uid}
                        canDelete={isAdmin}
                        canPause={task.asignado === user.uid || isAdmin}
                        expanded={expandedId === task.id}
                        onToggleExpand={() => setExpandedId(id => id === task.id ? null : task.id)}
                        onDragStart={handleDragStart}
                        onEdit={openEditTask}
                        onDelete={handleDelete}
                        onPauseResume={handlePauseResume}
                        onMoveStep={moveByStep}
                        onDuplicate={handleDuplicate}
                        canClaim={task.estado === 'pendiente' && task.asignado !== user.uid}
                        onClaim={handleClaim}
                        onStartNow={handleStartNow}
                        colIndex={COLUMNS.findIndex(c => c.id === col.id)}
                        totalCols={COLUMNS.length}
                      />
                    )
                  ))}

                  {/* Carica altri */}
                  {colTasks.length > (visibleCounts[col.id] || PAGE_SIZE) && (
                    <button
                      className="btn btn-secondary btn-sm"
                      style={{ width: '100%', justifyContent: 'center', marginTop: 6 }}
                      onClick={() => setVisibleCounts(v => ({ ...v, [col.id]: (v[col.id] || PAGE_SIZE) + PAGE_SIZE }))}
                    >
                      Carica altri ({colTasks.length - (visibleCounts[col.id] || PAGE_SIZE)} rimanenti)
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </>
  ) : (
    /* Render Modalità Tabella */
    sortedTableTasks.length === 0 ? (
      <div className="empty-state">
        <div className="empty-icon">📋</div>
        <h3>Nessun task</h3>
        <p>Nessun task corrisponde ai filtri selezionati</p>
      </div>
    ) : (
      <div className="table-wrap" style={{ marginTop: 6 }}>
        <table>
          <thead>
            <tr>
              <SortTh label="Task" col="tarea" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
              <th>Assegnato</th>
              <SortTh label="Priorità" col="prioridad" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
              <SortTh label="Stato" col="estado" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
              <SortTh label="Scade" col="fechaVencimiento" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
              <SortTh label="Inizio" col="fechaInicio" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
              <SortTh label="Consegna" col="fechaEntrega" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} />
              <th>Durata</th>
            </tr>
          </thead>
          <tbody>
            {sortedTableTasks.map(task => {
              const assignedUser = getUserById(task.asignado)
              const due = dueInfo(task)
              const stLabel = {
                pendiente: 'Da fare',
                en_progreso: 'In corso',
                en_espera: 'In attesa',
                en_revision: 'Correzioni',
                completada: 'Completato',
              }[task.estado] || task.estado
              return (
                <tr key={task.id} style={{ cursor: 'pointer' }} onClick={() => openEditTask(task)}>
                  <td style={{ maxWidth: 240 }}>
                    <div style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {task.tarea}
                    </div>
                    {task.descripcion && (
                      <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {task.descripcion}
                      </div>
                    )}
                  </td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      {assignedUser?.photo
                        ? <img src={assignedUser.photo} alt="" style={{ width: 22, height: 22, borderRadius: '50%' }} />
                        : <div style={{
                            width: 22, height: 22, borderRadius: '50%',
                            background: assignedUser?.color || '#4700E0',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            fontSize: 10, fontWeight: 700, color: '#fff'
                          }}>
                            {(assignedUser?.name || '?')[0].toUpperCase()}
                          </div>
                      }
                      <span style={{ fontSize: 13 }}>{assignedUser?.name?.split(' ')[0] || '—'}</span>
                    </div>
                  </td>
                  <td><span className={`badge badge-${task.prioridad}`}>{task.prioridad}</span></td>
                  <td>
                    <span className={`badge badge-${task.estado}`}>
                      {stLabel}
                    </span>
                  </td>
                  <td>
                    {due ? (
                      <span style={{
                        fontSize: 11, fontWeight: 700, color: due.color,
                        background: due.bg, borderRadius: 5, padding: '2px 7px', whiteSpace: 'nowrap'
                      }}>
                        {due.label}
                      </span>
                    ) : (
                      <span style={{ fontSize: 12.5, color: 'var(--text-3)' }}>{fmtDate(task.fechaVencimiento)}</span>
                    )}
                  </td>
                  <td style={{ fontSize: 12.5, color: 'var(--text-3)' }}>{fmtDate(task.fechaInicio)}</td>
                  <td style={{ fontSize: 12.5, color: 'var(--text-3)' }}
                    title={task.completedBy ? `Completato da ${task.completedBy}` : undefined}>
                    {fmtDate(task.fechaEntrega)}
                    {task.completedBy && (
                      <div style={{ fontSize: 10.5, color: 'var(--success)' }}>✅ {task.completedBy.split(' ')[0]}</div>
                    )}
                  </td>
                  <td style={{ fontSize: 12, fontFamily: 'Space Mono' }}>
                    {fmtDuration(task.totalTimeSeconds)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    )
  )}
</div>
  )
}

// Tempo lavorato in totale de una tarea; cuenta en vivo si el timer corre
function WorkedTime({ task }) {
  const running = task.estado === 'en_progreso' && task.timerStartedAt && !task.timerPaused
  const calc = () => {
    const base = task.totalTimeSeconds || 0
    if (!running) return base
    const startMs = task.timerStartedAt.toMillis?.() || task.timerStartedAt.seconds * 1000
    return base + Math.floor((Date.now() - startMs) / 1000)
  }
  const [secs, setSecs] = useState(calc)
  useEffect(() => {
    setSecs(calc())
    if (!running) return
    const id = setInterval(() => setSecs(calc()), 30000)
    return () => clearInterval(id)
  }, [task.totalTimeSeconds, task.timerStartedAt?.seconds, task.timerPaused, task.estado])

  if (secs < 60) return null
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 99,
      display: 'inline-flex', alignItems: 'center', gap: 3,
      color: 'var(--purple)', background: 'var(--purple-light)',
    }} title="Tempo lavorato in totale su questo task">
      <Icon name="clock" size={10} /> {fmtDuration(secs)} trabajado
    </span>
  )
}

// Tarjeta compacta para tareas COMPLETADAS: mitad de tamaño, solo lo esencial
function CompactCard({ task, assignedUser, draggable, onDragStart, onEdit, canDelete, onDelete, onDuplicate }) {
  return (
    <div
      className="kanban-card"
      draggable={draggable}
      onDragStart={draggable ? e => onDragStart(e, task.id) : undefined}
      style={{ padding: '8px 10px', cursor: 'pointer' }}
      onClick={() => onEdit(task)}
      title="Clicca per vedere il dettaglio"
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ color: 'var(--success)', display: 'inline-flex' }}><Icon name="check" size={13} /></span>
        <span style={{ fontSize: 12, fontWeight: 600, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {task.tarea}
        </span>
        <button
          className="btn btn-ghost btn-sm btn-icon"
          style={{ width: 22, height: 22, padding: 0 }}
          onClick={e => { e.stopPropagation(); onDuplicate(task) }}
          title="Duplica come modello"
        >
          <Icon name="copy" size={12} />
        </button>
        {canDelete && (
          <button
            className="btn btn-ghost btn-sm btn-icon"
            style={{ width: 22, height: 22, padding: 0, color: 'var(--danger)' }}
            onClick={e => { e.stopPropagation(); onDelete(task) }}
            title="Elimina"
          >
            <Icon name="trash" size={12} />
          </button>
        )}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4, fontSize: 10.5, color: 'var(--text-3)', flexWrap: 'wrap' }}>
        <span>{assignedUser?.name?.split(' ')[0] || 'Non assegnato'}</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
          <Icon name="calendar" size={10} /> {fmtStamp(task.fechaEntrega).split(' · ')[0] || 'Senza data'}
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontFamily: 'Space Mono' }}>
          <Icon name="clock" size={10} /> {fmtDuration(task.totalTimeSeconds)}
        </span>
        {/* Valutazione del super admin */}
        {task.rating > 0 && (
          <span style={{ display: 'inline-flex', gap: 1, color: 'var(--warning)' }} title={`Valutazione: ${task.rating}/5`}>
            {Array.from({ length: task.rating }, (_, i) => <Icon key={i} name="star" size={10} filled />)}
          </span>
        )}
      </div>
    </div>
  )
}

function KanbanCard({ task, assignedUser, onEdit }) {
  const due = dueInfo(task)

  return (
    <div
      className="kanban-card"
      style={{ cursor: 'pointer', padding: '12px 14px', position: 'relative' }}
      onClick={() => onEdit(task)}
      title="Clicca per aprire i dettagli"
    >
      {/* Top Header: Centered Badges */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, marginBottom: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', justifyContent: 'center' }}>
          <span className={`badge badge-${task.prioridad}`} style={{ fontSize: 9.5, padding: '2px 8px', borderRadius: 99 }}>
            {task.prioridad}
          </span>
          {due && (
            <span style={{
              fontSize: 9.5, fontWeight: 700, color: due.color,
              background: due.bg, borderRadius: 99,
              padding: '2px 8px', display: 'inline-flex', alignItems: 'center', gap: 3
            }}>
              {due.label}
            </span>
          )}
        </div>
      </div>

      {/* Task Title - Centered */}
      <div className="kanban-card-title" style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 6, lineHeight: 1.4, textAlign: 'center', color: 'var(--text)' }}>
        {task.tarea}
      </div>

      {/* Description preview - Centered */}
      {task.descripcion && (
        <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginBottom: 8, textAlign: 'center', lineHeight: 1.45, overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
          {task.descripcion}
        </div>
      )}

      {/* Meta Row: Client + Assigned - Centered */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, flexWrap: 'wrap', fontSize: 11, color: 'var(--text-3)' }}>
        {task.cliente && (
          <span style={{ fontWeight: 600, color: 'var(--text-2)' }}>{task.cliente}</span>
        )}

        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          {assignedUser?.photo
            ? <img src={assignedUser.photo} alt="" style={{ width: 16, height: 16, borderRadius: '50%' }} />
            : <div style={{
                width: 16, height: 16, borderRadius: '50%',
                background: assignedUser?.color || 'var(--purple)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 8, fontWeight: 700, color: '#fff'
              }}>
                {(assignedUser?.name || '?')[0].toUpperCase()}
              </div>
          }
          <span>{assignedUser?.name?.split(' ')[0] || 'Non assegnato'}</span>
        </div>
      </div>
    </div>
  )
}
