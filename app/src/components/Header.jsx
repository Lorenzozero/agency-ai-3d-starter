import React, { useState } from 'react'
import { useApp, APP_VERSION, SUPER_ADMIN_EMAIL } from '../App.jsx'
import Icon from './Icons.jsx'
import { BRAND } from '../config.js'

const EMPLOYEE_NAV = [
  { id: 'dashboard', icon: 'grid',        label: 'Il mio pannello' },
  { id: 'office',    icon: 'office',      label: 'Ufficio' },
  { id: 'tasks',     icon: 'columns',     label: 'Task' },
  { id: 'team',      icon: 'users',       label: 'Squadra' },
]

const ADMIN_NAV = [
  { id: 'dashboard', icon: 'grid',        label: 'Il mio pannello' },
  { id: 'office',    icon: 'office',      label: 'Ufficio' },
  { id: 'tasks',     icon: 'columns',     label: 'Task' },
  { id: 'team',      icon: 'users',       label: 'Squadra' },
]

// Solo el Super admin gestiona sistema (agenti e utenti vivono dentro Squadra, clienti gestiti da CRM)
const SUPER_NAV = [
  { id: 'system',  icon: 'gear',     label: 'Sistema' },
]

export default function Header({ page, setPage, onLogout }) {
  const { userProfile, isAdmin, isSuperAdmin, theme, setTheme, openNewTask, user, tasks, openEditTask } = useApp()
  const [menuOpen, setMenuOpen] = useState(false)

  const items = isAdmin
    ? (isSuperAdmin ? [...ADMIN_NAV, ...SUPER_NAV] : ADMIN_NAV)
    : EMPLOYEE_NAV

  const logoSrc = theme === 'dark' ? BRAND.logoDark : BRAND.logo

  return (
    <>
    <header className="header">
      {/* Brand: Agency AI */}
      <div className="header-logo" style={{ cursor: 'pointer', display: 'flex', alignItems: 'center' }}
        onClick={() => setPage(isAdmin ? 'dashboard' : 'tasks')}>
        <span style={{ fontSize: 17, fontWeight: 800, letterSpacing: '-0.02em', color: '#f0f0ff', whiteSpace: 'nowrap' }}>
          Agency <span style={{ color: 'var(--purple)' }}>AI</span>
        </span>
      </div>

      {/* Nav escritorio */}
      <nav className="header-nav">
        {items.map(item => {
          const isActive = page === item.id || (item.id === 'tasks' && page === 'pipeline')
          return (
            <button
              key={item.id}
              className={`nav-btn ${isActive ? 'active' : ''}`}
              onClick={() => setPage(item.id)}
            >
              <Icon name={item.icon} size={15} />
              <span>{item.label}</span>
            </button>
          )
        })}
      </nav>

      {/* Acciones */}
      <div className="header-actions">
        {/* Menú usuario */}
        <div className="dropdown-wrap">
          <div className="user-chip" onClick={() => setMenuOpen(v => !v)}>
            {userProfile?.photo
              ? <img src={userProfile.photo} alt="" />
              : <div style={{
                  width: 26, height: 26, borderRadius: '50%',
                  background: userProfile?.color || '#4700E0',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 12, fontWeight: 700, color: '#fff'
                }}>
                  {(userProfile?.name || '?')[0].toUpperCase()}
                </div>
            }
            <span style={{ maxWidth: 100, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {userProfile?.name?.split(' ')[0] || 'Io'}
            </span>
            <Icon name="chevronDown" size={12} style={{ color: 'var(--text-3)' }} />
          </div>

          {menuOpen && (
            <>
              <div style={{ position: 'fixed', inset: 0, zIndex: 150 }} onClick={() => setMenuOpen(false)} />
              <div className="dropdown-menu" style={{ zIndex: 200 }}>
                <div style={{ padding: '8px 12px 10px', borderBottom: '1px solid var(--border)' }}>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>{userProfile?.name}</div>
                  <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 2 }}>{userProfile?.email}</div>
                  <div style={{ marginTop: 6 }}>
                    <span className={`badge ${userProfile?.role === 'admin' ? 'badge-admin' : 'badge-empleado'}`}>
                      {userProfile?.email === SUPER_ADMIN_EMAIL ? 'Super admin'
                        : userProfile?.role === 'admin' ? 'Responsabile' : 'Collaboratore'}
                    </span>
                  </div>
                </div>
                <button className="dropdown-item" onClick={() => {
                  localStorage.removeItem(`tutorial_${userProfile?.uid}`)
                  window.location.reload()
                }}>
                  <Icon name="question" size={14} /> Guida rapida
                </button>
                <div className="dropdown-divider" />
                <button className="dropdown-item danger" onClick={onLogout}>
                  <Icon name="logout" size={14} /> Esci
                </button>
              </div>
            </>
          )}
        </div>
      </div>

    </header>

    {/* Nav inferior móvil: tutti i tab direttamente visibili, zero 'Altro' */}
    <nav className="mobile-nav">
      {items.map(item => {
        const isActive = page === item.id || (item.id === 'tasks' && page === 'pipeline')
        return (
          <button
            key={item.id}
            className={`mobile-nav-btn ${isActive ? 'active' : ''}`}
            onClick={() => setPage(item.id)}
          >
            <Icon name={item.icon} size={19} />
            <span className="mobile-nav-label">{item.label}</span>
          </button>
        )
      })}
    </nav>
    </>
  )
}
