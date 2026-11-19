import React, { useState } from 'react'
import { db } from '../firebase.js'
import { doc, updateDoc } from 'firebase/firestore'
import Icon from './Icons.jsx'
import LookEditor, { userLook } from './LookEditor.jsx'

// Personalizar el avatar 3D de una persona del equipo (la propia o, para administradores, la de cualquiera)
export default function AvatarModal({ person, onClose, showToast }) {
  const [look, setLook] = useState(() => ({ arms: userLook(person).torso, skin: '#f6c445', accent: '#ffd23f', emblem: '', ...userLook(person) }))
  const [saving, setSaving] = useState(false)

  const save = async () => {
    setSaving(true)
    const clean = { ...look }
    if (!clean.emblem) delete clean.emblem
    if (!clean.lipstick) delete clean.lipstick
    try {
      await updateDoc(doc(db, 'users', person.uid), { look: clean })
      showToast('Avatar aggiornato')
      onClose()
    } catch (e) {
      console.error(e)
      showToast('Impossibile salvare l\'avatar', 'error')
    }
    setSaving(false)
  }

  const reset = async () => {
    if (!confirm('Tornare all\'avatar automatico?')) return
    try {
      await updateDoc(doc(db, 'users', person.uid), { look: null })
      showToast('Avatar ripristinato')
      onClose()
    } catch { showToast('Impossibile ripristinare', 'error') }
  }

  return (
    <div className="modal-overlay">
      <div className="modal" style={{ maxWidth: 560 }}>
        <div className="modal-header">
          <h2 className="modal-title">Avatar 3D de {person.name?.split(' ')[0] || 'la persona'}</h2>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={14} /></button>
        </div>
        <p style={{ fontSize: 13, color: 'var(--text-3)', margin: '0 0 12px' }}>
          Così appare nell'ufficio. Scegli acconciatura, colore della pelle e colori dei vestiti.
        </p>
        <LookEditor look={look} onChange={setLook} />
        <div className="modal-footer">
          {person.look && <button className="btn btn-ghost" onClick={reset} style={{ marginRight: 'auto' }}>Ripristina</button>}
          <button className="btn btn-secondary" onClick={onClose}>Annulla</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? 'Salvataggio...' : 'Salva avatar'}</button>
        </div>
      </div>
    </div>
  )
}
