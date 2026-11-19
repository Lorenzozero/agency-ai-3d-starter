import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js'
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js'
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js'
import { GTAOPass } from 'three/addons/postprocessing/GTAOPass.js'
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js'
import { createMinifig } from './minifig.js'
import { NavGrid } from './navgrid.js'
import { woodTexture, tileTexture, carpetTexture } from './textures.js'

// ===== Planta (metros). x: oeste(-) a este(+); z: norte(-) a sur(+) =====
// PIANO TERRA: due file da sei uffici di vetro IDENTICI (nord e sud) e, in mezzo, l'area squadra, il giardino e l'ascensore.
// Primo ufficio a nord = Direzione (del titolare), secondo = COO, poi uno per agente; ne avanza uno per il prossimo agente.
// SECONDO PIANO: la Sala riunioni, SOPRA la fascia centrale. E' nascosta: compare quando il cursore passa sul punto in cui sta, o con il pulsante "Piano 2".
const B = { x0: -16, x1: 16, z0: -11, z1: 11 }
const ZN = -4.8, ZS = 4.8                                  // fronte degli uffici sul corridoio centrale: fila nord e fila sud
const COLS = 6                                             // uffici per fila: 12 in tutto, tutti della stessa misura
const CW = (B.x1 - B.x0) / COLS
const FLOOR_H = 3.3                                        // altezza del secondo piano (sopra la fascia centrale)
const LIFT = { x: 12.6, z: 0.2, zWait: 1.7, zIn: 0.1, zUp: -1.3 }   // ascensore: dove si aspetta, dove si entra in cabina, dove si esce di sopra
const LIFT_SPEED = 2.2
const GARDEN = { x0: -3.2, x1: 3.2, z0: -2.7, z1: 3.3 }
const SEAT_Y = 0.4
const WALK_SPEED = 2.6
const DOOR_W = 1.4

const ISO_DIR = new THREE.Vector3(1, 1.15, 1).normalize()
const HOME = new THREE.Vector3(0, 0.6, 0)                          // dove guarda la telecamera all'inizio: abbastanza in alto da vedere anche il secondo piano
const UP = new THREE.Vector3(0, 1, 0)

// ===== Materiales =====
const MAT = {}
function mats() {
  if (MAT.ready) return MAT
  Object.assign(MAT, {
    ready: true,
    wall: new THREE.MeshStandardMaterial({ color: '#f4f2ee', roughness: 0.92 }),
    wallCap: new THREE.MeshStandardMaterial({ color: '#7d838d', roughness: 0.7 }),
    glass: new THREE.MeshStandardMaterial({ color: '#cfe8f2', roughness: 0.05, metalness: 0.2, transparent: true, opacity: 0.18, depthWrite: false }),
    frame: new THREE.MeshStandardMaterial({ color: '#3c424c', roughness: 0.45, metalness: 0.6 }),
    deskTop: new THREE.MeshStandardMaterial({ color: '#f7f7f5', roughness: 0.35 }),
    deskWood: new THREE.MeshStandardMaterial({ color: '#b98a5a', roughness: 0.55 }),
    metal: new THREE.MeshStandardMaterial({ color: '#9aa1ab', roughness: 0.3, metalness: 0.8 }),
    dark: new THREE.MeshStandardMaterial({ color: '#23262d', roughness: 0.5 }),
    fabric: new THREE.MeshStandardMaterial({ color: '#2f3440', roughness: 0.85 }),
    fabricPurple: new THREE.MeshStandardMaterial({ color: '#4b2bb3', roughness: 0.8 }),
    leather: new THREE.MeshStandardMaterial({ color: '#1f2229', roughness: 0.45 }),
    leaf: new THREE.MeshStandardMaterial({ color: '#3f8f45', roughness: 0.7, flatShading: true }),
    leaf2: new THREE.MeshStandardMaterial({ color: '#5aa95e', roughness: 0.7, flatShading: true }),
    pot: new THREE.MeshStandardMaterial({ color: '#e8e4dc', roughness: 0.6 }),
    soil: new THREE.MeshStandardMaterial({ color: '#4a3524', roughness: 1 }),
    grass: new THREE.MeshStandardMaterial({ color: '#6fae55', roughness: 0.95 }),
    water: new THREE.MeshStandardMaterial({ color: '#5aa7d6', roughness: 0.08, metalness: 0.2 }),
    white: new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 0.4 }),
    counter: new THREE.MeshStandardMaterial({ color: '#2e3138', roughness: 0.25 }),
    steel: new THREE.MeshStandardMaterial({ color: '#c9ced6', roughness: 0.25, metalness: 0.9 }),
    tv: new THREE.MeshStandardMaterial({ color: '#0f1115', roughness: 0.2, emissive: '#16233a', emissiveIntensity: 0.6 }),
    purpleGlow: new THREE.MeshStandardMaterial({ color: '#7c4dff', emissive: '#7c4dff', emissiveIntensity: 1.2 }),
  })
  return MAT
}

// ===== Construcción =====
// ===== Testi e simboli disegnati su canvas (insegne e tappeti degli agenti) =====
const FONT = '"Plus Jakarta Sans","Segoe UI",system-ui,sans-serif'
const EMOJI = '"Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",sans-serif'

function canvasTexture(c) {
  const t = new THREE.CanvasTexture(c)
  t.colorSpace = THREE.SRGBColorSpace
  t.anisotropy = 8
  return t
}

function signTexture(text, ratio) {
  const H = 128, W = Math.round(H * ratio)
  const c = document.createElement('canvas'); c.width = W; c.height = H
  const g = c.getContext('2d')
  g.fillStyle = '#1b1530'; g.fillRect(0, 0, W, H)
  g.strokeStyle = '#7c4dff'; g.lineWidth = 4; g.strokeRect(6, 6, W - 12, H - 12)
  let size = 64
  g.font = `700 ${size}px ${FONT}`
  while (g.measureText(text).width > W - 36 && size > 18) { size -= 2; g.font = `700 ${size}px ${FONT}` }
  g.fillStyle = '#ffffff'; g.textAlign = 'center'; g.textBaseline = 'middle'
  g.fillText(text, W / 2, H / 2 + 3)
  return canvasTexture(c)
}

// Tappeto con il simbolo dello strumento che l'agente usa di piu' e il suo nome
function badgeTexture({ glyph, label, color }) {
  const S = 256
  const c = document.createElement('canvas'); c.width = c.height = S
  const g = c.getContext('2d')
  g.fillStyle = color || '#4700E0'
  g.beginPath(); g.roundRect(8, 8, S - 16, S - 16, 44); g.fill()
  g.strokeStyle = 'rgba(255,255,255,.55)'; g.lineWidth = 6; g.beginPath(); g.roundRect(18, 18, S - 36, S - 36, 36); g.stroke()
  g.textAlign = 'center'; g.textBaseline = 'middle'
  g.font = `110px ${EMOJI}`; g.fillText(glyph || '•', S / 2, S / 2 - 22)
  let size = 34
  g.font = `700 ${size}px ${FONT}`
  while (g.measureText(label).width > S - 56 && size > 14) { size -= 2; g.font = `700 ${size}px ${FONT}` }
  g.fillStyle = '#ffffff'; g.fillText(label, S / 2, S - 52)
  return canvasTexture(c)
}

class Builder {
  constructor(scene, nav) {
    this.group = new THREE.Group()
    scene.add(this.group)
    this.nav = nav
    this.seats = { team: [], lab: [], office: [], garden: [], kitchen: [], bathroom: [], coordinator: [], meeting: [] }
    this.level = 0                                                  // 0 = piano terra, 1 = secondo piano (usato mentre si costruisce)
  }

  mesh(geo, mat, x, y, z, { ry = 0, shadow = true, parent } = {}) {
    const m = new THREE.Mesh(geo, mat)
    m.position.set(x, y, z)
    m.rotation.y = ry
    m.castShadow = shadow
    m.receiveShadow = true
    ;(parent || this.group).add(m)
    return m
  }
  box(w, h, d, mat, x, y, z, opts) { return this.mesh(new THREE.BoxGeometry(w, h, d), mat, x, y, z, opts) }
  block(x, z, w, d, ry = 0, pad) { this.nav.blockRect(x, z, w, d, ry, pad) }

