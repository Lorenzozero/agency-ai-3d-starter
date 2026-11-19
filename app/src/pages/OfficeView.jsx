import React, { useState, useEffect, useRef, useMemo } from 'react'
import { useApp } from '../App.jsx'
import { db } from '../firebase.js'
import { doc, updateDoc, addDoc, deleteDoc, collection, serverTimestamp, Timestamp, onSnapshot } from 'firebase/firestore'
import { OfficeScene } from '../office/scene.js'
import Icon from '../components/Icons.jsx'
import AgentModal, { AgentAvatar } from '../components/AgentModal.jsx'
import AvatarModal from '../components/AvatarModal.jsx'
import AgentChat from '../components/AgentChat.jsx'
import { userLook } from '../components/LookEditor.jsx'
import { BRAND, SUPER_ADMIN_EMAIL } from '../config.js'
import { wipLimitReached, WIP_LIMIT } from './KanbanView.jsx'

const ONLINE_MS = 5 * 60 * 1000
// El coordinador (asistente personal) tiene escritorio propio y reparte el trabajo
const isCoordinador = (a) => a?.kind === 'coordinador' || /coordinador|asistente personal/i.test(a?.role || '')
const BREAKS = { cafe: { label: 'A prendere un caffè', minutes: 3 }, bano: { label: 'In bagno', minutes: 3 } }

const STATUS = {
  trabajando:   { label: 'Al lavoro',     color: '#22c55e' },
  pausa:        { label: 'Task in pausa', color: '#ef4444' },
  disponible:   { label: 'In allineamento', color: '#3b82f6' },
  cafe:         { label: 'A prendere un caffè',   color: '#f59e0b' },
  bano:         { label: 'In bagno',     color: '#06b6d4' },
  desconectado: { label: 'In giardino',   color: '#9ca3af' },
  reserva:      { label: 'In riserva',     color: '#9ca3af' },
}

// Agentes de ejemplo: nombres de referencia del usuario, diseño visual propio
// Agentes de ejemplo (nombres genéricos: cada negocio pone los suyos)
const EXAMPLE_AGENTS = [
  { name: 'Nova', rol: 'coordinador', role: 'Assistente e coordinatore', kind: 'coordinador', mission: 'Coordina la squadra di agenti, assegna i task e riassume le cose da fare.',
    look: { torso: '#0f172a', legs: '#0f172a', arms: '#334155', hair: '#3b2a1e', accent: '#38bdf8', style: 'pelo', emblem: '#7dd3fc' } },
  { name: 'Leo', rol: 'copies_redes', role: 'Copies para redes sociales', mission: 'Escribe captions, carruseles y guiones cortos para redes.',
    look: { torso: '#16a34a', legs: '#1f2937', arms: '#16a34a', hair: '#2b2118', style: 'pelo' } },
  { name: 'Mia', rol: 'estrategia', role: 'Estrategia de marketing', mission: 'Propone estrategias, ofertas y planes de marketing.',
    look: { torso: '#111827', legs: '#111827', arms: '#111827', hair: '#b4231c', style: 'pelo-largo', emblem: '#ef4444' } },
  { name: 'Teo', rol: 'diseno_web', role: 'Diseño web y landing pages', mission: 'Diseña landing pages en HTML listas para publicar.',
    look: { torso: '#1d4ed8', legs: '#1e3a8a', arms: '#1d4ed8', hair: '#7a5230', style: 'gorra' } },
]

const toMs = (ts) => !ts ? 0 : ts.toMillis ? ts.toMillis() : ts.seconds ? ts.seconds * 1000 : new Date(ts).getTime()

function fmtElapsed(s) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

function taskElapsed(task, now) {
  if (!task) return 0
  let s = task.totalTimeSeconds || 0
  if (task.estado === 'en_progreso' && task.timerStartedAt && !task.timerPaused) {
    s += Math.floor((now - toMs(task.timerStartedAt)) / 1000)
  }
  return s
}


