// Sostituto di 'firebase/auth': nessun Google, un solo utente (tu) che entra con il token personale del server.
import { apiBase, getToken, setToken, onSessionChange, notifySession } from './session.js'

export const getAuth = () => ({ __hlab: true, currentUser: null })
export class GoogleAuthProvider {}

async function check(token) {
  const res = await fetch(`${apiBase}/api/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ token }) })
  if (!res.ok) return null
  const { user } = await res.json()
  return { uid: user.uid, email: user.email, displayName: user.displayName, photoURL: null }
}

export function onAuthStateChanged(auth, cb) {
  const t = getToken()
  let alive = true
  const off = onSessionChange((u) => alive && cb(u))
  ;(t ? check(t) : Promise.resolve(null)).then((u) => { if (alive) { if (!u) setToken(''); cb(u) } }).catch(() => alive && cb(null))
  return () => { alive = false; off() }
}

// Chiamata dalla pagina di login con il token digitato (al posto del popup di Google).
export async function loginWithToken(token) {
  const u = await check(token.trim())
  if (!u) throw new Error('Token non valido')
  setToken(token.trim())
  notifySession(u)
  return u
}

export const signInWithPopup = async () => { throw new Error('Qui si entra con il token personale') }
export const signOut = async () => { setToken(''); notifySession(null) }