  // Muro sólido con remate superior (efecto de corte arquitectónico)
  wall(x0, z0, x1, z1, h = 1.8, t = 0.14) {
    const M = mats()
    const len = Math.hypot(x1 - x0, z1 - z0)
    const ry = -Math.atan2(z1 - z0, x1 - x0)
    const cx = (x0 + x1) / 2, cz = (z0 + z1) / 2
    this.mesh(new THREE.BoxGeometry(len, h, t), M.wall, cx, h / 2, cz, { ry })
    this.mesh(new THREE.BoxGeometry(len, 0.03, t + 0.02), M.wallCap, cx, h + 0.015, cz, { ry, shadow: false })
    this.block(cx, cz, len, t, ry, 0.25)
  }

  // Muro de vidrio con perfilería y base
  glassWall(x0, z0, x1, z1, h = 2.2, step = 1.5) {
    const M = mats()
    const len = Math.hypot(x1 - x0, z1 - z0)
    const ry = -Math.atan2(z1 - z0, x1 - x0)
    const cx = (x0 + x1) / 2, cz = (z0 + z1) / 2
    this.mesh(new THREE.BoxGeometry(len, h, 0.03), M.glass, cx, h / 2, cz, { ry, shadow: false })
    this.mesh(new THREE.BoxGeometry(len, 0.08, 0.08), M.frame, cx, 0.04, cz, { ry })
    this.mesh(new THREE.BoxGeometry(len, 0.06, 0.08), M.frame, cx, h, cz, { ry })
    const n = Math.max(1, Math.round(len / step))
    for (let i = 0; i <= n; i++) {
      const t = i / n
      this.box(0.06, h, 0.06, M.frame, x0 + (x1 - x0) * t, h / 2, z0 + (z1 - z0) * t)
    }
    this.block(cx, cz, len, 0.05, ry, 0.25)
  }

  floor(x0, z0, x1, z1, tex, y = 0.005, rough = 0.6) {
    const w = x1 - x0, d = z1 - z0
    const t = tex.clone()
    t.needsUpdate = true
    t.repeat.set(w / 4, d / 4)
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, d), new THREE.MeshStandardMaterial({ map: t, roughness: rough }))
    m.rotation.x = -Math.PI / 2
    m.position.set((x0 + x1) / 2, y, (z0 + z1) / 2)
    m.receiveShadow = true
    this.group.add(m)
  }

  officeChair(x, z, ry, seatMat) {
    const M = mats()
    const g = new THREE.Group()
    g.position.set(x, 0, z)
    g.rotation.y = ry
    this.group.add(g)
    const p = { parent: g }
    this.mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.3, 8), M.metal, 0, 0.2, 0, p)
    for (let i = 0; i < 5; i++) {
      const a = (i / 5) * Math.PI * 2
      const leg = this.mesh(new THREE.BoxGeometry(0.26, 0.025, 0.04), M.dark, Math.cos(a) * 0.13, 0.05, Math.sin(a) * 0.13, p)
      leg.rotation.y = -a
    }
    this.mesh(new THREE.BoxGeometry(0.44, 0.06, 0.42), seatMat, 0, SEAT_Y - 0.03, 0, p)
    this.mesh(new THREE.BoxGeometry(0.42, 0.46, 0.05), seatMat, 0, SEAT_Y + 0.25, -0.22, p)
  }

  laptop(x, y, z, ry = 0) {
    const M = mats()
    const g = new THREE.Group()
    g.position.set(x, y, z)
    g.rotation.y = ry
    this.group.add(g)
    this.mesh(new THREE.BoxGeometry(0.36, 0.015, 0.25), M.metal, 0, 0.008, 0, { parent: g })
    const screenMat = new THREE.MeshStandardMaterial({ color: '#16181d', roughness: 0.2, emissive: '#000000' })
    const screen = this.mesh(new THREE.BoxGeometry(0.36, 0.23, 0.012), screenMat, 0, 0.12, -0.13, { parent: g })
    screen.rotation.x = -0.22
    return screenMat
  }

  plant(x, z, s = 1) {
    const M = mats()
    const g = new THREE.Group()
    g.position.set(x, 0, z)
    g.scale.setScalar(s)
    this.group.add(g)
    const p = { parent: g }
    this.mesh(new THREE.CylinderGeometry(0.2, 0.15, 0.42, 16), M.pot, 0, 0.21, 0, p)
    this.mesh(new THREE.CylinderGeometry(0.18, 0.18, 0.02, 16), M.soil, 0, 0.41, 0, p)
    for (let i = 0; i < 9; i++) {
      const a = (i / 9) * Math.PI * 2
      const leaf = this.mesh(new THREE.ConeGeometry(0.07, 0.7, 5), i % 2 ? M.leaf : M.leaf2,
        Math.cos(a) * 0.08, 0.72, Math.sin(a) * 0.08, p)
      leaf.rotation.set(Math.sin(a) * 0.5, 0, -Math.cos(a) * 0.5)
    }
    this.block(x, z, 0.4 * s, 0.4 * s, 0, 0.15)
  }

  bookshelf(x, z, ry = 0, w = 1.4) {
    const M = mats()
    const g = new THREE.Group()
    g.position.set(x, 0, z)
    g.rotation.y = ry
    this.group.add(g)
    const p = { parent: g }
    this.mesh(new THREE.BoxGeometry(w, 1.6, 0.35), M.deskWood, 0, 0.8, 0, p)
    const colors = ['#c0392b', '#2c3e50', '#16a085', '#f39c12', '#8e44ad', '#ecf0f1']
    for (let row = 0; row < 4; row++) {
      this.mesh(new THREE.BoxGeometry(w - 0.06, 0.03, 0.3), M.white, 0, 0.18 + row * 0.38, 0.03, p)
      let bx = -w / 2 + 0.08
      let k = row * 7
      while (bx < w / 2 - 0.12) {
        const bw = 0.04 + ((k * 37) % 5) * 0.012
        const bh = 0.2 + ((k * 13) % 4) * 0.03
        this.mesh(new THREE.BoxGeometry(bw, bh, 0.22), new THREE.MeshStandardMaterial({ color: colors[k % colors.length], roughness: 0.7 }),
          bx + bw / 2, 0.2 + row * 0.38 + bh / 2, 0.05, p)
        bx += bw + 0.008
        k++
      }
    }
    this.block(x, z, w, 0.35, ry, 0.2)
  }

  sofa(x, z, ry, mat, w = 2.2) {
    const g = new THREE.Group()
    g.position.set(x, 0, z)
    g.rotation.y = ry
    this.group.add(g)
    const p = { parent: g }
    this.mesh(new THREE.BoxGeometry(w, 0.38, 0.85), mat, 0, 0.19, 0, p)
    this.mesh(new THREE.BoxGeometry(w, 0.5, 0.22), mat, 0, 0.55, -0.33, p)
    this.mesh(new THREE.BoxGeometry(0.2, 0.55, 0.85), mat, -w / 2 + 0.1, 0.3, 0, p)
    this.mesh(new THREE.BoxGeometry(0.2, 0.55, 0.85), mat, w / 2 - 0.1, 0.3, 0, p)
    this.block(x, z, w, 0.85, ry, 0.2)
  }

  // Insegna con il nome di un ambiente. `posts`: appoggiata a terra su due paletti; altrimenti appesa a una parete (y = altezza del centro).
  sign(text, x, y, z, w = 2.4, { posts = false } = {}) {
    const h = 0.5
    const board = this.box(w, h, 0.06, mats().dark, x, y, z, { shadow: false })
    const face = new THREE.Mesh(new THREE.PlaneGeometry(w - 0.1, h - 0.1), new THREE.MeshBasicMaterial({ map: signTexture(text, w / h), toneMapped: false }))
    face.position.set(x, y, z + 0.035)
    this.group.add(face)
    if (posts) for (const dx of [-w / 2 + 0.15, w / 2 - 0.15]) this.box(0.06, y - h / 2, 0.06, mats().metal, x + dx, (y - h / 2) / 2, z, { shadow: false })
    return board
  }

  // Mesa larga de trabajo con 4 puestos (2 por lado) y separador central
  pod(px, pz, chairMat, zone) {
    const M = mats()
    this.box(3.1, 0.05, 1.4, M.deskTop, px, 0.62, pz)
    for (const sx of [-1.45, 1.45]) this.box(0.06, 0.6, 1.3, M.metal, px + sx, 0.31, pz)
    this.box(3.0, 0.32, 0.04, zone === 'lab' ? M.fabricPurple : M.fabric, px, 0.8, pz)
    this.block(px, pz, 3.1, 1.4, 0, 0.22)
    for (const side of [-1, 1]) for (const dx of [-0.78, 0.78]) {
      const sz = pz + side * 1.02
      const ry = side < 0 ? 0 : Math.PI
      this.officeChair(px + dx, sz, ry, chairMat)
      const screen = this.laptop(px + dx, 0.645, pz + side * 0.42, ry + Math.PI)
      this.seats[zone].push({ x: px + dx, z: sz, rot: ry, seat: true, screen })
    }
  }

  // Escritorio de mando del coordinador: mesa en L, dos pantallas y un anillo holográfico
  coordinatorDesk(x, z) {
    const M = mats()
    this.box(2.4, 0.06, 0.95, M.deskTop, x, 0.64, z)
    this.box(0.9, 0.06, 0.8, M.deskTop, x + 1.6, 0.64, z + 0.45)
    for (const [dx, dz] of [[-1.15, 0], [1.15, 0], [1.98, 0.45]]) this.box(0.08, 0.62, 0.8, M.metal, x + dx, 0.31, z + dz)
    this.block(x + 0.3, z + 0.2, 3.3, 1.3, 0, 0.22)
    this.officeChair(x, z + 1.0, Math.PI, M.fabricPurple)
    const screen = this.laptop(x - 0.35, 0.67, z + 0.1, 0)
    this.laptop(x + 0.35, 0.67, z + 0.1, 0)
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.28, 0.018, 10, 40), M.purpleGlow)
    ring.rotation.x = Math.PI / 2
    ring.position.set(x + 1.6, 0.95, z + 0.45)
    this.group.add(ring)
    const core = new THREE.Mesh(new THREE.SphereGeometry(0.09, 18, 14), M.water)
    core.position.set(x + 1.6, 0.95, z + 0.45)
    this.group.add(core)
    this.coordinatorRing = ring
    this.seats.coordinator.push({ x, z: z + 1.0, rot: Math.PI, seat: true, screen })
  }

  // Ufficio privato di un agente: scrivania, sedia che guarda la telecamera. `frontZ` = parete sul corridoio, dove va l'insegna col nome (la scrive setAgentBadges).
  agentOffice(cx, chairZ, frontZ, coo = false) {
    const M = mats()
    const deskZ = chairZ + 1.02
    this.box(1.7, 0.05, 0.8, M.deskTop, cx, 0.62, deskZ)
    for (const sx of [-0.8, 0.8]) this.box(0.06, 0.6, 0.7, M.metal, cx + sx, 0.31, deskZ)
    this.box(1.6, 0.3, 0.04, M.fabricPurple, cx, 0.8, deskZ + 0.38)
    this.block(cx, deskZ, 1.75, 0.85, 0, 0.2)
    this.officeChair(cx, chairZ, 0, M.fabricPurple)
    const screen = this.laptop(cx, 0.645, deskZ - 0.25, Math.PI)
    const seat = { x: cx, z: chairZ, rot: 0, seat: true, screen, signZ: frontZ }
    if (!coo) { this.seats.lab.push(seat); return }
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.25, 0.018, 10, 40), M.purpleGlow)
    ring.rotation.x = Math.PI / 2
    ring.position.set(cx + 0.6, 0.95, deskZ)
    this.group.add(ring)
    const core = new THREE.Mesh(new THREE.SphereGeometry(0.08, 18, 14), M.water)
    core.position.set(cx + 0.6, 0.95, deskZ)
    this.group.add(core)
    this.coordinatorRing = ring
    this.seats.coordinator.push(seat)
  }

  bench(x, z, ry, n = 3) {
    const M = mats()
    const g = new THREE.Group()
    g.position.set(x, 0, z)
    g.rotation.y = ry
    this.group.add(g)
    const p = { parent: g }
    this.mesh(new THREE.BoxGeometry(2.3, 0.06, 0.48), M.deskWood, 0, SEAT_Y - 0.03, 0, p)
    this.mesh(new THREE.BoxGeometry(2.3, 0.42, 0.06), M.deskWood, 0, SEAT_Y + 0.22, -0.23, p)
    for (const sx of [-1.0, 1.0]) this.mesh(new THREE.BoxGeometry(0.06, SEAT_Y - 0.03, 0.42), M.frame, sx, (SEAT_Y - 0.03) / 2, 0, p)
    this.block(x, z, 2.3, 0.48, ry, 0.1)
    const c = Math.cos(ry), s = Math.sin(ry)
    for (let i = 0; i < n; i++) {
      const lx = (i - (n - 1) / 2) * 0.75
      this.seats.garden.push({ x: x + lx * c, z: z - lx * s, rot: ry, seat: true })
    }
  }
}

