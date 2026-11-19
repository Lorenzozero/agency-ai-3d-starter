import React, { useState, useEffect, useRef, useMemo } from 'react'
import { useApp, SUPER_ADMIN_EMAIL } from '../App.jsx'
import { db } from '../firebase.js'
import { doc, updateDoc, serverTimestamp, onSnapshot, collection, query, where } from 'firebase/firestore'
import { logWork, todayKey } from '../utils/worklog.js'
import { sortByPriorityAndAge, inPeriod, PeriodFilter, wipLimitReached, WIP_LIMIT } from './KanbanView.jsx'
import Icon from '../components/Icons.jsx'

function fmtSeconds(s) {
  if (!s || s <= 0) return '00:00:00'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  return [h, m, sec].map(n => String(n).padStart(2, '0')).join(':')
}

function fmtHours(s) {
  if (!s) return '0h'
  return `${(s / 3600).toFixed(1)}h`
}

function tsToMs(ts) {
  if (!ts) return 0
  return ts.toMillis ? ts.toMillis() : ts.seconds * 1000
}

function useLiveSeconds(timerStartedAt, totalTimeSeconds) {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!timerStartedAt) { setElapsed(totalTimeSeconds || 0); return }
    const base = totalTimeSeconds || 0
    const startMs = tsToMs(timerStartedAt)
    const tick = () => setElapsed(base + Math.floor((Date.now() - startMs) / 1000))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [timerStartedAt, totalTimeSeconds])
  return elapsed
}

