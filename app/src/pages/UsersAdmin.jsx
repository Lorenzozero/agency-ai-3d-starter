import React, { useState } from 'react'
import { BRAND } from '../config.js'
import { useApp, SUPER_ADMIN_EMAIL } from '../App.jsx'
import { db } from '../firebase.js'
import { doc, updateDoc, deleteDoc, setDoc, getDocs, collection, writeBatch, onSnapshot } from 'firebase/firestore'
import { useEffect } from 'react'

export default function UsersAdmin() {
  const { users, tasks, user, showToast, isSuperAdmin } = useApp()
  const [saving, setSaving] = useState(null)
  const [tab, setTab] = useState('active')
  const [inviteOpen, setInviteOpen] = useState(false)
  const [editingUser, setEditingUser] = useState(null)
  const [invites, setInvites] = useState([])

  // Invitaciones pendientes en tiempo real
  useEffect(() => {
    const unsub = onSnapshot(collection(db, 'invites'), snap => {
      setInvites(snap.docs.map(d => ({ email: d.id, ...d.data() })))
    })
    return unsub
  }, [])

  const pending = users.filter(u => u.role === 'pending')
  const active = users.filter(u => u.role !== 'pending')

  const getTaskCount = (uid) => tasks.filter(t => t.asignado === uid).length
  const getCompletedCount = (uid) => tasks.filter(t => t.asignado === uid && t.estado === 'completada').length

  // ¿Puede el usuario actual cambiar el rol de este usuario?
  const canChangeRole = (targetUser) => {
    if (targetUser.email === SUPER_ADMIN_EMAIL) return false
    if (isSuperAdmin) return true
    if (targetUser.role === 'admin') return false
    return true
  }

  const handleRoleChange = async (u, newRole) => {
    if (!canChangeRole(u)) { showToast('Non hai il permesso di cambiare questo ruolo', 'error'); return }
    setSaving(u.uid)
    try {
      await updateDoc(doc(db, 'users', u.uid), { role: newRole })
      showToast(`Rol de ${u.name} → "${newRole === 'admin' ? 'Amministratore' : 'Collaboratore'}"`)
    } catch (e) {
      console.error(e)
      const detail = e?.code === 'permission-denied' ? 'sin permisos' : (e?.code || '')
      showToast(`Impossibile cambiare il ruolo${detail ? ` (${detail})` : ''}`, 'error')
    }
    setSaving(null)
  }

  const handleApprove = async (u, role = 'empleado') => {
    setSaving(u.uid)
    await updateDoc(doc(db, 'users', u.uid), { role })
    setSaving(null)
    showToast(`✅ ${u.name} approvato come ${role === 'admin' ? 'Amministratore' : 'Collaboratore'}`)
  }

  const handleReject = async (u) => {
    if (!confirm(`Rifiutare ed eliminare l'account di ${u.name}?`)) return
    setSaving(u.uid)
    await deleteDoc(doc(db, 'users', u.uid))
    setSaving(null)
    showToast(`${u.name} eliminado`, 'info')
  }

  const handleDelete = async (u) => {
    if (u.email === SUPER_ADMIN_EMAIL) { showToast('Non puoi eliminare il super admin', 'error'); return }
    if (!confirm(`Eliminare ${u.name} dal sistema? I suoi task resteranno senza assegnatario e NON potrà rientrare senza la tua approvazione.`)) return
    setSaving(u.uid)
    await deleteDoc(doc(db, 'users', u.uid))
    // Borrar también su invitación (si existía): garantiza que si vuelve a
    // iniciar sesión quede en "pendiente" esperando tu aprobación
    if (u.email) {
      try { await deleteDoc(doc(db, 'invites', u.email.toLowerCase())) } catch (e) { /* sin invitación */ }
    }
    setSaving(null)
    showToast(`${u.name} eliminato. Se rientra, resterà in attesa di approvazione`, 'info')
  }

  // RESET TOTAL: borra todas las tareas y todos los usuarios excepto el super admin
  const handleResetPlatform = async () => {
    if (!isSuperAdmin) return
    if (!confirm('⚠️ RESET TOTALE: verranno eliminati TUTTI i task e TUTTI gli utenti (tranne il tuo account). Continuare?')) return
    if (!confirm('Ultima conferma: questa azione NON si può annullare. Sicuro?')) return
    setSaving('reset')
    try {
      // Borrar tareas
      const taskSnap = await getDocs(collection(db, 'tasks'))
      let batch = writeBatch(db)
      let count = 0
      for (const d of taskSnap.docs) {
        batch.delete(d.ref); count++
        if (count % 400 === 0) { await batch.commit(); batch = writeBatch(db) }
      }
      await batch.commit()
      // Borrar usuarios (excepto yo)
      const userSnap = await getDocs(collection(db, 'users'))
      batch = writeBatch(db)
      count = 0
      for (const d of userSnap.docs) {
        if (d.id !== user.uid) { batch.delete(d.ref); count++ }
        if (count % 400 === 0) { await batch.commit(); batch = writeBatch(db) }
      }
      await batch.commit()
      showToast('✅ Piattaforma azzerata: task e utenti eliminati', 'info')
    } catch (e) {
      console.error(e)
      showToast('Errore nel ripristino', 'error')
    }
    setSaving(null)
  }

  const handleCancelInvite = async (email) => {
    await deleteDoc(doc(db, 'invites', email))
    showToast('Invito annullato', 'info')
  }

  const sortedActive = [...active].sort((a, b) => {
    const order = { admin: 0, empleado: 1 }
    return (order[a.role] ?? 2) - (order[b.role] ?? 2)
  })

  return (
    <div>
      <div className="page-header">
        <div>
          <span className="page-eyebrow">Amministrazione</span>
          <h1 className="page-title">Utenti</h1>
          <p className="page-subtitle">{active.length} activos · {pending.length} pendientes · {invites.length} invitados</p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-primary btn-sm" onClick={() => setInviteOpen(true)}>
            ✉ Invita utente
          </button>
          {isSuperAdmin && (
            <button className="btn btn-danger btn-sm" onClick={handleResetPlatform} disabled={saving === 'reset'}>
              {saving === 'reset' ? 'Azzeramento...' : '⚠️ Reset totale'}
            </button>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 18 }}>
        <button className={`btn btn-sm ${tab === 'active' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setTab('active')}>
          Activos ({active.length})
        </button>
        <button className={`btn btn-sm ${tab === 'pending' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setTab('pending')} style={{ position: 'relative' }}>
          Pendientes ({pending.length})
          {pending.length > 0 && (
            <span style={{ position: 'absolute', top: -4, right: -4, width: 8, height: 8, borderRadius: '50%', background: 'var(--danger)' }} />
          )}
        </button>
        <button className={`btn btn-sm ${tab === 'invites' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setTab('invites')}>
          Invitaciones ({invites.length})
        </button>
      </div>

      {/* Pendientes de aprobación */}
      {tab === 'pending' && (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          {pending.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">✅</div>
              <h3>Nessun account in attesa</h3>
              <p>Tutti gli utenti sono approvati</p>
            </div>
          ) : pending.map(u => (
            <div key={u.uid} className="user-list-row">
              <Avatar u={u} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>{u.name}</div>
                <div style={{ fontSize: 12, color: 'var(--text-3)' }}>{u.email}</div>
                <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>
                  Registrado: {u.createdAt?.toDate?.()?.toLocaleDateString('it-IT') || '—'}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn-primary btn-sm" disabled={saving === u.uid}
                  onClick={() => handleApprove(u, 'empleado')}>
                  ✓ Approva come collaboratore
                </button>
                {isSuperAdmin && (
                  <button className="btn btn-secondary btn-sm" disabled={saving === u.uid}
                    onClick={() => handleApprove(u, 'admin')}>
                    ★ Como Admin
                  </button>
                )}
                <button className="btn btn-danger btn-sm" disabled={saving === u.uid}
                  onClick={() => handleReject(u)}>
                  ✕
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Invitaciones */}
      {tab === 'invites' && (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          {invites.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">✉</div>
              <h3>Nessun invito in sospeso</h3>
              <p>Invita qualcuno con il pulsante "✉ Invita utente"</p>
            </div>
          ) : invites.map(inv => (
            <div key={inv.email} className="user-list-row">
              <div style={{
                width: 40, height: 40, borderRadius: '50%', background: 'var(--card-2)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, flexShrink: 0
              }}>✉</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>{inv.email}</div>
                <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
                  Entrará como: <strong>{inv.role === 'admin' ? 'Amministratore' : 'Collaboratore'}</strong> · senza bisogno di approvazione
                </div>
              </div>
              <button className="btn btn-danger btn-sm" onClick={() => handleCancelInvite(inv.email)}>
                ✕ Annulla
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Usuarios activos */}
      {tab === 'active' && (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          {sortedActive.map(u => {
            const taskCount = getTaskCount(u.uid)
            const doneCount = getCompletedCount(u.uid)
            const isOnline = u.lastSeen && (Date.now() - (u.lastSeen.toMillis?.() || u.lastSeen.seconds * 1000)) < 5 * 60 * 1000

            return (
              <div key={u.uid} className="user-list-row">
                <Avatar u={u} online={isOnline} />

                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 14 }}>
                    {u.name || <span style={{ color: 'var(--danger)' }}>⚠ Non identificato</span>}
                  </div>
                  <div style={{ fontSize: 12, color: u.email ? 'var(--text-3)' : 'var(--danger)' }}>
                    {u.email || 'Account incompleto: eliminalo, si ripara al prossimo accesso'}
                    {u.position && <span style={{ marginLeft: 8 }}>· {u.position}</span>}
                  </div>
                </div>

                <div style={{ fontSize: 12, color: 'var(--text-3)', textAlign: 'right', flexShrink: 0 }}>
                  <div>{taskCount} tareas</div>
                  <div style={{ color: 'var(--success)' }}>{doneCount} hechas</div>
                </div>

                <div style={{ flexShrink: 0 }}>
                  {u.email === SUPER_ADMIN_EMAIL ? (
                    <span className="badge badge-admin">★ Super admin</span>
                  ) : canChangeRole(u) ? (
                    <select className="form-select" style={{ fontSize: 12.5, padding: '4px 8px', width: 'auto' }}
                      value={u.role || 'pending'} disabled={saving === u.uid}
                      onChange={e => handleRoleChange(u, e.target.value)}>
                      <option value="empleado">Collaboratore</option>
                      <option value="admin">Responsabile</option>
                      <option value="pending">⏳ Da fare</option>
                    </select>
                  ) : (
                    <span className="badge badge-admin">◆ Manager</span>
                  )}
                </div>

                {/* Modifica profilo (cualquier admin puede editar; super admin edita a todos) */}
                {(isSuperAdmin || u.role !== 'admin' || u.uid === user?.uid) && (
                  <button className="btn btn-ghost btn-sm btn-icon" style={{ flexShrink: 0 }}
                    onClick={() => setEditingUser(u)} title="Modifica profilo">
                    ✎
                  </button>
                )}

                {u.email !== SUPER_ADMIN_EMAIL && (isSuperAdmin || u.role !== 'admin') && (
                  <button className="btn btn-ghost btn-sm btn-icon" style={{ color: 'var(--danger)', flexShrink: 0 }}
                    onClick={() => handleDelete(u)} title="Elimina utente" disabled={saving === u.uid}>
                    🗑
                  </button>
                )}
              </div>
            )
          })}
          {sortedActive.length === 0 && (
            <div className="empty-state"><div className="empty-icon">👥</div><h3>Nessun utente attivo</h3></div>
          )}
        </div>
      )}

      {/* Leyenda roles */}
      <div className="card" style={{ marginTop: 18 }}>
        <p className="section-title">Ruoli</p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {[
            { role: 'admin', label: '★ Super admin', desc: 'Solo il titolare dell\'account. Accesso totale: produttività, classifica, tempi di connessione, gestione completa di utenti e ruoli.' },
            { role: 'admin', label: '◆ Manager', desc: 'Assegna task agli altri, gestisce utenti e clienti. Il suo pannello mostra solo chi è online e i timer attivi (senza classifica né produttività).' },
            { role: 'empleado', label: 'Collaboratore', desc: 'Vede tutti i task della squadra. Prende, esegue e cambia lo stato dei propri task. Crea task per sé. Non elimina né modifica i task degli altri.' },
          ].map((r, i) => (
            <div key={i} style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
              <span className={`badge badge-${r.role}`} style={{ flexShrink: 0 }}>{r.label}</span>
              <span style={{ fontSize: 13, color: 'var(--text-2)' }}>{r.desc}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Modal invitar */}
      {inviteOpen && (
        <InviteModal
          onClose={() => setInviteOpen(false)}
          showToast={showToast}
          isSuperAdmin={isSuperAdmin}
        />
      )}

      {/* Modal editar usuario */}
      {editingUser && (
        <EditUserModal
          target={editingUser}
          onClose={() => setEditingUser(null)}
          showToast={showToast}
          isSuperAdmin={isSuperAdmin}
        />
      )}
    </div>
  )
}

function Avatar({ u, online = false }) {
  return (
    <div style={{ position: 'relative', flexShrink: 0 }}>
      {u.photo
        ? <img src={u.photo} alt="" style={{ width: 40, height: 40, borderRadius: '50%' }} />
        : <div style={{
            width: 40, height: 40, borderRadius: '50%', background: u.color || '#4700E0',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 16, fontWeight: 700, color: '#fff'
          }}>
            {(u.name || '?')[0].toUpperCase()}
          </div>
      }
      <div style={{
        position: 'absolute', bottom: 1, right: 1, width: 10, height: 10, borderRadius: '50%',
        background: online ? 'var(--success)' : 'var(--text-3)',
        border: '2px solid var(--card)'
      }} title={online ? 'Online' : 'Non connesso'} />
    </div>
  )
}

function InviteModal({ onClose, showToast, isSuperAdmin }) {
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('empleado')
  const [saving, setSaving] = useState(false)

  const handleInvite = async () => {
    const clean = email.trim().toLowerCase()
    if (!clean || !clean.includes('@')) { showToast('Scrivi un\'e-mail valida', 'error'); return }
    setSaving(true)
    try {
      // Registrar la invitación: al registrarse con este correo, entra directo con el rol asignado
      await setDoc(doc(db, 'invites', clean), {
        role,
        invitedAt: new Date().toISOString(),
      })
      // Abrir WhatsApp con la invitación pre-redactada (eliges el contacto allí)
      const text = encodeURIComponent(
        `Ciao! 👋 Ti invito a unirti a *${BRAND.appName}*, la piattaforma di task e agenti IA di ${BRAND.company}.

` +
        `1️⃣ Entra su: ${BRAND.url}
` +
        `2️⃣ Accedi con il token personale che ti ho inviato (${clean})
` +
        `3️⃣ Fatto! 🚀`
      )
      window.open(`https://wa.me/?text=${text}`, '_blank')
      showToast(`✅ Invito registrato per ${clean}`)
      onClose()
    } catch (e) {
      showToast('Errore nella creazione dell\'invito', 'error')
    }
    setSaving(false)
  }

  return (
    <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal" style={{ maxWidth: 440 }}>
        <div className="modal-header">
          <h2 className="modal-title">✉ Invita utente</h2>
          <button className="icon-btn" onClick={onClose}>✕</button>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="form-group">
            <label className="form-label">E-mail dell'invitato</label>
            <input className="form-input" type="email" value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder="persona@gmail.com" autoFocus
              onKeyDown={e => e.key === 'Enter' && handleInvite()} />
          </div>
          <div className="form-group">
            <label className="form-label">Ruolo all'ingresso</label>
            <select className="form-select" value={role} onChange={e => setRole(e.target.value)}>
              <option value="empleado">Collaboratore</option>
              {isSuperAdmin && <option value="admin">Responsabile</option>}
            </select>
          </div>
          <p style={{ fontSize: 12, color: 'var(--text-3)', margin: 0, lineHeight: 1.5 }}>
            Se abrirá <strong>WhatsApp</strong> con la invitación pre-redactada: solo
            elige el contacto y envíala. Cuando la persona entre con ese correo de
            Google, su cuenta quedará activa de inmediato sin necesidad de aprobación.
          </p>
        </div>
        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose}>Annulla</button>
          <button className="btn btn-primary" onClick={handleInvite} disabled={saving}>
            {saving ? 'Invio...' : '✉ Invitar'}
          </button>
        </div>
      </div>
    </div>
  )
}

