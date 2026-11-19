// Sessione dell'app: indirizzo dell'API e token personale. Il token si tiene nel browser (localStorage/sessionStorage) o, nell'app Flutter, nel contenitore del WebView.
const KEY = 'hlab_token'

// Stesso indirizzo da cui e' servita l'app, salvo VITE_HLAB_API (sviluppo con `npm run dev`).
export const apiBase = (import.meta.env.VITE_HLAB_API || '').replace(/\/$/, '')

export const extractTokenFromUrl = () => {
  try {
    if (typeof window === 'undefined') return ''
    const url = new URL(window.location.href)
    let tok = url.searchParams.get('token') || url.searchParams.get('t')
    if (!tok && window.location.hash) {
      const match = window.location.hash.match(/[#&]token=([^&]+)/)
      if (match) tok = decodeURIComponent(match[1])
    }
    if (!tok && typeof window !== 'undefined' && window.__HLAB_TOKEN) {
      tok = window.__HLAB_TOKEN
    }
    if (tok) {
      tok = tok.trim()
      try { localStorage.setItem(KEY, tok) } catch {}
      try { sessionStorage.setItem(KEY, tok) } catch {}
      if (typeof window !== 'undefined') window.__HLAB_TOKEN = tok
      return tok
    }
    return ''
  } catch {
    return ''
  }
}

export const getToken = () => {
  try {
    const fromUrl = extractTokenFromUrl()
    if (fromUrl) return fromUrl
    return localStorage.getItem(KEY) || sessionStorage.getItem(KEY) || (typeof window !== 'undefined' ? window.__HLAB_TOKEN : '') || ''
  } catch {
    return ''
  }
}

export const setToken = (t) => {
  try {
    if (t) {
      localStorage.setItem(KEY, t)
      sessionStorage.setItem(KEY, t)
      if (typeof window !== 'undefined') window.__HLAB_TOKEN = t
    } else {
      localStorage.removeItem(KEY)
      sessionStorage.removeItem(KEY)
      if (typeof window !== 'undefined') delete window.__HLAB_TOKEN
    }
  } catch {
    /* modalita' privata: resta valido solo per questa pagina */
  }
}

const listeners = new Set()
export const onSessionChange = (fn) => { listeners.add(fn); return () => listeners.delete(fn) }
export const notifySession = (user) => listeners.forEach((fn) => fn(user))
export const onUnauthorized = () => { setToken(''); notifySession(null) }
