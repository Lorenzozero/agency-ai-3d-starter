import React, { useState, useEffect } from 'react'
import { loginWithToken } from '../shim/auth.js'
import { extractTokenFromUrl, getToken } from '../shim/session.js'
import { BRAND } from '../config.js'
import { APP_VERSION } from '../App.jsx'

export default function LoginPage({ onLogin, theme }) {
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const autoTok = extractTokenFromUrl() || getToken()
    if (autoTok && autoTok.trim()) {
      setBusy(true)
      setToken(autoTok.trim())
      loginWithToken(autoTok.trim())
        .catch(err => {
          setError(err.message || 'Token non valido')
          setBusy(false)
        })
    }
  }, [])

  const enter = async (e) => {
    e.preventDefault()
    setBusy(true); setError('')
    try { await loginWithToken(token) } catch (err) { setError(err.message || 'Errore'); setBusy(false) }
  }
  return (
    <div className="login-page" data-theme={theme || 'dark'}>
      <div className="login-card">
        {/* Logo según tema */}
        <div style={{ marginBottom: 24 }}>
          <img
            src={theme === 'light' ? BRAND.logo : BRAND.logoDark}
            alt={BRAND.company}
            style={{ height: 52, objectFit: 'contain', maxWidth: '100%' }}
            onError={e => {
              e.target.style.display = 'none'
              e.target.nextSibling.style.display = 'flex'
            }}
          />
          {/* Fallback si no carga la imagen */}
          <div style={{
            display: 'none', width: 56, height: 56, borderRadius: 14,
            background: 'var(--purple)', alignItems: 'center', justifyContent: 'center',
            fontSize: 26, fontWeight: 900, color: '#fff', margin: '0 auto'
          }}>E</div>
        </div>

        <h1 className="login-title">{BRAND.appName}</h1>
        <p className="login-sub">
          La squadra di agenti di {BRAND.company}<br />
          <span style={{ fontSize: 11, color: 'var(--text-3)' }}>Versione {APP_VERSION}</span>
        </p>

        {busy && !error ? (
          <div style={{ padding: '24px 0', textAlign: 'center' }}>
            <div className="spinner" style={{ margin: '0 auto 14px' }} />
            <p style={{ fontSize: 13, color: 'var(--text-2)', fontWeight: 600 }}>Accesso automatico in corso...</p>
          </div>
        ) : (
          <form onSubmit={enter} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <input
              type="password" autoComplete="current-password" placeholder="Token personale" value={token} onChange={e => setToken(e.target.value)}
              style={{ padding: '12px 14px', borderRadius: 10, border: '1px solid var(--border, #444)', background: 'transparent', color: 'inherit', fontSize: 15 }}
            />
            <button className="google-btn" type="submit" disabled={busy || !token.trim()}>{busy ? 'Controllo...' : 'Entra'}</button>
            {error && <span style={{ color: '#ef4444', fontSize: 13 }}>{error}</span>}
          </form>
        )}

        <p style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 24, lineHeight: 1.5 }}>
          Accesso riservato. Il token personale lo trovi sul server: `hlab api-token`.
        </p>
      </div>
    </div>
  )
}