function EditUserModal({ target, onClose, showToast, isSuperAdmin }) {
  const [form, setForm] = useState({
    name: target.name || '',
    email: target.email || '',
    position: target.position || '',
    phone: target.phone || '',
    bio: target.bio || '',
  })
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    if (!form.name.trim()) { showToast('Il nome non può essere vuoto', 'error'); return }
    setSaving(true)
    try {
      const updates = {
        name: form.name.trim(),
        position: form.position.trim(),
        phone: form.phone.trim(),
        bio: form.bio.trim(),
      }
      // Solo el Super admin puede cambiar el correo de contacto
      if (isSuperAdmin && form.email.trim()) {
        updates.email = form.email.trim().toLowerCase()
      }
      await updateDoc(doc(db, 'users', target.uid), updates)
      showToast(`Profilo di ${form.name} aggiornato`)
      onClose()
    } catch (e) {
      showToast('Errore nel salvataggio', 'error')
    }
    setSaving(false)
  }

  return (
    <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal" style={{ maxWidth: 440 }}>
        <div className="modal-header">
          <h2 className="modal-title">✎ Modifica: {target.name}</h2>
          <button className="icon-btn" onClick={onClose}>✕</button>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div className="form-group">
            <label className="form-label">Nome</label>
            <input className="form-input" value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
          </div>
          {isSuperAdmin && (
            <div className="form-group">
              <label className="form-label">E-mail</label>
              <input className="form-input" type="email" value={form.email}
                onChange={e => setForm(f => ({ ...f, email: e.target.value }))} />
              <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                Cambia el correo de contacto y notificaciones. El inicio de sesión
                sigue siendo con su cuenta de Google original.
              </span>
            </div>
          )}
          <div className="form-group">
            <label className="form-label">Ruolo</label>
            <input className="form-input" value={form.position}
              onChange={e => setForm(f => ({ ...f, position: e.target.value }))}
              placeholder="Es. Grafica" />
          </div>
          <div className="form-group">
            <label className="form-label">Telefono</label>
            <input className="form-input" value={form.phone}
              onChange={e => setForm(f => ({ ...f, phone: e.target.value }))} />
          </div>
          <div className="form-group">
            <label className="form-label">Bio</label>
            <textarea className="form-textarea" rows={2} value={form.bio}
              onChange={e => setForm(f => ({ ...f, bio: e.target.value }))} />
          </div>
        </div>
        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose}>Annulla</button>
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? 'Salvataggio...' : 'Salva'}
          </button>
        </div>
      </div>
    </div>
  )
}