export default function OfficeView() {
  const { user, userProfile, users, tasks, agents, clients, isAdmin, isSuperAdmin, showToast, openEditTask } = useApp()
  const containerRef = useRef(null)
  const [noGl, setNoGl] = useState(false)                // il 3D non e' partito (niente WebGL)
  const sceneRef = useRef(null)
  const [now, setNow] = useState(Date.now())
  const [hover, setHover] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  // Motor de agentes (corre en el PC del dueño): late cada minuto en config/agentWorker
  const [briefing, setBriefing] = useState(null)       // config/briefing: la squadra e' in sala riunioni finche' `until` non e' passato
  useEffect(() => onSnapshot(doc(db, 'config', 'briefing'), s => setBriefing(s.exists() ? s.data() : null), () => {}), [])
  const [engine, setEngine] = useState(null)
  useEffect(() => onSnapshot(doc(db, 'config', 'agentWorker'), s => setEngine(s.exists() ? s.data() : null), () => {}), [])
  const [tab, setTab] = useState('agentes')
  const [agentModal, setAgentModal] = useState(null) // null | 'new' | agent
  const [assignFor, setAssignFor] = useState(null)
  const [avatarFor, setAvatarFor] = useState(null)   // persona cuyo avatar 3D se edita
  const [chatWith, setChatWith] = useState(null)     // null | 'grupo' | agente

  // Reloj: actualiza estados (fin de descansos, timers) cada 5 s
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 5000)
    return () => clearInterval(id)
  }, [])

  // ===== Estado de cada personaje =====
  const characters = useMemo(() => {
    const list = []
    const kitchenIdx = { n: 0 }, bathIdx = { n: 0 }, gardenIdx = { n: 0 }
    const nextPlace = (status, home) => {
      if (status === 'cafe') return { type: 'kitchen', index: kitchenIdx.n++ }
      if (status === 'bano') return { type: 'bathroom', index: bathIdx.n++ }
      if (status === 'desconectado' || status === 'reserva') return { type: 'garden', index: gardenIdx.n++ }
      return home
    }
    const ACTIVITY = { trabajando: 'typing', pausa: 'paused', cafe: 'drink', desconectado: 'relax', reserva: 'relax' }

    // El Pipeline manda: una tarea en progreso con timer corriendo = trabajando en su puesto,
    // aunque la persona no tenga la app abierta; si está pausada, en su puesto "en pausa"
    const statusFor = ({ online, onBreak, working, paused }) => {
      if (onBreak) return onBreak
      if (working) return 'trabajando'
      if (!online) return 'desconectado'
      return paused ? 'pausa' : 'disponible'
    }

    const employees = users
      .filter(u => u.role !== 'pending' && u.email)
      .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
    // Oficinas privadas para administradores: el dueño primero
    const officeOwners = employees
      .filter(u => u.role === 'admin')
      .sort((a, b) => (b.email === SUPER_ADMIN_EMAIL) - (a.email === SUPER_ADMIN_EMAIL))
      .slice(0, 3)
    let deskN = 0
    employees.forEach((u) => {
      const online = u.lastSeen && now - toMs(u.lastSeen) < ONLINE_MS
      const onBreak = u.breakUntil && toMs(u.breakUntil) > now ? (u.breakType || 'cafe') : null
      const working = tasks.find(t => t.asignado === u.uid && t.estado === 'en_progreso' && !t.timerPaused && !t.pendingAcceptance)
      const paused = tasks.find(t => t.asignado === u.uid && t.estado === 'en_progreso')
      const status = statusFor({ online, onBreak, working, paused })
      const officeIdx = officeOwners.findIndex(o => o.uid === u.uid)
      const home = officeIdx >= 0 ? { type: 'office', index: officeIdx } : { type: 'desk', zone: 'team', index: deskN++ }
      list.push({
        id: `u:${u.uid}`, kind: 'empleado', ref: u, name: u.name?.split(' ')[0] || 'Senza nome',
        fullName: u.name, role: u.position || (officeIdx >= 0 ? 'Direzione' : 'Squadra'), status,
        task: working || paused,
        look: userLook(u),
        place: nextPlace(status, home), activity: ACTIVITY[status] || 'idle',
      })
    })

    let labN = 0, meetN = 0, idleN = 0
    const briefingOn = !!briefing?.until && briefing.until > now
    agents
      .slice()
      .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
      .forEach((a) => {
        const home = isCoordinador(a) ? { type: 'coordinator', index: 0 } : { type: 'desk', zone: 'lab', index: labN++ }
        const onBreak = a.active && a.breakUntil && toMs(a.breakUntil) > now ? (a.breakType || 'cafe') : null
        const working = tasks.find(t => t.asignado === a.id && t.estado === 'en_progreso' && !t.timerPaused)
        const paused = tasks.find(t => t.asignado === a.id && t.estado === 'en_progreso')
        const status = !a.active ? 'reserva' : statusFor({ online: true, onBreak, working, paused })
        // Dove sta un agente: ciascuno nel suo ufficio privato/scrivania; in sala allineamento solo se c'è un briefing attivo; in giardino se in riserva
        let place = nextPlace(status, home)
        const inMeeting = a.active && briefingOn
        if (inMeeting) {
          place = { type: 'meeting', index: meetN++ }
        }
        const activity = working
          ? 'typing'
          : inMeeting
            ? (meetN % 2 === 0 ? 'typing' : 'idle')
            : (ACTIVITY[status] || 'idle')

        list.push({
          id: `a:${a.id}`, kind: 'agente', ref: a, name: a.name, fullName: a.name,
          role: a.role, status: inMeeting ? 'disponible' : status, task: working || paused, look: a.look || {},
          place, home, activity,
        })
      })
    return list
  }, [users, agents, tasks, now, briefing])

  const byId = useMemo(() => Object.fromEntries(characters.map(c => [c.id, c])), [characters])
  const engineOn = !!engine?.online && now - (engine?.lastSeen?.toMillis?.() || 0) < 3 * 60000

  // ===== Entregas visuales: el coordinador reparte y recoge el trabajo =====
  // Solo animación: al delegar, el coordinador lleva la carpeta al agente; al terminar, l'agente se la lleva
  // al coordinador y este va a la oficina del dueño. Cada paso espera a que el personaje llegue.
  const [errand, setErrand] = useState(null)         // paso actual { who, to, label }
  const errandQueue = useRef([])
  const prevTasks = useRef(null)
  const coordId = useMemo(() => { const j = agents.find(isCoordinador); return j ? 'a:' + j.id : null }, [agents])
  const ownerId = useMemo(() => { const e = users.find(u => (u.email || '').toLowerCase() === SUPER_ADMIN_EMAIL); return e ? 'u:' + e.uid : null }, [users])

  const enqueue = (steps) => {
    if (!steps.length || errandQueue.current.length > 12) return
    errandQueue.current.push(...steps)
    setErrand(cur => cur || errandQueue.current.shift())
  }

  useEffect(() => {
    const prev = prevTasks.current
    prevTasks.current = new Map(tasks.map(t => [t.id, { estado: t.estado, agentStatus: t.agentStatus }]))
    if (!prev || !coordId) return // primera carga: no se anima lo que ya existía
    const isAgent = (uid) => agents.some(a => a.id === uid)
    for (const t of tasks) {
      const before = prev.get(t.id)
      const who = 'a:' + t.asignado
      if (!before && isAgent(t.asignado) && who !== coordId && /^coordinador/i.test(t.creadoPor || '')) {
        enqueue([{ who: coordId, to: who, label: 'consegna un task' }])
      }
      if (before && before.estado !== 'en_espera' && t.estado === 'en_espera' && t.agentStatus === 'done' && isAgent(t.asignado)) {
        const steps = who !== coordId ? [{ who, to: coordId, label: 'entregando al coordinador' }] : []
        if (ownerId) steps.push({ who: coordId, to: ownerId, label: 'entregando al jefe' })
        enqueue(steps)
      }
    }
  }, [tasks, agents, coordId, ownerId])

  useEffect(() => {
    if (!errand) return
    const started = Date.now()
    let arrivedAt = null
    const id = setInterval(() => {
      const sc = sceneRef.current
      if (!arrivedAt && (sc?.arrived(errand.who) ?? true) && Date.now() - started > 800) arrivedAt = Date.now()
      const done = (arrivedAt && Date.now() - arrivedAt > 3500) || Date.now() - started > 45000
      if (done) {
        clearInterval(id)
        setErrand(errandQueue.current.shift() || null)
      }
    }, 300)
    return () => clearInterval(id)
  }, [errand])

  // ===== Montaje de la escena 3D =====
  useEffect(() => {
    let scene
    try {
      scene = new OfficeScene(containerRef.current, {
        onHover: (id, x, y) => setHover(id ? { id, x, y } : null),
        onSelect: (id) => setSelectedId(id),
      })
    } catch (e) {                                       // browser senza WebGL o grafica disattivata: la pagina resta, senza il 3D
      console.error('Ufficio 3D non disponibile:', e)
      setNoGl(true)
      return undefined
    }
    sceneRef.current = scene
    return () => { scene.dispose(); sceneRef.current = null }
  }, [])

  useEffect(() => {
    sceneRef.current?.setCharacters(characters.map(c => {
      const busy = errand?.who === c.id && byId[errand.to]
      return {
        id: c.id, name: busy ? c.name + ' · ' + errand.label : c.name, look: c.look,
        place: busy ? { type: 'visit', targetId: errand.to } : c.place,
        activity: busy ? 'carry' : c.activity,
        statusColor: STATUS[c.status].color,
      }
    }))
  }, [characters, errand, byId])

  // Il lancio: quando un deploy va a buon fine (scheda di modifica completata, con il link dell'anteprima o della PR) un missile parte dal tetto
  // dell'ufficio dell'agente che l'ha fatto. Al primo caricamento si segna l'esistente in silenzio: partono solo i lanci nuovi.
  const launched = useRef(null)
  useEffect(() => {
    const ready = tasks.filter(t => t.hlab?.kind === 'change' && t.estado === 'completada' && t.linkTerminada)
    if (launched.current === null) { launched.current = new Set(ready.map(t => t.id)); return }
    for (const t of ready) {
      if (launched.current.has(t.id)) continue
      launched.current.add(t.id)
      const ch = byId[`a:${t.asignado}`]
      if (ch?.home) { sceneRef.current?.launchRocket(ch.home); showToast(`🚀 Lancio: ${t.tarea}`) }
    }
  }, [tasks, byId])

  // Il simbolo dello strumento piu' usato da ogni agente (lo calcola il server dal registro degli eventi) sul pavimento del suo posto
  useEffect(() => {
    sceneRef.current?.setAgentBadges(characters
      .filter(c => c.kind === 'agente' && c.ref.active && c.ref.topTool)
      .map(c => ({ place: c.home, name: c.name, glyph: c.ref.topTool.glyph, label: c.ref.topTool.label, color: c.ref.topTool.color })))
  }, [characters])

  useEffect(() => { sceneRef.current?.select(selectedId) }, [selectedId])

  // ===== Acciones =====
  const setBreak = async (target, type) => {
    const ref = target.kind === 'agente' ? doc(db, 'agents', target.ref.id) : doc(db, 'users', target.ref.uid)
    const until = type ? Timestamp.fromMillis(Date.now() + BREAKS[type].minutes * 60000) : null
    try {
      await updateDoc(ref, { breakUntil: until, breakType: type || null })
      showToast(type ? `${target.name}: ${BREAKS[type].label.toLowerCase()}` : `${target.name} volvió a su puesto`)
    } catch (e) {
      console.error(e)
      showToast('Impossibile aggiornare lo stato', 'error')
    }
  }

  const toggleAgent = async (agent) => {
    try {
      await updateDoc(doc(db, 'agents', agent.id), { active: !agent.active, breakUntil: null })
      showToast(agent.active ? `${agent.name} pasó a reserva` : `${agent.name} se activó y va a su puesto`)
    } catch (e) {
      showToast('Impossibile cambiare lo stato dell\'agente', 'error')
    }
  }

  const deleteAgent = async (agent) => {
    if (!confirm(`Eliminare l'agente "${agent.name}"? I suoi task resteranno senza assegnatario.`)) return
    await deleteDoc(doc(db, 'agents', agent.id))
    setSelectedId(null)
    showToast('Agente eliminato', 'info')
  }

  const seedAgents = async () => {
    try {
      for (let i = 0; i < EXAMPLE_AGENTS.length; i++) {
        await addDoc(collection(db, 'agents'), {
          ...EXAMPLE_AGENTS[i], active: i < 3, order: i,
          breakUntil: null, breakType: null,
          createdAt: serverTimestamp(), createdBy: user.email,
        })
      }
      showToast('Agenti di esempio creati')
    } catch (e) {
      console.error(e)
      showToast('Impossibile creare gli agenti', 'error')
    }
  }

  const selected = selectedId ? byId[selectedId] : null
  const me = byId[`u:${user.uid}`]
  const employees = characters.filter(c => c.kind === 'empleado' && c.ref.uid !== user?.uid)
  const activeAgents = characters.filter(c => c.kind === 'agente' && c.ref.active)
  const reserveAgents = characters.filter(c => c.kind === 'agente' && !c.ref.active)
  const onlineCount = characters.filter(c => c.kind === 'empleado' && c.status !== 'desconectado').length
  const workingCount = characters.filter(c => c.status === 'trabajando').length
  const hovered = hover ? byId[hover.id] : null

  return (
    <div>
      <div className="office-layout">
        {/* ===== Escena 3D ===== */}
        <div className="office-stage">
          <div ref={containerRef} className="office-canvas" />
          {noGl && (
            <div className="card" style={{ position: 'absolute', inset: 24, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', padding: 24 }}>
              <h3 style={{ marginTop: 0 }}>L'ufficio 3D non può partire qui</h3>
              <p style={{ color: 'var(--text-3)', maxWidth: 420 }}>
                Il browser non supporta la grafica 3D (WebGL) o è disattivata. Gli agenti lavorano comunque: la console funziona normalmente. Riprova con Chrome o Edge aggiornati, con l'accelerazione hardware attiva.
              </p>
            </div>
          )}
          <button className="icon-btn office-reset" onClick={() => sceneRef.current?.resetView()} title="Centra la vista">
            <Icon name="target" size={15} />
          </button>
          <div className="office-controls-hint">
            <span><kbd>W</kbd><kbd>A</kbd><kbd>S</kbd><kbd>D</kbd> muoviti</span>
            <span><kbd>Q</kbd><kbd>E</kbd> ruota</span>
            <span><kbd>Rotella</kbd> zoom</span>
            <span><kbd>Trascina</kbd> sposta</span>
          </div>
          {chatWith && (
            <AgentChat
              key={chatWith === 'grupo' ? 'grupo' : chatWith.id}
              chatId={chatWith === 'grupo' ? 'grupo' : chatWith.id}
              agent={chatWith === 'grupo' ? null : chatWith}
              agents={agents} engineOn={engineOn} user={user} userProfile={userProfile}
              onClose={() => setChatWith(null)}
            />
          )}
        </div>

        {/* ===== Panel con solo i 3 riquadri ===== */}
        <aside className="office-panel">
          <div className="office-stats">
            <div><strong>{onlineCount}</strong><span>connessi</span></div>
            <div><strong>{activeAgents.length}</strong><span>agenti attivi</span></div>
            <div><strong>{workingCount}</strong><span>al lavoro</span></div>
          </div>
        </aside>
      </div>

      {/* Tarjeta al pasar el mouse */}
      {hovered && (
        <div className="office-tooltip" style={{ left: hover.x + 14, top: hover.y + 14 }}>
          <div style={{ fontWeight: 700 }}>{hovered.fullName}</div>
          <div style={{ fontSize: 11.5, opacity: 0.75 }}>{hovered.kind === 'agente' ? `Agente · ${hovered.role}` : hovered.role}</div>
          <StatusLine c={hovered} now={now} compact />
        </div>
      )}

      {avatarFor && (
        <AvatarModal person={avatarFor} onClose={() => setAvatarFor(null)} showToast={showToast} />
      )}
      {agentModal && (
        <AgentModal
          agent={agentModal === 'new' ? null : agentModal}
          nextOrder={agents.length}
          onClose={() => setAgentModal(null)}
          showToast={showToast}
          userEmail={user.email}
        />
      )}

      {assignFor && (
        <AssignModal
          agent={assignFor}
          clients={clients}
          tasks={tasks}
          onClose={() => setAssignFor(null)}
          showToast={showToast}
          userEmail={user.email}
        />
      )}
    </div>
  )
}

function StatusLine({ c, now, compact }) {
  const st = STATUS[c.status]
  return (
    <div style={{ marginTop: compact ? 6 : 10 }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 600 }}>
        <i style={{ width: 8, height: 8, borderRadius: '50%', background: st.color, display: 'inline-block' }} />
        {st.label}
      </span>
      {c.task && (
        <div style={{ fontSize: 12, marginTop: 4 }}>
          <span style={{ opacity: 0.7 }}>{c.task.timerPaused ? 'In pausa: ' : 'Task: '}</span>
          <strong>{c.task.tarea}</strong>
          <span style={{ fontFamily: 'Space Mono', marginLeft: 6 }}>{fmtElapsed(taskElapsed(c.task, now))}</span>
        </div>
      )}
    </div>
  )
}