// ===== Secondo piano (Dismesso: sala riunioni portata al piano terra) =====
function buildUpper(scene, b, nav1, tex) {
  const up = new THREE.Group()
  up.position.y = FLOOR_H
  up.visible = false
  scene.add(up)
  b.upper = up
}

function buildOffice(scene, nav, nav1) {
  const M = mats()
  const b = new Builder(scene, nav)
  const wood = woodTexture('#c49466')
  const darkWood = woodTexture('#8a6242', 1, 1, 21)
  const tile = tileTexture('#e3e6ea', '#c3c8cf', 4)
  const plaza = tileTexture('#cfd3d8', '#b3b8bf', 2, 1, 1, 9)
  const carpet = carpetTexture('#6b6f7c')
  const labCarpet = carpetTexture('#3a3357', 1, 1, 17)

  // Esterno e pavimenti
  b.floor(-28, -22, 28, 22, plaza, -0.02, 0.85)
  b.box(B.x1 - B.x0 + 0.4, 0.2, B.z1 - B.z0 + 0.4, M.frame, 0, -0.1, 0, { shadow: false })
  b.floor(B.x0, B.z0, B.x1, B.z1, tile, 0.003)
  b.floor(B.x0, B.z0, B.x1, ZN, labCarpet, 0.006, 0.95)               // fila nord
  b.floor(B.x0, ZS, B.x1, B.z1, labCarpet, 0.006, 0.95)               // fila sud
  b.floor(B.x0, B.z0, B.x0 + CW, ZN, carpet, 0.007, 0.95)             // Direzione: moquette grigia
  b.floor(-15.7, ZN + 0.3, -5.0, ZS - 0.3, wood, 0.006)               // area squadra

  // Fioriere esterne
  for (const [x, z, w, d] of [[-21, 4, 2, 10], [4, 15, 16, 2]]) {
    b.box(w, 0.5, d, M.frame, x, 0.25, z)
    b.box(w - 0.2, 0.05, d - 0.2, M.soil, x, 0.5, z, { shadow: false })
    for (let i = 0; i < Math.max(w, d) / 1.6; i++) {
      const t = (i + 0.5) / Math.ceil(Math.max(w, d) / 1.6)
      const bx = w > d ? x - w / 2 + t * w : x, bz = w > d ? z : z - d / 2 + t * d
      const shrub = b.mesh(new THREE.IcosahedronGeometry(0.55, 1), M.leaf, bx, 0.95, bz)
      shrub.scale.y = 1.15
    }
  }

  // Involucro: nord e ovest pieni, sud ed est di vetro; una finestra sopra ogni ufficio nord
  b.wall(B.x0, B.z0, B.x1, B.z0, 2.8, 0.25)
  b.wall(B.x0, B.z0, B.x0, B.z1, 2.8, 0.25)
  b.glassWall(B.x0, B.z1, B.x1, B.z1, 2.6, 1.6)
  b.glassWall(B.x1, B.z0, B.x1, B.z1, 2.6, 1.6)
  for (let i = 0; i < COLS; i++) b.box(1.8, 1.2, 0.05, M.glass, B.x0 + CW * (i + 0.5), 1.6, B.z0 + 0.14, { shadow: false })

  // Gli uffici: due file identiche, pareti di vetro, una porta davanti a ognuno
  for (let i = 1; i < COLS; i++) {
    b.glassWall(B.x0 + CW * i, B.z0, B.x0 + CW * i, ZN, 2.2, 1.5)
    b.glassWall(B.x0 + CW * i, ZS, B.x0 + CW * i, B.z1, 2.2, 1.5)
  }
  for (const wallZ of [ZN, ZS]) {
    let x = B.x0
    for (let i = 0; i < COLS; i++) {
      const c = B.x0 + CW * (i + 0.5)
      b.glassWall(x, wallZ, c - DOOR_W / 2, wallZ, 2.2, 1.2)
      x = c + DOOR_W / 2
    }
    b.glassWall(x, wallZ, B.x1, wallZ, 2.2, 1.2)
  }
  b.box(B.x1 - B.x0, 0.04, 0.04, M.purpleGlow, 0, 0.02, ZN, { shadow: false })        // strisce luminose sul corridoio
  b.box(B.x1 - B.x0, 0.04, 0.04, M.purpleGlow, 0, 0.02, ZS, { shadow: false })

  // Ufficio Sentinella & SOC (ex Direzione, assegnato all'agente Sentinella di sorveglianza)
  const cd = B.x0 + CW / 2
  b.box(1.9, 0.06, 0.8, M.deskWood, cd, 0.62, -8.0)
  b.box(1.8, 0.6, 0.06, M.deskWood, cd, 0.31, -7.65)
  for (const sx of [-0.9, 0.9]) b.box(0.06, 0.6, 0.75, M.deskWood, cd + sx, 0.31, -8.0)
  b.block(cd, -8.0, 1.9, 0.8, 0, 0.22)
  b.officeChair(cd, -9.0, 0, M.leather)
  const dscreen = b.laptop(cd, 0.65, -8.15, Math.PI)
  b.box(0.08, 0.35, 0.08, M.dark, cd + 0.7, 0.82, -8.2)
  b.mesh(new THREE.ConeGeometry(0.12, 0.12, 16, 1, true), M.white, cd + 0.7, 1.02, -8.2)
  b.seats.office.push({ x: cd, z: -9.0, rot: 0, seat: true, screen: dscreen })
  b.officeChair(cd - 0.5, -6.9, Math.PI, M.fabric)
  b.officeChair(cd + 0.5, -6.9, Math.PI, M.fabric)
  b.bookshelf(B.x0 + CW - 0.35, -8.2, -Math.PI / 2, 1.6)
  b.plant(B.x0 + 0.5, B.z0 + 0.5, 1.0)
  b.sign('Sentinella SOC', cd, 1.95, ZN + 0.04, 2.0)

  // Un ufficio per agente: stessa scrivania, stessa libreria, stessa pianta. Il secondo a nord e' del COO (anello olografico)
  const ROWS = [{ chairZ: -9.0, frontZ: ZN, mid: -8.2, plantZ: B.z0 + 0.5 }, { chairZ: 6.9, frontZ: ZS, mid: 8.0, plantZ: B.z1 - 0.5 }]
  ROWS.forEach((row, r) => {
    for (let i = 0; i < COLS; i++) {
      if (r === 0 && i === 0) continue                                   // Direzione
      const x0 = B.x0 + CW * i
      b.agentOffice(x0 + CW / 2, row.chairZ, row.frontZ, r === 0 && i === 1)
      b.bookshelf(x0 + CW - 0.35, row.mid, -Math.PI / 2, 1.6)
      b.plant(x0 + 0.5, row.plantZ, 0.8)
    }
  })

  // Fascia centrale: Sala Riunioni & Collaborazione (a ovest, al posto dell'angolo relax)
  const rx0 = -14.2, rx1 = -6.4, rz0 = -3.8, rz1 = 3.8
  b.floor(rx0, rz0, rx1, rz1, darkWood, 0.012)
  b.wall(rx0, rz0, rx0, rz1, 2.2, 0.15)
  b.glassWall(rx0, rz0, rx1, rz0, 1.4, 1.6)
  b.glassWall(rx0, rz1, rx1, rz1, 1.4, 1.6)
  b.glassWall(rx1, rz0, rx1, -1.1, 1.4, 1.2)
  b.glassWall(rx1, 1.1, rx1, rz1, 1.4, 1.2)

  // Tavolo conferenze centrale
  const tcx = (rx0 + rx1) / 2
  b.box(5.8, 0.06, 1.4, M.deskWood, tcx, 0.62, 0)
  for (const sx of [-1.8, 1.8]) b.box(0.5, 0.6, 0.5, M.metal, tcx + sx, 0.3, 0)
  b.block(tcx, 0, 5.8, 1.4, 0, 0.22)

  // Schermo TV smart per presentazioni e coordinamento
  b.box(0.06, 1.4, 3.2, M.tv, rx0 + 0.1, 1.45, 0)

  // 12 Posti a sedere (6 a nord, 6 a sud)
  for (const dx of [-2.2, -1.3, -0.4, 0.4, 1.3, 2.2]) {
    b.officeChair(tcx + dx, -1.05, 0, M.fabricPurple)
    b.officeChair(tcx + dx, 1.05, Math.PI, M.fabricPurple)
    const s1 = { x: tcx + dx, z: -1.05, rot: 0, seat: true, level: 0 }
    const s2 = { x: tcx + dx, z: 1.05, rot: Math.PI, seat: true, level: 0 }
    b.seats.meeting.push(s1, s2)
    b.seats.team.push(s1, s2)
  }

  // Piante decorative agli angoli della sala
  b.plant(rx0 + 0.6, rz0 + 0.6, 1.0)
  b.plant(rx0 + 0.6, rz1 - 0.6, 1.0)
  b.plant(rx1 - 0.6, rz0 + 0.6, 0.9)
  b.plant(rx1 - 0.6, rz1 - 0.6, 0.9)

  // Insegna della sala riunioni
  b.sign('Allineamento & Collaborazione', tcx, 1.4, ZN + 1.2, 3.2, { posts: true })

  const gw = GARDEN.x1 - GARDEN.x0, gd = GARDEN.z1 - GARDEN.z0
  const gcx = (GARDEN.x0 + GARDEN.x1) / 2, gcz = (GARDEN.z0 + GARDEN.z1) / 2
  b.box(gw, 0.42, gd, M.deskWood, gcx, 0.21, gcz)
  b.box(gw - 0.24, 0.05, gd - 0.24, M.grass, gcx, 0.44, gcz, { shadow: false })
  b.block(gcx, gcz, gw, gd, 0, 0.25)
  b.mesh(new THREE.CylinderGeometry(0.16, 0.24, 1.7, 10), new THREE.MeshStandardMaterial({ color: '#6b4a2d', roughness: 0.9 }), gcx - 0.8, 1.3, gcz - 0.5)
  b.mesh(new THREE.IcosahedronGeometry(1.25, 1), M.leaf, gcx - 0.8, 2.55, gcz - 0.5)
  b.mesh(new THREE.IcosahedronGeometry(0.8, 1), M.leaf2, gcx - 0.2, 2.95, gcz - 0.1)
  b.mesh(new THREE.CylinderGeometry(0.75, 0.75, 0.04, 32), M.water, gcx + 1.4, 0.47, gcz + 1.4, { shadow: false })
  ;[[-2.4, -0.8], [2.4, -0.9], [-2.3, 2.4], [0.8, 2.6], [2.5, 1.3]].forEach(([x, z], i) => {
    b.mesh(new THREE.IcosahedronGeometry(0.42, 1), i % 2 ? M.leaf : M.leaf2, x, 0.78, z)
  })
  b.bench(gcx, GARDEN.z0 - 0.75, 0)
  b.bench(gcx, GARDEN.z1 + 0.75, Math.PI)
  b.bench(GARDEN.x0 - 0.75, gcz, Math.PI / 2)
  b.bench(GARDEN.x1 + 0.75, gcz, -Math.PI / 2)
  b.plant(8.2, -3.9, 1.2)
  b.plant(8.2, 3.9, 1.2)

  // Area Tech & Lounge a est (ex vano ascensore)
  b.floor(10.8, -2.5, 14.4, 2.5, plaza, 0.008)
  b.bookshelf(12.6, -1.8, 0, 2.4)
  b.plant(11.2, 2.0, 1.1)
  b.plant(14.0, 2.0, 1.1)
  b.bench(12.6, 1.8, 0, 3)
  b.lift = new THREE.Group()

  // Insegna Giardino
  b.sign('Giardino', 0, 1.3, GARDEN.z0 - 1.5, 1.8, { posts: true })

  return b
}