function isOnline(u) {
  if (!u.lastSeen) return false
  return Date.now() - tsToMs(u.lastSeen) < 5 * 60 * 1000
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

const PIE_PALETTE = ['#7c3aed', '#22c55e', '#38bdf8', '#f59e0b', '#ec4899', '#14b8a6', '#a78bfa', '#f97316', '#64748b']

function fmtCompact(n) {
  if (!n || n <= 0) return '0'
  if (n >= 1000000) return `${(n / 1000000).toFixed(2)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1).replace(/\.0$/, '')}k`
  return String(n)
}

function DonutChartCard({ title, icon, slices, centerValue, centerLabel, unit = '' }) {
  const valid = (slices || []).filter(s => s.value > 0)
  const total = valid.reduce((acc, s) => acc + s.value, 0)
  const r = 48
  const c = 2 * Math.PI * r
  let accPct = 0

  return (
    <div className="card" style={{ padding: '14px 14px 12px' }}>
      <p className="section-title" style={{ margin: '0 0 12px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, fontSize: 12 }}>
        <Icon name={icon || 'trendUp'} size={13} /> {title}
      </p>

      {total === 0 ? (
        <p style={{ fontSize: 12.5, color: 'var(--text-3)', textAlign: 'center', margin: '18px 0' }}>Nessun dato nel periodo selezionato</p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
          {/* SVG Donut Chart */}
          <div style={{ position: 'relative', width: 136, height: 136, flexShrink: 0 }}>
            <svg viewBox="0 0 128 128" style={{ width: '100%', height: '100%', transform: 'rotate(-90deg)' }}>
              <circle cx="64" cy="64" r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="16" />
              {valid.map((s, idx) => {
                const frac = s.value / total
                const dash = frac * c
                const offset = -accPct * c
                accPct += frac
                return (
                  <circle
                    key={s.label + idx}
                    cx="64"
                    cy="64"
                    r={r}
                    fill="none"
                    stroke={s.color || PIE_PALETTE[idx % PIE_PALETTE.length]}
                    strokeWidth="16"
                    strokeDasharray={`${dash} ${Math.max(0, c - dash)}`}
                    strokeDashoffset={offset}
                    style={{ transition: 'stroke-dasharray 0.35s ease, stroke-dashoffset 0.35s ease' }}
                  />
                )
              })}
            </svg>
            <div style={{
              position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
              alignItems: 'center', justifyContent: 'center', textAlign: 'center', pointerEvents: 'none'
            }}>
              <span style={{ fontFamily: 'Space Mono', fontSize: 16, fontWeight: 800, color: '#f0f0ff', lineHeight: 1.1 }}>
                {centerValue !== undefined ? centerValue : fmtCompact(total)}
              </span>
              <span style={{ fontSize: 9.5, fontWeight: 700, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: '0.04em', marginTop: 2 }}>
                {centerLabel}
              </span>
            </div>
          </div>

          {/* Legenda compatta a 2 colonne adatta al mobile */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
            gap: '6px 10px',
            width: '100%'
          }}>
            {valid.map((s, idx) => {
              const pct = Math.max(1, Math.round((s.value / total) * 100))
              const col = s.color || PIE_PALETTE[idx % PIE_PALETTE.length]
              return (
                <div key={s.label + idx} style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6,
                  background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)',
                  borderRadius: 7, padding: '4px 7px', minWidth: 0
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 5, minWidth: 0, flex: 1 }}>
                    <span style={{ width: 8, height: 8, borderRadius: '50%', background: col, flexShrink: 0 }} />
                    <span style={{ fontSize: 11, fontWeight: 600, color: '#f0f0ff', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {s.label}
                    </span>
                  </div>
                  <span style={{ fontFamily: 'Space Mono', fontSize: 10.5, fontWeight: 700, color: col, flexShrink: 0 }}>
                    {fmtCompact(s.value)}{unit} ({pct}%)
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

// ============ ADMIN DASHBOARD ============
function AdminDashboard() {
  const { tasks, users, agents, openEditTask } = useApp()

  // Filtro de periodo (afecta statistiche e grafici)
  const [period, setPeriod] = useState('all')
  const [monthVal, setMonthVal] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [filterEmpleado, setFilterEmpleado] = useState('')
  const filteredTasks = tasks
    .filter(t => inPeriod(t, period, monthVal, fromDate, toDate))
    .filter(t => !filterEmpleado || t.asignado === filterEmpleado)

  const activeTasks = tasks.filter(t => t.estado === 'en_progreso')
  const activeAgentsCount = agents.filter(a => a.active !== false).length
  const getUserById = (uid) => users.find(u => u.uid === uid)
    || ((a) => a && { uid: a.id, name: `${a.name} (agente)`, color: a.look?.torso, photo: a.photo || '' })(agents.find(a => a.id === uid))

  const totals = {
    pendiente: filteredTasks.filter(t => t.estado === 'pendiente').length,
    en_progreso: filteredTasks.filter(t => t.estado === 'en_progreso').length,
    en_espera: filteredTasks.filter(t => t.estado === 'en_espera').length,
    en_revision: filteredTasks.filter(t => t.estado === 'en_revision').length,
    completada: filteredTasks.filter(t => t.estado === 'completada').length,
  }

  // 1. Fette grafico a torta Stato Task
  const taskSlices = [
    { label: 'Completati', value: totals.completada, color: '#22c55e' },
    { label: 'Da fare',    value: totals.pendiente,  color: '#a78bfa' },
    { label: 'In corso',   value: totals.en_progreso, color: '#7c3aed' },
    { label: 'In attesa',  value: totals.en_espera,   color: '#38bdf8' },
    { label: 'Correzioni', value: totals.en_revision, color: '#f59e0b' },
  ]

  // 2. Fette grafico a torta Produttività & Azioni Reali per Agente
  const actionSlices = useMemo(() => {
    const sorted = [...agents]
      .map(a => {
        const taskCount = filteredTasks.filter(t => t.asignado === a.id && t.estado === 'completada').length
        const act = Number(a.actionsCount || 0) + taskCount
        return { label: a.name, value: act }
      })
      .filter(x => x.value > 0)
      .sort((a, b) => b.value - a.value)
    if (sorted.length <= 6) return sorted.map((s, i) => ({ ...s, color: PIE_PALETTE[i % PIE_PALETTE.length] }))
    const top = sorted.slice(0, 5).map((s, i) => ({ ...s, color: PIE_PALETTE[i] }))
    const rest = sorted.slice(5).reduce((acc, s) => acc + s.value, 0)
    return [...top, { label: 'Altri agenti', value: rest, color: '#64748b' }]
  }, [agents, filteredTasks])

  // 3. Fette grafico a torta Consumo Token Reali per Agente
  const tokenSlices = useMemo(() => {
    const sorted = [...agents]
      .map(a => ({ label: a.name, value: Number(a.tokensUsed || 0) }))
      .filter(x => x.value > 0)
      .sort((a, b) => b.value - a.value)
    if (sorted.length <= 6) return sorted.map((s, i) => ({ ...s, color: PIE_PALETTE[i % PIE_PALETTE.length] }))
    const top = sorted.slice(0, 5).map((s, i) => ({ ...s, color: PIE_PALETTE[i] }))
    const rest = sorted.slice(5).reduce((acc, s) => acc + s.value, 0)
    return [...top, { label: 'Altri agenti', value: rest, color: '#64748b' }]
  }, [agents])

  const totalActions = actionSlices.reduce((acc, s) => acc + s.value, 0)
  const totalTokens = tokenSlices.reduce((acc, s) => acc + s.value, 0)

  return (
    <div>
      <div className="page-header" style={{ marginBottom: 10, display: 'flex', justifyContent: 'center' }}>
        <PeriodFilter period={period} setPeriod={setPeriod} />
      </div>

      {/* 6 Riquadri Statistiche — 3 per riga su mobile e desktop */}
      <div className="stats-grid" style={{ marginBottom: 12 }}>
        {[
          { icon: 'wifi', value: activeAgentsCount, label: 'Agenti Attivi', color: 'var(--success)' },
          {
            icon: 'check',
            value: (
              <span>
                <span style={{ color: 'var(--success)' }}>{totals.completada}</span>
                <span style={{ color: 'var(--text-3)', fontSize: '0.8em', margin: '0 3px' }}>/</span>
                <span style={{ color: 'var(--text-2)' }}>{filteredTasks.length}</span>
              </span>
            ),
            label: 'Fatti / Totali',
            color: 'var(--success)'
          },
          { icon: 'hourglass', value: totals.pendiente, label: 'Da fare', color: 'var(--text-3)' },
          { icon: 'timer', value: totals.en_progreso, label: 'In corso', color: 'var(--purple)' },
          { icon: 'clock', value: totals.en_espera, label: 'In attesa', color: 'var(--info)' },
          { icon: 'wrench', value: totals.en_revision, label: 'Correzioni', color: 'var(--warning)' },
        ].map((s, i) => (
          <div className="stat-card" key={i}>
            <span style={{ color: s.color }}><Icon name={s.icon} size={18} /></span>
            <span className="stat-value">{s.value}</span>
            <span className="stat-label">{s.label}</span>
          </div>
        ))}
      </div>

      {/* Timer attivi adesso */}
      {activeTasks.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <p className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Icon name="timer" size={13} /> Timer attivi adesso
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {activeTasks.map(task => (
              <ActiveTimerRow key={task.id} task={task}
                assignedUser={getUserById(task.asignado)}
                onEdit={() => openEditTask(task)} />
            ))}
          </div>
        </div>
      )}

      {/* Grafici a Torta (Donut Charts) Mobile-First con Dati Reali */}
      <div className="metrics-grid" style={{ gap: 12 }}>
        <DonutChartCard
          title="Distribuzione Task"
          icon="columns"
          slices={taskSlices}
          centerValue={`${totals.completada}/${filteredTasks.length}`}
          centerLabel="Completati"
        />
        <DonutChartCard
          title="Produttività & Azioni Squadra"
          icon="trendUp"
          slices={actionSlices}
          centerValue={fmtCompact(totalActions)}
          centerLabel="Azioni"
        />
        <DonutChartCard
          title="Consumo Token per Agente"
          icon="star"
          slices={tokenSlices}
          centerValue={fmtCompact(totalTokens)}
          centerLabel="Token"
        />
      </div>
    </div>
  )
}

function UserAvatar({ u, online = false, size = 32 }) {
  return (
    <div style={{ position: 'relative', flexShrink: 0 }}>
      {u.photo
        ? <img src={u.photo} alt="" style={{ width: size, height: size, borderRadius: '50%' }} />
        : <div style={{
            width: size, height: size, borderRadius: '50%',
            background: u.color || '#4700E0',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: size * 0.4, fontWeight: 700, color: '#fff'
          }}>
            {(u.name || '?')[0].toUpperCase()}
          </div>
      }
      {online && (
        <div style={{
          position: 'absolute', bottom: 0, right: 0,
          width: size * 0.3, height: size * 0.3, borderRadius: '50%',
          background: 'var(--success)', border: '2px solid var(--card)'
        }} />
      )}
    </div>
  )
}

function ActiveTimerRow({ task, assignedUser, onEdit }) {
  const isPaused = task.timerPaused === true
  const elapsed = useLiveSeconds(isPaused ? null : task.timerStartedAt, task.totalTimeSeconds)

  return (
    <div className="card card-sm" style={{ display: 'flex', alignItems: 'center', gap: 14, cursor: 'pointer' }} onClick={onEdit}>
      {assignedUser && <UserAvatar u={assignedUser} size={32} />}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 2 }}>{task.tarea}</div>
        <div style={{ fontSize: 12, color: 'var(--text-3)' }}>{assignedUser?.name || '—'} · {task.cliente}</div>
      </div>
      <div className="timer-display" style={{
        opacity: isPaused ? 0.7 : 1,
        background: isPaused ? 'rgba(239,68,68,0.1)' : undefined,
        color: isPaused ? 'var(--danger)' : undefined,
        borderColor: isPaused ? 'rgba(239,68,68,0.3)' : undefined,
      }}>
        {!isPaused && <span className="timer-dot" />}
        {isPaused && <span style={{ fontSize: 10 }}>⏸</span>}
        {fmtSeconds(elapsed)}
      </div>
      <span className={`badge badge-${task.prioridad}`}>{task.prioridad}</span>
    </div>
  )
}

// ============ EMPLOYEE DASHBOARD ============
const WELCOME_MESSAGES = [
  (n) => `Benvenuto ${n}! Oggi sarà una gran giornata`,
  (n) => `Ciao ${n}, la tua produttività continua a crescere. Avanti così!`,
  (n) => `Buongiorno ${n}! Ogni task completato ti avvicina ai tuoi obiettivi`,
  (n) => `${n}, oggi è un gran giorno per fare cose incredibili`,
  (n) => `Ciao ${n}! Respira, concentrati e dai il massimo`,
  (n) => `${n}, il tuo impegno di ogni giorno costruisce qualcosa di grande`,
  (n) => `Bentornato ${n}! La squadra conta su di te`,
  (n) => `${n}, la costanza è il tuo superpotere. Oggi si brilla!`,
  (n) => `Ciao ${n}! Un passo alla volta, un task alla volta`,
  (n) => `${n}, oggi è perfetto per battere il tuo record`,
]

function dailyWelcome(name) {
  // Mensaje distinto cada día, estable durante el día
  const dayNumber = Math.floor(Date.now() / 86400000)
  const msg = WELCOME_MESSAGES[dayNumber % WELCOME_MESSAGES.length]
  return msg(name)
}

// Frases motivadoras — rotan cada día, distintas del saludo
const MOTIVATIONAL_QUOTES = [
  '"Il successo è la somma di piccoli sforzi ripetuti giorno dopo giorno." · Robert Collier',
  '"Non contare i giorni, fai in modo che i giorni contino." · Muhammad Ali',
  '"La disciplina è il ponte tra gli obiettivi e i risultati." · Jim Rohn',
  '"Fallo con passione o non farlo." · Rosa Nouchette',
  '"L\'unico modo per fare un ottimo lavoro è amare ciò che fai." · Steve Jobs',
  '"La creatività è l\'intelligenza che si diverte." · Albert Einstein',
  '"Non aspettare il momento perfetto: prendi il momento e rendilo perfetto."',
  '"I grandi risultati richiedono tempo. Fidati del tuo percorso."',
  '"Ogni task completato è un passo verso la tua versione migliore."',
  '"Il talento vince le partite, ma il gioco di squadra vince i campionati." · Michael Jordan',
  '"Concentrati sul progresso, non sulla perfezione."',
  '"Il tuo atteggiamento, non la tua attitudine, determinerà la tua altezza." · Zig Ziglar',
  '"Un piccolo progresso ogni giorno porta grandi risultati."',
  '"Lavora sodo in silenzio, lascia che sia il successo a far rumore." · Frank Ocean',
]

function dailyQuote() {
  const dayNumber = Math.floor(Date.now() / 86400000)
  return MOTIVATIONAL_QUOTES[dayNumber % MOTIVATIONAL_QUOTES.length]
}

const DAILY_QUOTA_HOURS = 8

function EmployeeDashboard() {
  const { user, userProfile, tasks, showToast, sendNotification, openEditTask, openNewTask } = useApp()
  const notifiedTimers = useRef(new Set())

  // Ore lavorate oggi (worklog en tiempo real)
  const [todayLoggedSeconds, setTodayLoggedSeconds] = useState(0)
  useEffect(() => {
    const unsub = onSnapshot(doc(db, 'worklog', `${user.uid}_${todayKey()}`), snap => {
      setTodayLoggedSeconds(snap.exists() ? (snap.data().seconds || 0) : 0)
    })
    return unsub
  }, [user.uid])

  const myTasks = tasks.filter(t => t.asignado === user.uid)
  const myActive = myTasks.find(t => t.estado === 'en_progreso')
  const myPending = sortByPriorityAndAge(myTasks.filter(t => t.estado === 'pendiente'))
  const myReview = myTasks.filter(t => t.estado === 'en_revision')
  const myWaiting = myTasks.filter(t => t.estado === 'en_espera')
  const myDone = myTasks.filter(t => t.estado === 'completada')

  const weekAgo = Date.now() - 7 * 24 * 3600 * 1000
  const doneWeek = myDone.filter(t => tsToMs(t.fechaEntrega) > weekAgo).length

  const completedWithTime = myDone.filter(t => t.totalTimeSeconds > 0)
  const avgHours = completedWithTime.length > 0
    ? (completedWithTime.reduce((acc, t) => acc + (t.totalTimeSeconds || 0), 0) / completedWithTime.length / 3600).toFixed(1)
    : null

  useEffect(() => {
    if (!myActive?.timerStartedAt) return
    const key = `${myActive.id}-${myActive.timerStartedAt?.seconds}`
    if (notifiedTimers.current.has(key)) return
    notifiedTimers.current.add(key)
    const age = Date.now() - tsToMs(myActive.timerStartedAt)
    if (age < 60000) {
      showToast(`⏱️ Timer avviato! "${myActive.tarea}"`, 'info')
      sendNotification('⏱️ Il tempo sta scorrendo!', `Task: "${myActive.tarea}"`)
    }
  }, [myActive?.timerStartedAt?.seconds])

  const isPaused = myActive?.timerPaused === true
  const elapsed = useLiveSeconds(isPaused ? null : myActive?.timerStartedAt, myActive?.totalTimeSeconds)

  // Aviso de tiempo excedido: si supera el tiempo estimado de la tarea
  const estimateSecs = (myActive?.tiempoEstimadoMin || 0) * 60
  const overEstimate = estimateSecs > 0 && elapsed > estimateSecs
  const overrunNotified = useRef(new Set())
  useEffect(() => {
    if (!overEstimate || !myActive) return
    if (overrunNotified.current.has(myActive.id)) return
    overrunNotified.current.add(myActive.id)
    showToast(`Stai impiegando più tempo del previsto (${myActive.tiempoEstimadoMin} min) su "${myActive.tarea}"`, 'warning')
    sendNotification('Tempo superato', `"${myActive.tarea}" ha superato il tempo previsto di ${myActive.tiempoEstimadoMin} min`)
  }, [overEstimate, myActive?.id])

  // Sesión en curso (solo lo que corre AHORA, sin lo acumulado de la tarea)
  const currentSessionSeconds = useLiveSeconds(isPaused ? null : myActive?.timerStartedAt, 0)
  const todayTotalSeconds = todayLoggedSeconds + (myActive && !isPaused ? currentSessionSeconds : 0)
  const todayHours = todayTotalSeconds / 3600
  const quotaPct = Math.min(100, Math.round((todayHours / DAILY_QUOTA_HOURS) * 100))
  const quotaMet = todayHours >= DAILY_QUOTA_HOURS

  // Tareas devueltas por el admin esperando mi aceptación
  const pendingAccept = myTasks.filter(t => t.pendingAcceptance)

  const handleAcceptTask = async (task) => {
    try {
      await updateDoc(doc(db, 'tasks', task.id), {
        pendingAcceptance: false,
        timerPaused: false,
        timerStartedAt: serverTimestamp(),
        lastEditedBy: user.email,
        lastEditedAt: serverTimestamp(),
      })
      showToast(`Hai accettato "${task.tarea}". Timer in corso!`, 'info')
      sendNotification('⏱️ Timer avviato', `Hai accettato: "${task.tarea}"`)
    } catch (e) {
      console.error(e)
      const detail = e?.code === 'permission-denied' ? 'sin permisos' : (e?.code || '')
      showToast(`Impossibile accettare il task${detail ? ` (${detail})` : ''}`, 'error')
    }
  }

  const handleTakeTask = async (task) => {
    if (wipLimitReached(tasks, user.uid, task.id)) {
      showToast(`Al massimo ${WIP_LIMIT} task In corso. Finiscine uno o rimettilo in Da fare prima di prenderne un altro`, 'error')
      return
    }
    const updates = {
      estado: 'en_progreso',
      timerStartedAt: serverTimestamp(),
      timerPaused: false,
      lastEditedBy: user.email,
      lastEditedAt: serverTimestamp(),
    }
    if (!task.fechaInicio) updates.fechaInicio = serverTimestamp()
    await updateDoc(doc(db, 'tasks', task.id), updates)
    showToast(`Hai preso "${task.tarea}". Timer avviato!`, 'info')
    sendNotification('⏱️ Timer avviato!', `Hai iniziato: "${task.tarea}"`)
  }

  const handlePauseResume = async (task) => {
    if (!task.timerPaused) {
      const startMs = tsToMs(task.timerStartedAt)
      const elapsedNow = startMs ? Math.floor((Date.now() - startMs) / 1000) : 0
      await updateDoc(doc(db, 'tasks', task.id), {
        timerPaused: true,
        timerStartedAt: null,
        totalTimeSeconds: (task.totalTimeSeconds || 0) + elapsedNow,
        lastEditedBy: user.email,
        lastEditedAt: serverTimestamp(),
      })
      logWork(task.asignado, elapsedNow)
      showToast('⏸ Timer in pausa')
    } else {
      await updateDoc(doc(db, 'tasks', task.id), {
        timerPaused: false,
        timerStartedAt: serverTimestamp(),
        lastEditedBy: user.email,
        lastEditedAt: serverTimestamp(),
      })
      showToast('▶ Timer ripreso')
    }
  }

  return (
    <div>
      <div className="page-header" style={{ marginBottom: 10, display: 'flex', justifyContent: 'center' }}>
        <button className="btn btn-primary btn-sm" onClick={openNewTask}>+ Nuovo task</button>
      </div>

      {/* Frase motivadora del día */}
      <div className="motivation-card">
        <span className="quote-icon">💜</span>
        <span className="quote-text">{dailyQuote()}</span>
      </div>

      {/* Ore lavorate oggi */}
      <div className="card" style={{ marginBottom: 20, padding: '16px 20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, flexWrap: 'wrap', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ color: quotaMet ? 'var(--success)' : 'var(--purple)' }}>
              <Icon name={quotaMet ? 'trophy' : 'clock'} size={22} />
            </span>
            <div>
              <div style={{ fontWeight: 700, fontSize: 14 }}>Ore lavorate oggi</div>
              <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
                {quotaMet
                  ? 'Obiettivo giornaliero raggiunto! Ottimo lavoro'
                  : `Obiettivo giornaliero: ${DAILY_QUOTA_HOURS}h · Ti mancano ${(DAILY_QUOTA_HOURS - todayHours).toFixed(1)}h`}
              </div>
            </div>
          </div>
          <div style={{ fontFamily: 'Space Mono', fontSize: 24, fontWeight: 700, color: quotaMet ? 'var(--success)' : 'var(--purple)' }}>
            {todayHours.toFixed(1)}h
            <span style={{ fontSize: 13, color: 'var(--text-3)' }}> / {DAILY_QUOTA_HOURS}h</span>
          </div>
        </div>
        <div className="bar-track" style={{ height: 10 }}>
          <div className="bar-fill" style={{
            width: `${quotaPct}%`,
            background: quotaMet ? 'var(--success)' : 'var(--purple)',
            transition: 'width 1s ease'
          }} />
        </div>
      </div>

      {/* Popup: tareas devueltas por el admin */}
      {pendingAccept.map(task => (
        <div key={task.id} className="card accept-task-popup" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
          <div style={{ color: 'var(--purple)' }}><Icon name="bell" size={30} /></div>
          <div style={{ flex: 1, minWidth: 200 }}>
            <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 2 }}>
              Ti hanno restituito un task
            </div>
            <div style={{ fontSize: 13, color: 'var(--text-2)' }}>
              <strong>{task.returnedBy || 'L\'amministratore'}</strong> puso "{task.tarea}" de nuevo In corso.
              El timer NO corre hasta que la aceptes.
            </div>
          </div>
          <button className="btn btn-primary" onClick={() => handleAcceptTask(task)}>
            <Icon name="check" size={14} /> Accetta e inizia
          </button>
        </div>
      ))}

      {myActive ? (
        <div className="active-task-banner" style={{
          marginBottom: 24,
          background: isPaused
            ? 'linear-gradient(135deg, #b91c1c, #ef4444)'
            : undefined,
        }}>
          <div>
            <div className="timer-big">{fmtSeconds(elapsed)}</div>
            <div style={{ fontSize: 11, opacity: 0.75, marginTop: 4, textAlign: 'center' }}>
              {isPaused ? 'IN PAUSA' : 'tiempo activo'}
            </div>
            {estimateSecs > 0 && (
              <div style={{
                fontSize: 10.5, marginTop: 4, textAlign: 'center', fontWeight: 700,
                color: overEstimate ? '#ffd6d6' : 'rgba(255,255,255,0.75)',
                background: overEstimate ? 'rgba(0,0,0,0.25)' : 'transparent',
                borderRadius: 6, padding: '2px 6px'
              }}>
                {overEstimate ? 'TEMPO SUPERATO' : `Estimado: ${myActive.tiempoEstimadoMin} min`}
              </div>
            )}
          </div>
          <div className="timer-info" style={{ flex: 1, minWidth: 0 }}>
            <h3 style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{myActive.tarea}</h3>
            <p>{myActive.cliente}</p>
            {!isPaused && (
              <div className="timer-running-badge">
                <span style={{ animation: 'blink 1s infinite', display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: '#fff' }} />
                Timer corriendo
              </div>
            )}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="btn btn-sm"
              onClick={() => handlePauseResume(myActive)}
              style={{ background: 'rgba(255,255,255,0.2)', color: '#fff', border: '1px solid rgba(255,255,255,0.3)', minWidth: 42 }}
              title={isPaused ? 'Riprendi timer' : 'Metti in pausa il timer'}
            >
              <Icon name={isPaused ? 'play' : 'pause'} size={14} />
            </button>
            <button
              className="btn btn-sm"
              onClick={() => openEditTask(myActive)}
              style={{ background: 'rgba(255,255,255,0.15)', color: '#fff', border: '1px solid rgba(255,255,255,0.3)' }}
            >
              Vedi task
            </button>
          </div>
        </div>
      ) : (
        <div className="card" style={{ marginBottom: 24, textAlign: 'center', padding: '24px', borderStyle: 'dashed' }}>
          <div style={{ marginBottom: 8, color: 'var(--text-3)' }}><Icon name="target" size={32} /></div>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Nessun task attivo</div>
          <div style={{ fontSize: 13, color: 'var(--text-3)' }}>Prendi un task da fare per avviare il timer</div>
        </div>
      )}

      <div className="stats-grid" style={{ marginBottom: 28 }}>
        <div className="stat-card">
          <span style={{ color: 'var(--text-3)' }}><Icon name="hourglass" size={20} /></span>
          <span className="stat-value">{myPending.length}</span>
          <span className="stat-label">Da fare</span>
        </div>
        <div className="stat-card">
          <span style={{ color: 'var(--info)' }}><Icon name="clock" size={20} /></span>
          <span className="stat-value" style={{ color: 'var(--info)' }}>{myWaiting.length}</span>
          <span className="stat-label">In attesa di approvazione</span>
        </div>
        <div className="stat-card">
          <span style={{ color: 'var(--warning)' }}><Icon name="wrench" size={20} /></span>
          <span className="stat-value" style={{ color: 'var(--warning)' }}>{myReview.length}</span>
          <span className="stat-label">Correzioni</span>
        </div>
        <div className="stat-card">
          <span style={{ color: 'var(--success)' }}><Icon name="check" size={20} /></span>
          <span className="stat-value" style={{ color: 'var(--success)' }}>{doneWeek}</span>
          <span className="stat-label">Questa settimana</span>
        </div>
        {avgHours && (
          <div className="stat-card">
            <span style={{ color: 'var(--purple)' }}><Icon name="timer" size={20} /></span>
            <span className="stat-value">{avgHours}h</span>
            <span className="stat-label">Media/task</span>
          </div>
        )}
      </div>

      {/* ===== Le mie metriche: panel personal con comparativas ===== */}
      <MyMetricsPanel myTasks={myTasks} myDone={myDone} myPending={myPending} myReview={myReview} />

      {myPending.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <p className="section-title">I tuoi task da fare ({myPending.length})</p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {myPending.slice(0, 6).map(task => (
              <div key={task.id} className="card card-sm" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{ flex: 1, minWidth: 0, cursor: 'pointer' }} onClick={() => openEditTask(task)}>
                  <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{task.tarea}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-3)' }}>{task.cliente}</div>
                </div>
                <span className={`badge badge-${task.prioridad}`}>{task.prioridad}</span>
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => handleTakeTask(task)}
                  disabled={!!myActive}
                  title={myActive ? 'Prima termina o metti in pausa il task attivo' : 'Prendi questo task e avvia il timer'}
                  style={{ minWidth: 88, opacity: myActive ? 0.5 : 1 }}
                >
                  <Icon name="play" size={12} /> Prendi
                </button>
              </div>
            ))}
            {myPending.length > 6 && (
              <div style={{ textAlign: 'center', fontSize: 13, color: 'var(--text-3)', paddingTop: 4 }}>
                +{myPending.length - 6} más · ve a "Task" para ver todas
              </div>
            )}
          </div>
        </div>
      )}

      {myReview.length > 0 && (
        <div>
          <p className="section-title">🔧 Correzioni richieste</p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {myReview.map(task => (
              <div key={task.id} className="card card-sm" style={{ display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer' }} onClick={() => openEditTask(task)}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{task.tarea}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-3)' }}>{task.cliente}</div>
                </div>
                <span className={`badge badge-${task.prioridad}`}>{task.prioridad}</span>
                <span className="badge badge-en_revision">Correzioni</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ===== Informe mensual detallado por empleado (Super admin) =====
function MonthlyReport({ tasks, employees }) {
  const nowMonth = () => {
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  }
  const [month, setMonth] = useState(nowMonth())
  const [monthLogs, setMonthLogs] = useState([])

  // Horas de conexión del mes: rango sobre el campo date (YYYY-MM-DD)
  useEffect(() => {
    if (!month) return
    const q = query(
      collection(db, 'worklog'),
      where('date', '>=', `${month}-01`),
      where('date', '<=', `${month}-31`)
    )
    const unsub = onSnapshot(q, snap => setMonthLogs(snap.docs.map(d => d.data())))
    return unsub
  }, [month])

  const [y, m] = month.split('-').map(Number)
  const monthStart = new Date(y, m - 1, 1).getTime()
  const monthEnd = new Date(y, m, 1).getTime()
  const inMonth = (ts) => { const ms = tsToMs(ts); return ms >= monthStart && ms < monthEnd }

  const rows = employees.map(u => {
    const done = tasks.filter(t => t.asignado === u.uid && t.estado === 'completada' && inMonth(t.fechaEntrega))
    const totalTime = done.reduce((a, t) => a + (t.totalTimeSeconds || 0), 0)
    const withTime = done.filter(t => t.totalTimeSeconds > 0)
    const avgTime = withTime.length ? totalTime / withTime.length : 0

    // Cumplimiento de tiempo estimado
    const withEstimate = done.filter(t => t.tiempoEstimadoMin > 0)
    const overTime = withEstimate.filter(t => (t.totalTimeSeconds || 0) > t.tiempoEstimadoMin * 60).length
    const underTime = withEstimate.length - overTime

    // Cumplimiento de fechas de vencimiento
    const withDeadline = done.filter(t => t.fechaVencimiento)
    const late = withDeadline.filter(t => tsToMs(t.fechaEntrega) > tsToMs(t.fechaVencimiento)).length
    const onTime = withDeadline.length - late

    // Inicio a tiempo: fechaInicio vs fechaRequerimiento
    const withReq = done.filter(t => t.fechaRequerimiento && t.fechaInicio)
    const startedLate = withReq.filter(t => tsToMs(t.fechaInicio) > tsToMs(t.fechaRequerimiento)).length

    // Prioridad: urgentes/altas entregadas tarde
    const priorityMissed = done.filter(t =>
      ['urgente', 'alta'].includes(t.prioridad) &&
      t.fechaVencimiento && tsToMs(t.fechaEntrega) > tsToMs(t.fechaVencimiento)
    ).length

    // Conexión del mes
    const onlineSecs = monthLogs.filter(l => l.uid === u.uid).reduce((a, l) => a + (l.onlineSeconds || 0), 0)

    // Valutazione promedio
    const rated = done.filter(t => t.rating > 0)
    const avgRating = rated.length ? (rated.reduce((a, t) => a + t.rating, 0) / rated.length) : 0

    return { u, done: done.length, totalTime, avgTime, overTime, underTime, withEstimate: withEstimate.length,
      onTime, late, withDeadline: withDeadline.length, startedLate, withReq: withReq.length, priorityMissed, onlineSecs, avgRating }
  }).sort((a, b) => b.done - a.done)

  return (
    <div className="card" style={{ marginBottom: 24, padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        <p className="section-title" style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
          <Icon name="calendar" size={13} /> Rapporto mensile per collaboratore
        </p>
        <input type="month" className="form-input" style={{ width: 'auto', padding: '5px 10px', fontSize: 13 }}
          value={month} onChange={e => setMonth(e.target.value)} />
      </div>
      <div className="table-wrap" style={{ border: 'none', borderRadius: 0 }}>
        <table>
          <thead>
            <tr>
              <th>Collaboratore</th>
              <th style={{ textAlign: 'center' }}>Connessione</th>
              <th style={{ textAlign: 'center' }}>Completati</th>
              <th style={{ textAlign: 'center' }}>Tempo totale</th>
              <th style={{ textAlign: 'center' }}>Media/task</th>
              <th style={{ textAlign: 'center' }}>Tempo stimato</th>
              <th style={{ textAlign: 'center' }}>Date di consegna</th>
              <th style={{ textAlign: 'center' }}>Inizio in ritardo</th>
              <th style={{ textAlign: 'center' }}>Priorità non rispettata</th>
              <th style={{ textAlign: 'center' }}>Valutazione</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.u.uid}>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <UserAvatar u={r.u} size={26} />
                    <span style={{ fontSize: 13, fontWeight: 600 }}>{r.u.name}</span>
                  </div>
                </td>
                <td style={{ textAlign: 'center', fontFamily: 'Space Mono', fontSize: 12, color: 'var(--info)' }}>{fmtHours(r.onlineSecs)}</td>
                <td style={{ textAlign: 'center', fontFamily: 'Space Mono', fontWeight: 700, color: 'var(--purple)' }}>{r.done}</td>
                <td style={{ textAlign: 'center', fontFamily: 'Space Mono', fontSize: 12 }}>{fmtHours(r.totalTime)}</td>
                <td style={{ textAlign: 'center', fontFamily: 'Space Mono', fontSize: 12 }}>{fmtHours(r.avgTime)}</td>
                <td style={{ textAlign: 'center', fontSize: 12 }}>
                  {r.withEstimate === 0 ? <span style={{ color: 'var(--text-3)' }}>Nessun dato</span> : (
                    <>
                      <span style={{ color: 'var(--success)', fontWeight: 700 }}>{r.underTime} ok</span>
                      {' · '}
                      <span style={{ color: r.overTime > 0 ? 'var(--danger)' : 'var(--text-3)', fontWeight: 700 }}>{r.overTime} excedidas</span>
                    </>
                  )}
                </td>
                <td style={{ textAlign: 'center', fontSize: 12 }}>
                  {r.withDeadline === 0 ? <span style={{ color: 'var(--text-3)' }}>Nessun dato</span> : (
                    <>
                      <span style={{ color: 'var(--success)', fontWeight: 700 }}>{r.onTime} a tiempo</span>
                      {' · '}
                      <span style={{ color: r.late > 0 ? 'var(--danger)' : 'var(--text-3)', fontWeight: 700 }}>{r.late} tarde</span>
                    </>
                  )}
                </td>
                <td style={{ textAlign: 'center', fontSize: 12, color: r.startedLate > 0 ? 'var(--warning)' : 'var(--text-3)' }}>
                  {r.withReq === 0 ? 'Nessun dato' : `${r.startedLate} de ${r.withReq}`}
                </td>
                <td style={{ textAlign: 'center', fontSize: 12, color: r.priorityMissed > 0 ? 'var(--danger)' : 'var(--text-3)', fontWeight: 700 }}>
                  {r.priorityMissed}
                </td>
                <td style={{ textAlign: 'center' }}>
                  {r.avgRating > 0 ? (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, color: 'var(--warning)', fontFamily: 'Space Mono', fontSize: 12, fontWeight: 700 }}>
                      <Icon name="star" size={12} filled /> {r.avgRating.toFixed(1)}
                    </span>
                  ) : <span style={{ fontSize: 12, color: 'var(--text-3)' }}>Senza valutazione</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ===== Panel de métricas personales con comparativas semana/mes =====
function MyMetricsPanel({ myTasks, myDone, myPending, myReview }) {
  const now = new Date()
  const weekStart = new Date(now); weekStart.setDate(now.getDate() - now.getDay()); weekStart.setHours(0, 0, 0, 0)
  const lastWeekStart = new Date(weekStart); lastWeekStart.setDate(weekStart.getDate() - 7)
  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1)
  const lastMonthStart = new Date(now.getFullYear(), now.getMonth() - 1, 1)

  const doneIn = (from, to) => myDone.filter(t => {
    const ms = tsToMs(t.fechaEntrega)
    return ms >= from.getTime() && (!to || ms < to.getTime())
  })

  const thisWeek = doneIn(weekStart)
  const lastWeek = doneIn(lastWeekStart, weekStart)
  const thisMonth = doneIn(monthStart)
  const lastMonth = doneIn(lastMonthStart, monthStart)

  const sumHours = (list) => list.reduce((a, t) => a + (t.totalTimeSeconds || 0), 0) / 3600
  const hoursWeek = sumHours(thisWeek)
  const withTime = myDone.filter(t => t.totalTimeSeconds > 0)
  const avgH = withTime.length ? sumHours(withTime) / withTime.length : 0

  const weekDiff = thisWeek.length - lastWeek.length
  const monthDiff = thisMonth.length - lastMonth.length

  const Metric = ({ icon, value, label, color }) => (
    <div className="stat-card" style={{ padding: 14 }}>
      <span style={{ color: color || 'var(--text-2)' }}><Icon name={icon} size={20} /></span>
      <span className="stat-value" style={{ fontSize: 22, color }}>{value}</span>
      <span className="stat-label" style={{ fontSize: 10.5 }}>{label}</span>
    </div>
  )

  return (
    <div className="card" style={{ marginBottom: 24 }}>
      <p className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <Icon name="target" size={14} /> Le mie metriche
      </p>

      {/* Comparativa semanal */}
      {(lastWeek.length > 0 || thisWeek.length > 0) && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px',
          borderRadius: 10, marginBottom: 16,
          background: weekDiff >= 0 ? 'rgba(34,197,94,0.08)' : 'rgba(245,158,11,0.08)',
          border: `1px solid ${weekDiff >= 0 ? 'rgba(34,197,94,0.25)' : 'rgba(245,158,11,0.3)'}`,
        }}>
          <span style={{ color: weekDiff >= 0 ? 'var(--success)' : 'var(--warning)' }}>
            <Icon name={weekDiff >= 0 ? 'trendUp' : 'trendDown'} size={26} />
          </span>
          <div style={{ fontSize: 13, lineHeight: 1.5 }}>
            {weekDiff > 0 && (
              <><strong>Ottimo lavoro!</strong> Questa settimana hai superato la produttività della settimana scorsa
              ({thisWeek.length} vs {lastWeek.length} task completati).</>
            )}
            {weekDiff === 0 && (
              <><strong>Stai andando allo stesso ritmo</strong> della settimana scorsa ({thisWeek.length} task). Ancora uno sforzo e la superi!</>
            )}
            {weekDiff < 0 && (
              <><strong>Questa settimana la tua produttività è stata inferiore alla scorsa</strong> ({thisWeek.length} vs {lastWeek.length}).
              Controlla le tue metriche e individua dove migliorare.</>
            )}
          </div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 12 }}>
        <Metric icon="check" value={thisWeek.length} label="Completati in settimana" color="var(--success)" />
        <Metric icon="checkSquare" value={thisMonth.length} label="Completati nel mese" color="var(--success)" />
        <Metric icon="clock" value={`${hoursWeek.toFixed(1)}h`} label="Ore questa settimana" color="var(--purple)" />
        <Metric icon="timer" value={`${avgH.toFixed(1)}h`} label="Media per task" />
        <Metric icon="hourglass" value={myPending.length} label="Da fare" color="var(--text-2)" />
        <Metric icon="wrench" value={myReview.length} label="In correzione" color="var(--warning)" />
      </div>

      {/* Comparativa mensual */}
      <div style={{ display: 'flex', gap: 16, marginTop: 14, fontSize: 12, color: 'var(--text-3)', flexWrap: 'wrap' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <span style={{ color: weekDiff >= 0 ? 'var(--success)' : 'var(--warning)' }}>
            <Icon name={weekDiff >= 0 ? 'trendUp' : 'trendDown'} size={13} />
          </span>
          Semana: {thisWeek.length} vs {lastWeek.length} anterior
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <span style={{ color: monthDiff >= 0 ? 'var(--success)' : 'var(--warning)' }}>
            <Icon name={monthDiff >= 0 ? 'trendUp' : 'trendDown'} size={13} />
          </span>
          Mes: {thisMonth.length} vs {lastMonth.length} anterior
        </span>
      </div>
    </div>
  )
}

export default function Dashboard() {
  const { isAdmin } = useApp()
  return isAdmin ? <AdminDashboard /> : <EmployeeDashboard />
}