// Cuadrito de color del personaje, o su foto si tiene
function swatchStyle(c) {
  const photo = c.ref?.photo
  return photo
    ? { background: `${c.look.torso} url(${photo}) center / cover no-repeat` }
    : { background: c.look.torso }
}

function PersonRow({ c, now, selected, onClick }) {
  const st = STATUS[c.status]
  return (
    <button className={`office-row ${selected ? 'selected' : ''}`} onClick={onClick}>
      <span className="office-swatch" style={swatchStyle(c)}>
        <i style={{ background: st.color }} />
      </span>
      <span style={{ flex: 1, minWidth: 0, textAlign: 'left' }}>
        <span style={{ display: 'block', fontSize: 13.5, fontWeight: 700, color: '#f0f0ff', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {c.fullName}
        </span>
        <span style={{ display: 'block', fontSize: 11.5, color: '#c4c4e0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: 1 }}>
          {c.task ? c.task.tarea : (c.kind === 'agente' ? c.role : st.label)}
        </span>
      </span>
      <span style={{ fontSize: 10.5, color: st.color, fontWeight: 700, whiteSpace: 'nowrap' }}>
        {c.task && c.status === 'trabajando' ? fmtElapsed(taskElapsed(c.task, now)) : st.label}
      </span>
    </button>
  )
}

// Tareas abiertas del personaje; clic para abrirlas en el editor de tareas
function OpenTaskList({ ownerId, tasks, onEdit }) {
  if (!ownerId) return null
  const open = tasks.filter(t => t.asignado === ownerId && t.estado !== 'completada')
  if (!open.length) return null
  return (
    <div style={{ marginTop: 8 }}>
      <p className="office-group-title" style={{ margin: '4px 0' }}>Task aperti ({open.length})</p>
      {open.slice(0, 6).map(t => (
        <button key={t.id} className="office-task-row" onClick={() => onEdit(t)} title="Apri e modifica il task">
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, textAlign: 'left' }}>{t.tarea}</span>
          <span className={`badge badge-${t.estado}`} style={{ fontSize: 9.5 }}>{t.estado.replace('_', ' ')}</span>
          <Icon name="edit" size={11} />
        </button>
      ))}
    </div>
  )
}

function AssignModal({ agent, clients, tasks, onClose, showToast, userEmail }) {
  const [form, setForm] = useState({ tarea: '', descripcion: '', cliente: '', prioridad: 'media', startNow: true })
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    if (!form.tarea.trim()) { showToast('Scrivi il nome del task', 'error'); return }
    const start = form.startNow && !wipLimitReached(tasks, agent.id)
    if (form.startNow && !start) showToast(`${agent.name} ya tiene ${WIP_LIMIT} tareas en progreso: la tarea queda pendiente`, 'warning')
    setSaving(true)
    try {
      await addDoc(collection(db, 'tasks'), {
        tarea: form.tarea.trim(), descripcion: form.descripcion.trim(),
        asignado: agent.id, asignadoAgente: true,
        cliente: form.cliente, prioridad: form.prioridad,
        estado: start ? 'en_progreso' : 'pendiente',
        fechaRequerimiento: null, fechaVencimiento: null, tiempoEstimadoMin: 0,
        links: '', linkTerminada: '', feedback: '', rating: 0,
        fechaInicio: start ? serverTimestamp() : null, fechaEntrega: null,
        timerStartedAt: start ? serverTimestamp() : null, timerPaused: false, totalTimeSeconds: 0,
        createdAt: serverTimestamp(), createdBy: userEmail,
        lastEditedBy: userEmail, lastEditedAt: serverTimestamp(),
      })
      showToast(`Task assegnato a ${agent.name}`)
      onClose()
    } catch (e) {
      console.error(e)
      showToast('Impossibile assegnare il task', 'error')
    }
    setSaving(false)
  }

  return (
    <div className="modal-overlay">
      <div className="modal" style={{ maxWidth: 460 }}>
        <div className="modal-header">
          <h2 className="modal-title">Task per {agent.name}</h2>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={14} /></button>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div className="form-group">
            <label className="form-label">Task</label>
            <input className="form-input" value={form.tarea} onChange={e => setForm(f => ({ ...f, tarea: e.target.value }))}
              placeholder={`Es. 3 titoli per ${clients[0] || 'un cliente'}`} autoFocus />
          </div>
          <div className="form-group">
            <label className="form-label">Istruzioni</label>
            <textarea className="form-textarea" rows={3} value={form.descripcion}
              onChange={e => setForm(f => ({ ...f, descripcion: e.target.value }))}
              placeholder="Cosa serve, tono, quantità, link al brief..." />
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Cliente</label>
              <select className="form-select" value={form.cliente} onChange={e => setForm(f => ({ ...f, cliente: e.target.value }))}>
                <option value="">Seleziona</option>
                {clients.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Priorità</label>
              <select className="form-select" value={form.prioridad} onChange={e => setForm(f => ({ ...f, prioridad: e.target.value }))}>
                <option value="urgente">Urgente</option>
                <option value="alta">Alta</option>
                <option value="media">Media</option>
                <option value="baja">Bassa</option>
              </select>
            </div>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
            <input type="checkbox" checked={form.startNow} onChange={e => setForm(f => ({ ...f, startNow: e.target.checked }))} />
            Inizia subito (l'agente si mette al lavoro)
          </label>
        </div>
        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose}>Annulla</button>
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>{saving ? 'Assegnazione...' : 'Assegna'}</button>
        </div>
      </div>
    </div>
  )
}
