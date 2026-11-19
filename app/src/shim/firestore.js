// Sostituto di 'firebase/firestore' per questa app: stessa interfaccia, ma i dati stanno sul server hlab (API in lab/hlab/api.py).
// Copre solo cio' che l'interfaccia usa (collection, doc, query, where, orderBy, limit, onSnapshot, getDoc(s), addDoc, setDoc, updateDoc,
// deleteDoc, writeBatch, serverTimestamp, increment, arrayUnion/Remove, Timestamp). Il tempo reale e' un flusso SSE letto con fetch
// (EventSource non permette l'header di autorizzazione).
import { apiBase, getToken, onUnauthorized } from './session.js'

export class Timestamp {
  constructor(ms) { this._ms = ms; this.seconds = Math.floor(ms / 1000); this.nanoseconds = (ms % 1000) * 1e6 }
  toMillis() { return this._ms }
  toDate() { return new Date(this._ms) }
  static now() { return new Timestamp(Date.now()) }
  static fromDate(d) { return new Timestamp(d.getTime()) }
  static fromMillis(ms) { return new Timestamp(ms) }
}

// ---- valori: dal server (timestamp -> Timestamp) e verso il server (Date/Timestamp -> {__ts}) ----
const decode = (v) => {
  if (Array.isArray(v)) return v.map(decode)
  if (v && typeof v === 'object') {
    if (Object.keys(v).length === 1 && typeof v.__ts === 'number') return new Timestamp(v.__ts)
    return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, decode(x)]))
  }
  return v
}
const encode = (v) => {
  if (v instanceof Timestamp) return { __ts: v.toMillis() }
  if (v instanceof Date) return { __ts: v.getTime() }
  if (Array.isArray(v)) return v.map(encode)
  if (v && typeof v === 'object') {
    return Object.fromEntries(Object.entries(v).filter(([, x]) => x !== undefined).map(([k, x]) => [k, encode(x)]))
  }
  return v
}

export const serverTimestamp = () => ({ __op: 'serverTimestamp' })
export const increment = (n) => ({ __op: 'increment', n })
export const arrayUnion = (...v) => ({ __op: 'arrayUnion', v: encode(v) })
export const arrayRemove = (...v) => ({ __op: 'arrayRemove', v: encode(v) })
export const deleteField = () => ({ __op: 'delete' })

// ---- riferimenti ----
export const getFirestore = () => ({ __hlab: true })
const segs = (parts) => parts.flatMap((p) => String(p).split('/')).filter(Boolean)
const autoId = () => Array.from(crypto.getRandomValues(new Uint8Array(10)), (b) => b.toString(36).padStart(2, '0')).join('').slice(0, 20)

export function collection(parent, ...parts) {
  const path = (parent && parent.type === 'doc' ? [parent.path, ...parts] : parts).join('/')
  return { type: 'collection', path: segs([path]).join('/') }
}
export function doc(parent, ...parts) {
  if (parent && parent.type === 'collection') {
    const id = parts.length ? parts.join('/') : autoId()
    return { type: 'doc', path: `${parent.path}/${id}`, id, parent }
  }
  const path = segs(parts).join('/')
  const bits = path.split('/')
  return { type: 'doc', path, id: bits[bits.length - 1], parent: { type: 'collection', path: bits.slice(0, -1).join('/') } }
}
export const where = (field, op, value) => ({ t: 'where', field, op, value: encode(value) })
export const orderBy = (field, dir = 'asc') => ({ t: 'order', field, dir })
export const limit = (n) => ({ t: 'limit', n })
export function query(coll, ...cs) {
  return {
    type: 'query', coll: coll.path,
    where: cs.filter((c) => c.t === 'where').map((c) => [c.field, c.op, c.value]),
    order: cs.filter((c) => c.t === 'order').map((c) => [c.field, c.dir]),
    limit: (cs.find((c) => c.t === 'limit') || {}).n,
  }
}