// ===== Escena =====
export class OfficeScene {
  constructor(container, { onHover, onSelect } = {}) {
    this.container = container
    this.onHover = onHover
    this.onSelect = onSelect
    this.chars = new Map()
    this.selectedId = null
    this.clock = new THREE.Clock()
    this.keys = new Set()

    const w = container.clientWidth, h = container.clientHeight
    this.highQuality = window.innerWidth > 900

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, this.highQuality ? 2 : 1.5))
    this.renderer.setSize(w, h)
    this.renderer.shadowMap.enabled = true
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping
    this.renderer.toneMappingExposure = 1.05
    container.appendChild(this.renderer.domElement)

    this.labels = new CSS2DRenderer()
    this.labels.setSize(w, h)
    Object.assign(this.labels.domElement.style, { position: 'absolute', top: '0', left: '0', pointerEvents: 'none' })
    container.appendChild(this.labels.domElement)

    this.scene = new THREE.Scene()
    const pmrem = new THREE.PMREMGenerator(this.renderer)
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture
    this.scene.environmentIntensity = 0.55
    pmrem.dispose()

    // Cámara isométrica fija (sin rotación)
    this.viewSize = 22
    const aspect = w / h
    this.camera = new THREE.OrthographicCamera(
      -this.viewSize * aspect / 2, this.viewSize * aspect / 2, this.viewSize / 2, -this.viewSize / 2, 0.1, 300)
    this.camera.position.copy(ISO_DIR).multiplyScalar(48).add(HOME)
    this.camera.zoom = aspect < 1 ? 1.85 : 1.75
    this.camera.updateProjectionMatrix()
    this.camera.lookAt(HOME.x, HOME.y, HOME.z)

    this.controls = new OrbitControls(this.camera, this.renderer.domElement)
    this.controls.enableRotate = false
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.12
    this.controls.screenSpacePanning = false
    this.controls.zoomToCursor = true
    this.controls.minZoom = 0.7
    this.controls.maxZoom = 5
    this.controls.mouseButtons = { LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN }
    this.controls.touches = { ONE: THREE.TOUCH.PAN, TWO: THREE.TOUCH.DOLLY_PAN }
    this.controls.target.copy(HOME)
    this.targetFocus = null

    // Iluminación: cielo suave + sol con sombras + entorno para reflejos
    this.scene.add(new THREE.HemisphereLight('#ffffff', '#c8bfae', 0.45))
    const sun = new THREE.DirectionalLight('#fff4e5', 2.6)
    sun.position.set(14, 26, 10)
    sun.castShadow = true
    sun.shadow.mapSize.set(this.highQuality ? 4096 : 2048, this.highQuality ? 4096 : 2048)
    Object.assign(sun.shadow.camera, { left: -24, right: 24, top: 20, bottom: -20, near: 1, far: 80 })
    sun.shadow.bias = -0.0004
    sun.shadow.normalBias = 0.02
    this.scene.add(sun)

    this.nav = new NavGrid(B.x0, B.z0, B.x1, B.z1, 0.2)
    this.nav1 = new NavGrid(B.x0, ZN, B.x1, ZS, 0.2)                   // griglia dei percorsi del secondo piano (sopra la fascia centrale)
    this.effects = []                                                 // animazioni a tempo (il missile)
    this.liftUser = null                                              // chi sta usando l'ascensore adesso
    this.builder = buildOffice(this.scene, this.nav, this.nav1)
    this.lift = this.builder.lift
    this.upper = this.builder.upper                                   // il secondo piano: nascosto finche' non serve
    this.upperShown = false
    this.upperHover = false                                           // il cursore e' sopra il punto in cui sta il secondo piano
    this.upperPinned = false                                          // il pulsante "Piano 2"
    this.upperDrop = 0
    this.seats = this.builder.seats
    this.charGroup = new THREE.Group()
    this.scene.add(this.charGroup)

    // Post-proceso: oclusión ambiental para sombras de contacto
    this.composer = new EffectComposer(this.renderer)
    this.composer.addPass(new RenderPass(this.scene, this.camera))
    if (this.highQuality) {
      this.gtao = new GTAOPass(this.scene, this.camera, w, h)
      this.gtao.updateGtaoMaterial({ radius: 0.5, distanceExponent: 1.4, thickness: 1, scale: 1 })
      this.gtao.blendIntensity = 0.85
      this.composer.addPass(this.gtao)
    }
    this.composer.addPass(new OutputPass())

    // Interacción
    this.raycaster = new THREE.Raycaster()
    this.pointer = new THREE.Vector2()
    this.downAt = null
    const el = this.renderer.domElement
    this._onMove = (e) => { this.updateUpperHover(e); this.handlePointer(e, false) }
    this._onDown = (e) => { this.downAt = [e.clientX, e.clientY] }
    this._onUp = (e) => {
      if (e.button === 0 && this.downAt && Math.hypot(e.clientX - this.downAt[0], e.clientY - this.downAt[1]) < 6) this.handlePointer(e, true)
      this.downAt = null
    }
    this._onLeave = () => { this.upperHover = false; this.onHover?.(null) }
    this._onContext = (e) => e.preventDefault()
    el.addEventListener('pointermove', this._onMove)
    el.addEventListener('pointerdown', this._onDown)
    el.addEventListener('pointerup', this._onUp)
    el.addEventListener('pointerleave', this._onLeave)
    el.addEventListener('contextmenu', this._onContext)

    // Teclado estilo juego: WASD / flechas para moverse, Q/E para alejar/acercar
    const typing = () => {
      const a = document.activeElement
      return a && (/INPUT|TEXTAREA|SELECT/.test(a.tagName) || a.isContentEditable) || document.querySelector('.modal-overlay')
    }
    const KEYS = { w: 'up', arrowup: 'up', s: 'down', arrowdown: 'down', a: 'left', arrowleft: 'left', d: 'right', arrowright: 'right', q: 'rotL', e: 'rotR', '+': 'in', '=': 'in', '-': 'out' }
    this._onKeyDown = (e) => {
      const k = KEYS[e.key.toLowerCase()]
      if (!k || typing()) return
      this.keys.add(k)
      if (e.key.startsWith('Arrow')) e.preventDefault()
    }
    this._onKeyUp = (e) => { const k = KEYS[e.key.toLowerCase()]; if (k) this.keys.delete(k) }
    this._onBlur = () => this.keys.clear()
    window.addEventListener('keydown', this._onKeyDown)
    window.addEventListener('keyup', this._onKeyUp)
    window.addEventListener('blur', this._onBlur)

    this.resizeObserver = new ResizeObserver(() => this.resize())
    this.resizeObserver.observe(container)

    this.running = true
    this.animate = this.animate.bind(this)
    requestAnimationFrame(this.animate)
  }

  // list: [{ place, glyph, label, color }]. Un tappeto col simbolo dello strumento piu' usato dietro la sedia di ogni agente; si ridisegna solo se cambia qualcosa.
  setAgentBadges(list) {
    const key = JSON.stringify(list)
    if (key === this.badgeKey) return
    this.badgeKey = key
    if (this.badges) {
      this.scene.remove(this.badges)
      this.badges.traverse(o => { o.material?.map?.dispose(); o.material?.dispose(); o.geometry?.dispose() })
    }
    this.badges = new THREE.Group()
    for (const a of list) {
      const spot = this.seatFor(a.place)
      const behind = spot.rot === 0 ? -0.95 : 0.95                      // le sedute guardano verso la scrivania: il tappeto sta sul lato libero
      const m = new THREE.Mesh(new THREE.PlaneGeometry(1.05, 1.05), new THREE.MeshBasicMaterial({ map: badgeTexture(a), transparent: true, toneMapped: false }))
      m.rotation.order = 'YXZ'
      m.rotation.set(-Math.PI / 2, Math.PI / 4, 0)                      // il lato alto del disegno punta in alto sullo schermo (telecamera isometrica)
      m.position.set(spot.x, 0.014, spot.z + behind)
      this.badges.add(m)
      if (spot.signZ !== undefined) {                                   // insegna col nome dell'agente sopra la porta del suo ufficio
        const w = 2.0, h = 0.5
        const board = new THREE.Mesh(new THREE.BoxGeometry(w, h, 0.06), mats().dark)
        board.position.set(spot.x, 1.95, spot.signZ + 0.04)
        const face = new THREE.Mesh(new THREE.PlaneGeometry(w - 0.1, h - 0.1), new THREE.MeshBasicMaterial({ map: signTexture(a.name, w / h), toneMapped: false }))
        face.position.set(spot.x, 1.95, spot.signZ + 0.075)
        this.badges.add(board, face)
      }
    }
    this.scene.add(this.badges)
  }

  seatFor(place) {
    const pick = (arr) => arr.length ? arr[((place.index || 0) % arr.length + arr.length) % arr.length] : { x: 0, z: 8, rot: 0, seat: false }
    switch (place.type) {
      case 'desk': return pick(place.zone === 'lab' ? this.seats.lab : this.seats.team)
      case 'office': return pick(this.seats.office)
      case 'kitchen': return pick(this.seats.kitchen)
      case 'bathroom': return pick(this.seats.bathroom)
      case 'coordinator': return pick(this.seats.coordinator)
      case 'meeting': return pick(this.seats.meeting)
      case 'visit': {
        // De pie al lado del puesto de otro personaje, mirándolo
        const target = this.chars.get(place.targetId)?.spot || { x: 0, z: 0, rot: 0, level: 0 }
        const side = place.side || 1
        const ox = Math.cos(target.rot) * 0.75 * side, oz = -Math.sin(target.rot) * 0.75 * side
        let x = target.x + ox, z = target.z + oz
        if (this.nav && !this.nav.free(...this.nav.toCell(x, z))) { x = target.x - ox; z = target.z - oz }
        return { x, z, rot: Math.atan2(target.x - x, target.z - z), seat: false, level: target.level || 0 }
      }
      default: return pick(this.seats.garden)
    }
  }

  hitCharacter(e) {
    const rect = this.renderer.domElement.getBoundingClientRect()
    this.pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
    this.pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1
    this.raycaster.setFromCamera(this.pointer, this.camera)
    const hits = this.raycaster.intersectObjects(this.charGroup.children, true)
    for (const h of hits) {
      let o = h.object
      while (o && !o.userData.charId) o = o.parent
      if (o && o.visible) return o.userData.charId                     // chi sta al piano nascosto non si puo' toccare
    }
    return null
  }

  // Il cursore e' sopra il punto in cui starebbe il secondo piano? (raggio contro un piano a quota FLOOR_H, sopra la fascia centrale)
  updateUpperHover(e) {
    const rect = this.renderer.domElement.getBoundingClientRect()
    const v = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1)
    this.raycaster.setFromCamera(v, this.camera)
    const p = new THREE.Vector3()
    const hit = this.raycaster.ray.intersectPlane(new THREE.Plane(UP, -FLOOR_H), p)
    this.upperHover = !!hit && p.x > B.x0 - 0.3 && p.x < B.x1 + 0.3 && p.z > ZN - 0.3 && p.z < ZS + 0.3
  }

  setUpperPinned(v) { this.upperPinned = !!v }

  // Tutti i personaggi sono sempre visibili sul piano unico
  updateUpper(dt) {
    for (const ch of this.chars.values()) {
      ch.root.visible = true
      if (ch.label) ch.label.visible = true
    }
  }

  handlePointer(e, isClick) {
    if (!isClick && e.buttons) return
    const id = this.hitCharacter(e)
    this.renderer.domElement.style.cursor = id ? 'pointer' : 'grab'
    if (isClick) { if (id) this.onSelect?.(id) }
    else this.onHover?.(id, e.clientX, e.clientY)
  }

  resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight
    if (!w || !h) return
    const aspect = w / h
    Object.assign(this.camera, {
      left: -this.viewSize * aspect / 2, right: this.viewSize * aspect / 2,
      top: this.viewSize / 2, bottom: -this.viewSize / 2,
    })
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(w, h)
    this.composer.setSize(w, h)
    this.labels.setSize(w, h)
  }

  // list: [{ id, name, look, place: {type, zone, index}, activity, statusColor }]
  setCharacters(list) {
    const seen = new Set()
    for (const c of list) {
      seen.add(c.id)
      let ch = this.chars.get(c.id)
      const lookKey = JSON.stringify(c.look)
      if (ch && ch.lookKey !== lookKey) { this.removeChar(c.id); ch = null }
      const spot = this.seatFor(c.place)
      const placeKey = `${c.place.type}:${c.place.zone || ''}:${c.place.index}:${c.place.targetId || ''}`

      if (!ch) {
        const root = createMinifig(c.look)
        root.userData.charId = c.id
        const labelEl = document.createElement('div')
        labelEl.className = 'office-label'
        const label = new CSS2DObject(labelEl)
        label.position.set(0, 1.3, 0)
        root.add(label)
        root.position.set(spot.x, (spot.level || 0) * FLOOR_H, spot.z)
        root.rotation.y = spot.rot
        this.charGroup.add(root)
        ch = { root, labelEl, label, lookKey, placeKey, spot, route: [], phase: Math.random() * 6, level: spot.level || 0, riding: false, replan: false }
        this.chars.set(c.id, ch)
      } else if (ch.placeKey !== placeKey) {
        ch.placeKey = placeKey
        ch.spot = spot
        if (ch.riding) ch.replan = true                               // e' in ascensore: il percorso nuovo si calcola quando scende
        else ch.route = this.planRoute([ch.root.position.x, ch.root.position.z], ch.level, spot)
      }
      ch.activity = c.activity
      ch.place = c.place
      ch.labelEl.innerHTML = `<span class="office-dot" style="background:${c.statusColor}"></span>${c.name}`
    }
    for (const id of [...this.chars.keys()]) if (!seen.has(id)) this.removeChar(id)
    this.applySelection()

    // Centra e avvicina automaticamente la telecamera sul gruppo più denso di agenti
    const agentCount = [...this.chars.keys()].filter(id => id.startsWith('a:')).length
    if (agentCount > 0 && this.centeredAgentCount !== agentCount) {
      this.centeredAgentCount = agentCount
      this.hasAutoCentered = true
      this.resetView()
    } else if (!this.hasAutoCentered && this.chars.size > 0) {
      this.hasAutoCentered = true
      this.resetView()
    }
  }

  // Baricentro (centroid) del cluster principale dove si trova la maggior parte degli agenti
  getAgentsCentroid() {
    if (!this.chars || this.chars.size === 0) return new THREE.Vector3(HOME.x, 0.4, HOME.z)
    const agentEntries = [...this.chars.entries()].filter(([id, ch]) => id.startsWith('a:') && ch.root && ch.root.visible !== false)
    const pool = agentEntries.length > 0 ? agentEntries.map(([, ch]) => ch) : [...this.chars.values()].filter(ch => ch.root && ch.root.visible !== false)
    if (pool.length === 0) return new THREE.Vector3(HOME.x, 0.4, HOME.z)

    // Trova il punto con la massima densità locale (raggio 6.5 unità) così la camera inquadra sempre da vicino il gruppo più numeroso
    let bestCluster = pool
    if (pool.length > 3) {
      let maxNeighbors = 0
      for (const candidate of pool) {
        const neighbors = pool.filter(other =>
          Math.hypot(other.root.position.x - candidate.root.position.x, other.root.position.z - candidate.root.position.z) <= 6.5
        )
        if (neighbors.length > maxNeighbors) {
          maxNeighbors = neighbors.length
          bestCluster = neighbors
        }
      }
    }

    let sx = 0, sz = 0
    for (const ch of bestCluster) {
      sx += ch.root.position.x
      sz += ch.root.position.z
    }
    return new THREE.Vector3(sx / bestCluster.length, 0.4, sz / bestCluster.length)
  }

  // Percorso diretto sul piano terra
  planRoute(from, level, spot) {
    const to = [spot.x, spot.z]
    return this.nav.findPath(from, to)
  }

  // ¿Ya llegó a su destino? (lo usa la Oficina para encadenar entregas)
  arrived(id) {
    const ch = this.chars.get(id)
    if (!ch) return true
    return !ch.route.length && Math.hypot(ch.root.position.x - ch.spot.x, ch.root.position.z - ch.spot.z) < 0.2
  }

  removeChar(id) {
    const ch = this.chars.get(id)
    if (!ch) return
    if (this.liftUser === ch) this.liftUser = null
    ch.labelEl.remove()
    this.charGroup.remove(ch.root)
    ch.root.traverse(o => {
      if (o.isMesh) {
        o.geometry.dispose()
        ;(Array.isArray(o.material) ? o.material : [o.material]).forEach(m => { m.map?.dispose(); m.dispose() })
      }
    })
    this.chars.delete(id)
  }

  select(id, focus = true) {
    this.selectedId = id
    this.applySelection()
    const ch = id && this.chars.get(id)
    // un personaggio al secondo piano, con la telecamera isometrica, appare spostato: si compensa l'altezza
    if (ch && focus) { const k = ch.root.position.y / 1.15; this.targetFocus = new THREE.Vector3(ch.root.position.x - k, 0, ch.root.position.z - k) }
  }

  applySelection() {
    for (const [id, ch] of this.chars) ch.root.userData.parts.ring.visible = id === this.selectedId
  }

  resetView() {
    // Inquadra isometricamente da vicino il cluster dove si trova la maggior parte degli agenti
    const centroid = this.getAgentsCentroid()
    const t = this.controls.target
    t.set(centroid.x, 0.4, centroid.z)
    this.camera.position.copy(t).add(ISO_DIR.clone().multiplyScalar(48))
    this.camera.lookAt(t)
    this.orbitVel = 0
    this.targetFocus = null
    const aspect = (this.container.clientWidth || 1) / (this.container.clientHeight || 1)
    this.camera.zoom = aspect < 1 ? 1.85 : 1.75
    this.camera.updateProjectionMatrix()
  }

  // Mueve cámara y objetivo juntos sobre el piso, sin salirse de la oficina
  panBy(dx, dz) {
    const t = this.controls.target
    const nx = Math.min(B.x1 + 4, Math.max(B.x0 - 4, t.x + dx))
    const nz = Math.min(B.z1 + 4, Math.max(B.z0 - 4, t.z + dz))
    const mx = nx - t.x, mz = nz - t.z
    t.x += mx; t.z += mz
    this.camera.position.x += mx; this.camera.position.z += mz
  }

  handleKeys(dt) {
    // Órbita suave con Q / E: acelera y frena, gira alrededor del punto que se mira
    const want = (this.keys.has('rotR') ? 1 : 0) - (this.keys.has('rotL') ? 1 : 0)
    this.orbitVel = (this.orbitVel || 0) + (want * 0.9 - (this.orbitVel || 0)) * Math.min(1, dt * 5)
    if (Math.abs(this.orbitVel) > 0.002) {
      const t = this.controls.target
      const off = this.camera.position.clone().sub(t).applyAxisAngle(UP, this.orbitVel * dt)
      this.camera.position.copy(t).add(off)
      this.camera.lookAt(t)
    }
    if (!this.keys.size) return
    const speed = 12 * dt / this.camera.zoom
    // Adelante y derecha según hacia dónde mira la cámara (sirve aunque se haya girado)
    const fwd = this.controls.target.clone().sub(this.camera.position).setY(0).normalize()
    const right = new THREE.Vector3(-fwd.z, 0, fwd.x)
    const v = new THREE.Vector3()
    if (this.keys.has('up')) v.add(fwd)
    if (this.keys.has('down')) v.sub(fwd)
    if (this.keys.has('right')) v.add(right)
    if (this.keys.has('left')) v.sub(right)
    if (v.lengthSq()) { v.normalize().multiplyScalar(speed); this.panBy(v.x, v.z); this.targetFocus = null }
    if (this.keys.has('in') || this.keys.has('out')) {
      const f = this.keys.has('in') ? 1 + 1.6 * dt : 1 / (1 + 1.6 * dt)
      this.camera.zoom = Math.min(this.controls.maxZoom, Math.max(this.controls.minZoom, this.camera.zoom * f))
      this.camera.updateProjectionMatrix()
    }
  }

  animateChar(ch, dt, t) {
    const { body, legL, legR, armL, armR, cup, doc } = ch.root.userData.parts
    const carrying = ch.activity === 'carry'
    if (doc) doc.visible = carrying
    const p = ch.root.position
    let walking = false

    if (ch.route.length && !Array.isArray(ch.route[0])) {              // ascensore: una persona alla volta, gli altri aspettano il loro turno
      if (!ch.riding && this.liftUser && this.liftUser !== ch) {
        walking = false
      } else {
        const to = ch.route[0].lift ? FLOOR_H : 0
        if (!ch.riding) { ch.riding = true; this.liftUser = ch; if (this.lift) this.lift.position.y = p.y }
        const dy = to - p.y, step = LIFT_SPEED * dt
        if (Math.abs(dy) <= step) {
          p.y = to
          ch.level = ch.route[0].lift
          ch.riding = false
          this.liftUser = null
          ch.route.shift()
          if (ch.replan) { ch.replan = false; ch.route = this.planRoute([p.x, p.z], ch.level, ch.spot) }
        } else p.y += Math.sign(dy) * step
        if (this.lift) this.lift.position.y = p.y
        walking = false
      }
    } else if (ch.route.length) {
      const [tx, tz] = ch.route[0]
      const dx = tx - p.x, dz = tz - p.z
      const d = Math.hypot(dx, dz)
      const step = WALK_SPEED * dt
      if (d <= step) { p.x = tx; p.z = tz; ch.route.shift() }
      else { p.x += (dx / d) * step; p.z += (dz / d) * step }
      if (d > 0.001) {
        // Giro suave hacia la dirección de marcha
        const target = Math.atan2(dx, dz)
        let diff = target - ch.root.rotation.y
        diff = Math.atan2(Math.sin(diff), Math.cos(diff))
        ch.root.rotation.y += diff * Math.min(1, dt * 12)
      }
      walking = ch.route.length > 0
    }

    if (!ch.riding) p.y = ch.level * FLOOR_H
    const arrived = !ch.route.length
    if (arrived) {
      let diff = ch.spot.rot - ch.root.rotation.y
      diff = Math.atan2(Math.sin(diff), Math.cos(diff))
      ch.root.rotation.y += diff * Math.min(1, dt * 10)
    }
    const sitting = arrived && ch.spot.seat
    cup.visible = arrived && ch.activity === 'drink'

    if (walking) {
      const s = Math.sin(t * 10 + ch.phase)
      legL.rotation.x = s * 0.55
      legR.rotation.x = -s * 0.55
      armL.rotation.x = -s * 0.45
      armR.rotation.x = carrying ? -0.95 : s * 0.45
      body.position.y = Math.abs(s) * 0.022
      return
    }
    if (sitting) {
      body.position.y = SEAT_Y - 0.33
      legL.rotation.x = legR.rotation.x = -Math.PI / 2
    } else {
      body.position.y = 0
      legL.rotation.x = legR.rotation.x = 0
    }
    switch (ch.activity) {
      case 'typing':
        armL.rotation.x = -1.2 + Math.sin(t * 16 + ch.phase) * 0.09
        armR.rotation.x = -1.2 + Math.sin(t * 16 + ch.phase + 1.7) * 0.09
        break
      case 'drink':
        armL.rotation.x = -0.15
        armR.rotation.x = -2.1 + Math.sin(t * 1.4 + ch.phase) * 0.22
        break
      case 'relax':
        armL.rotation.x = armR.rotation.x = -0.35 + Math.sin(t * 1.1 + ch.phase) * 0.05
        break
      case 'carry':
        armL.rotation.x = Math.sin(t * 1.4 + ch.phase) * 0.06
        armR.rotation.x = -0.95
        break
      case 'paused':
        armL.rotation.x = -0.6
        armR.rotation.x = -0.2 + Math.sin(t * 0.8 + ch.phase) * 0.05
        break
      default:
        armL.rotation.x = Math.sin(t * 1.4 + ch.phase) * 0.06
        armR.rotation.x = -Math.sin(t * 1.4 + ch.phase) * 0.06
    }
  }

  animate() {
    if (!this.running) return
    requestAnimationFrame(this.animate)
    const dt = Math.min(this.clock.getDelta(), 0.05)
    const t = this.clock.elapsedTime
    this.handleKeys(dt)

    const allScreens = [...this.seats.team, ...this.seats.lab, ...this.seats.office, ...this.seats.coordinator].filter(s => s.screen)
    for (const s of allScreens) s.screen.emissive.set('#000000')
    for (const ch of this.chars.values()) {
      this.animateChar(ch, dt, t)
      if (!ch.route.length && ch.spot.screen && ch.activity === 'typing') {
        ch.spot.screen.emissive.set(ch.place?.zone === 'lab' || ch.place?.type === 'coordinator' ? '#7c4dff' : '#3aa0ff')
      }
    }
    this.updateUpper(dt)
    if (this.effects.length) this.effects = this.effects.filter(e => !e.update(dt))
    // Anillo holográfico del escritorio del coordinador
    const ring = this.builder?.coordinatorRing
    if (ring) { ring.rotation.z += dt * 0.8; ring.position.y = 0.95 + Math.sin(t * 1.6) * 0.03 }

    if (this.targetFocus) {
      const t0 = this.controls.target
      const nx = t0.x + (this.targetFocus.x - t0.x) * 0.12
      const nz = t0.z + (this.targetFocus.z - t0.z) * 0.12
      this.panBy(nx - t0.x, nz - t0.z)
      if (Math.hypot(this.targetFocus.x - t0.x, this.targetFocus.z - t0.z) < 0.05) this.targetFocus = null
    }
    this.controls.update()
    this.composer.render()
    this.labels.render(this.scene, this.camera)
  }

  // Il lancio: un missile parte dal tetto dell'ufficio indicato (`place`, di solito quello del Deployer) e sale nel cielo con fumo e fiamma.
  launchRocket(place) {
    const spot = this.seatFor(place)
    const g = new THREE.Group()
    const mat = (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.4, metalness: 0.2, ...extra })
    const add = (geo, m, x, y, z) => { const o = new THREE.Mesh(geo, m); o.position.set(x, y, z); g.add(o); return o }
    add(new THREE.CylinderGeometry(0.2, 0.2, 1.1, 20), mat('#f4f4f8'), 0, 0.55, 0)
    add(new THREE.ConeGeometry(0.2, 0.45, 20), mat('#e5383b'), 0, 1.32, 0)
    add(new THREE.SphereGeometry(0.09, 12, 10), mat('#3aa0ff', { emissive: '#3aa0ff', emissiveIntensity: 0.6 }), 0, 0.75, 0.19)
    for (let i = 0; i < 3; i++) {
      const fin = add(new THREE.BoxGeometry(0.04, 0.4, 0.22), mat('#e5383b'), 0, 0.14, 0)
      fin.position.set(Math.sin(i * 2.094) * 0.24, 0.14, Math.cos(i * 2.094) * 0.24)
      fin.rotation.y = i * 2.094
    }
    const flameMat = new THREE.MeshBasicMaterial({ color: '#ffb703' })
    const flame = add(new THREE.ConeGeometry(0.15, 0.7, 14), flameMat, 0, -0.35, 0)
    flame.rotation.x = Math.PI
    g.scale.setScalar(1.25)
    const baseY = 2.45                                                  // sopra il vetro dell'ufficio
    g.position.set(spot.x, baseY, spot.z - (spot.rot === 0 ? 0.9 : -0.9))
    this.scene.add(g)
    const puffs = []
    const puffMat = () => new THREE.MeshBasicMaterial({ color: '#e8e8ee', transparent: true, opacity: 0.75, depthWrite: false })
    const puff = (x, y, z, s) => { const m = new THREE.Mesh(new THREE.SphereGeometry(0.2, 10, 8), puffMat()); m.position.set(x, y, z); m.scale.setScalar(s); this.scene.add(m); puffs.push({ m, age: 0 }) }
    for (let i = 0; i < 9; i++) puff(g.position.x + (Math.random() - 0.5) * 0.9, baseY + 0.1, g.position.z + (Math.random() - 0.5) * 0.9, 1.2 + Math.random())   // nube all'accensione
    this.targetFocus = new THREE.Vector3(spot.x, 0, spot.z)
    const fx = { t: 0, puffT: 0 }
    fx.update = (dt) => {
      fx.t += dt
      const t = fx.t
      const lift = Math.max(0, t - 0.9)                                  // 0,9 s di accensione, poi il decollo
      g.position.y = baseY + 1.1 * lift * lift
      g.position.x = spot.x + (t < 0.9 ? Math.sin(t * 60) * 0.015 : 0)    // vibra sulla rampa
      flame.scale.set(1 + Math.sin(t * 40) * 0.12, (t < 0.9 ? 0.5 : 1.3) + Math.sin(t * 55) * 0.25, 1)
      fx.puffT -= dt
      if (fx.puffT <= 0 && g.position.y < 60) { fx.puffT = 0.05; puff(g.position.x, g.position.y - 0.5, g.position.z, 0.7) }
      for (const p of puffs) {
        p.age += dt
        p.m.scale.multiplyScalar(1 + dt * 1.6)
        p.m.material.opacity = Math.max(0, 0.75 * (1 - p.age / 1.8))
      }
      for (let i = puffs.length - 1; i >= 0; i--) if (puffs[i].age > 1.8) { this.scene.remove(puffs[i].m); puffs[i].m.geometry.dispose(); puffs[i].m.material.dispose(); puffs.splice(i, 1) }
      if (g.position.y > 70 && !puffs.length) {
        this.scene.remove(g)
        g.traverse(o => { if (o.isMesh) { o.geometry.dispose(); o.material.dispose() } })
        return true
      }
      return false
    }
    this.effects.push(fx)
  }

  dispose() {
    this.running = false
    this.resizeObserver.disconnect()
    const el = this.renderer.domElement
    el.removeEventListener('pointermove', this._onMove)
    el.removeEventListener('pointerdown', this._onDown)
    el.removeEventListener('pointerup', this._onUp)
    el.removeEventListener('pointerleave', this._onLeave)
    el.removeEventListener('contextmenu', this._onContext)
    window.removeEventListener('keydown', this._onKeyDown)
    window.removeEventListener('keyup', this._onKeyUp)
    window.removeEventListener('blur', this._onBlur)
    this.controls.dispose()
    this.scene.traverse(o => {
      if (o.isMesh) {
        o.geometry.dispose()
        ;(Array.isArray(o.material) ? o.material : [o.material]).forEach(m => { m.map?.dispose(); m.dispose() })
      }
    })
    this.scene.environment?.dispose()
    for (const k of Object.keys(MAT)) delete MAT[k]
    this.composer.dispose?.()
    this.renderer.dispose()
    el.remove()
    this.labels.domElement.remove()
  }
}