// ---- snapshot ----
const docRef = (path) => doc(null, path)
const mkDoc = (id, path, data) => ({ id, ref: docRef(path), exists: () => data != null, data: () => (data == null ? undefined : decode(data)) })
function mkQuerySnap(coll, rows) {
  const docs = rows.map((r) => mkDoc(r.id, `${coll}/${r.id}`, r.data))
  return { docs, size: docs.length, empty: docs.length === 0, forEach: (fn) => docs.forEach(fn) }
}

// ---- rete ----
async function call(path, body) {
  const res = await fetch(`${apiBase}${path}`, {
    method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${getToken()}` }, body: JSON.stringify(body),
  })
  if (res.status === 401) { onUnauthorized(); throw Object.assign(new Error('non autorizzato'), { code: 'permission-denied' }) }
  if (!res.ok) throw Object.assign(new Error((await res.json().catch(() => ({}))).error || res.statusText), { code: 'invalid-argument' })
  return res.json()
}
const write = (b) => call('/api/db/write', b)

export async function getDoc(ref) { const r = await call('/api/db/get', { path: ref.path }); return mkDoc(ref.id, ref.path, r.data) }
export async function getDocs(q) {
  const src = q.type === 'collection' ? query(q) : q
  const r = await call('/api/db/query', { coll: src.coll, where: src.where, order: src.order, limit: src.limit })
  return mkQuerySnap(src.coll, r.docs)
}
export const addDoc = async (coll, data) => { const r = await write({ op: 'add', coll: coll.path, data: encode(data) }); return docRef(`${coll.path}/${r.id}`) }
export const setDoc = (ref, data, opts = {}) => write({ op: 'set', path: ref.path, data: encode(data), merge: !!opts.merge })
export const updateDoc = (ref, data) => write({ op: 'update', path: ref.path, data: encode(data) })
export const deleteDoc = (ref) => write({ op: 'delete', path: ref.path })
export function writeBatch() {
  const ops = []
  const b = {
    set: (ref, data, opts = {}) => { ops.push({ op: 'set', path: ref.path, data: encode(data), merge: !!opts.merge }); return b },
    update: (ref, data) => { ops.push({ op: 'update', path: ref.path, data: encode(data) }); return b },
    delete: (ref) => { ops.push({ op: 'delete', path: ref.path }); return b },
    commit: () => write({ op: 'batch', ops }),
  }
  return b
}

// ---- tempo reale: flusso SSE con riconnessione ----
export function onSnapshot(target, next, onError) {
  const isDoc = target.type === 'doc'
  const src = target.type === 'collection' ? query(target) : target
  const body = isDoc ? { kind: 'doc', path: target.path } : { kind: 'query', coll: src.coll, where: src.where, order: src.order, limit: src.limit }
  const ctl = new AbortController()
  let stopped = false

  async function run() {
    let wait = 1000
    while (!stopped) {
      try {
        const res = await fetch(`${apiBase}/api/db/listen`, {
          method: 'POST', signal: ctl.signal, headers: { 'content-type': 'application/json', authorization: `Bearer ${getToken()}` }, body: JSON.stringify(body),
        })
        if (res.status === 401) { onUnauthorized(); return }
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
        wait = 1000
        const reader = res.body.getReader()
        const dec = new TextDecoder()
        let buf = ''
        for (;;) {
          const { value, done } = await reader.read()
          if (done) break
          buf += dec.decode(value, { stream: true })
          let i
          while ((i = buf.indexOf('\n\n')) >= 0) {
            const frame = buf.slice(0, i)
            buf = buf.slice(i + 2)
            if (!frame.startsWith('data: ')) continue                       // ": ping" di mantenimento
            const msg = JSON.parse(frame.slice(6))
            next(isDoc ? mkDoc(target.id, target.path, msg.doc) : mkQuerySnap(src.coll, msg.docs))
          }
        }
      } catch (e) {
        if (stopped) return
        if (onError) onError(e)
      }
      if (stopped) return
      await new Promise((r) => setTimeout(r, wait))
      wait = Math.min(wait * 2, 15000)
    }
  }
  run()
  return () => { stopped = true; ctl.abort() }
}
