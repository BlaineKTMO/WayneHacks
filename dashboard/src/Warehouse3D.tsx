// Live 3D warehouse (three.js). World (x, y) metres -> three (x, -z); heading theta -> rotation.y.
import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { Forklift, Graph } from './types'
import { unitColour } from './WarehouseMap'

type Props = {
  graph: Graph
  forklifts: Record<string, Forklift>
  pallets: Record<string, string>
  locks?: Record<string, string>
  people?: { x: number; y: number }[]
  selected?: string
  onNodeClick: (node: string) => void
  onUnitClick?: (id: string) => void
}

const V = (x: number, y: number, h = 0) => new THREE.Vector3(x, h, -y)

function stripeTexture(a: string, b: string, n = 8) {
  const c = document.createElement('canvas')
  c.width = c.height = 64
  const g = c.getContext('2d')!
  g.fillStyle = b
  g.fillRect(0, 0, 64, 64)
  g.fillStyle = a
  for (let i = -n; i < n * 2; i++) {
    g.beginPath()
    g.moveTo(i * 16, 0); g.lineTo(i * 16 + 8, 0); g.lineTo(i * 16 + 8 - 64, 64); g.lineTo(i * 16 - 64, 64)
    g.fill()
  }
  const t = new THREE.CanvasTexture(c)
  t.wrapS = t.wrapT = THREE.RepeatWrapping
  t.colorSpace = THREE.SRGBColorSpace
  return t
}

function labelSprite(text: string, colour: string) {
  const c = document.createElement('canvas')
  c.width = 256; c.height = 96
  const g = c.getContext('2d')!
  g.fillStyle = 'rgba(17,20,26,.88)'
  g.beginPath(); g.roundRect(8, 16, 240, 64, 18); g.fill()
  g.strokeStyle = colour; g.lineWidth = 5; g.stroke()
  g.fillStyle = colour; g.font = '600 40px "JetBrains Mono", monospace'; g.textAlign = 'center'; g.textBaseline = 'middle'
  g.fillText(text, 128, 49)
  const t = new THREE.CanvasTexture(c)
  t.colorSpace = THREE.SRGBColorSpace
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, depthTest: false }))
  s.scale.set(0.16, 0.06, 1)
  s.renderOrder = 10
  return s
}

function makeForklift(colour: string, id: string) {
  const g = new THREE.Group()
  const body = new THREE.MeshStandardMaterial({ color: colour, roughness: 0.45, metalness: 0.1 })
  const dark = new THREE.MeshStandardMaterial({ color: 0x23262d, roughness: 0.7 })
  const steel = new THREE.MeshStandardMaterial({ color: 0xb8bec9, roughness: 0.35, metalness: 0.8 })
  const add = (geo: THREE.BufferGeometry, mat: THREE.Material, x: number, y: number, z: number) => {
    const m = new THREE.Mesh(geo, mat); m.position.set(x, y, z); m.castShadow = true; g.add(m); return m
  }
  add(new THREE.BoxGeometry(0.075, 0.03, 0.055), body, -0.005, 0.022, 0)          // chassis
  add(new THREE.BoxGeometry(0.025, 0.02, 0.05), dark, -0.035, 0.045, 0)           // counterweight
  for (const z of [-0.022, 0.022]) for (const x of [-0.028, 0.022])
    add(new THREE.CylinderGeometry(0.011, 0.011, 0.01, 16).rotateX(Math.PI / 2), dark, x, 0.011, z) // wheels
  for (const z of [-0.022, 0.022]) add(new THREE.BoxGeometry(0.004, 0.07, 0.004), dark, -0.02, 0.07, z) // guard posts
  add(new THREE.BoxGeometry(0.05, 0.004, 0.05), dark, -0.005, 0.105, 0)            // overhead guard
  for (const z of [-0.018, 0.018]) add(new THREE.BoxGeometry(0.005, 0.09, 0.005), steel, 0.033, 0.06, z) // mast
  const forks = new THREE.Group(); forks.name = 'forks'; g.add(forks)
  for (const z of [-0.014, 0.014]) {
    const f = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.003, 0.007), steel); f.position.set(0.06, 0.008, z); f.castShadow = true; forks.add(f)
  }
  const load = new THREE.Group(); load.name = 'load'; load.visible = false; load.position.set(0.06, 0.012, 0); g.add(load)
  load.add(makePallet())
  const tag = labelSprite(id.toUpperCase(), colour); tag.position.set(0, 0.17, 0); g.add(tag)
  const ring = new THREE.Mesh(new THREE.RingGeometry(0.065, 0.075, 48).rotateX(-Math.PI / 2),
    new THREE.MeshBasicMaterial({ color: colour, transparent: true, opacity: 0.9 }))
  ring.name = 'ring'; ring.position.y = 0.003; ring.visible = false; g.add(ring)
  return g
}

function makePallet() {
  const g = new THREE.Group()
  const wood = new THREE.MeshStandardMaterial({ color: 0xb57a3c, roughness: 0.9 })
  const box = new THREE.MeshStandardMaterial({ color: 0xd9a35c, roughness: 0.8 })
  const base = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.008, 0.045), wood); base.position.y = 0.004; base.castShadow = true; g.add(base)
  const crate = new THREE.Mesh(new THREE.BoxGeometry(0.044, 0.034, 0.04), box); crate.position.y = 0.025; crate.castShadow = true; g.add(crate)
  return g
}

function makeRack() {
  const g = new THREE.Group()
  const steel = new THREE.MeshStandardMaterial({ color: 0x2f6fd6, roughness: 0.5, metalness: 0.5 })
  const beam = new THREE.MeshStandardMaterial({ color: 0xf08a24, roughness: 0.5, metalness: 0.4 })
  for (const x of [-0.05, 0.05]) for (const z of [-0.04, 0.04]) {
    const p = new THREE.Mesh(new THREE.BoxGeometry(0.006, 0.16, 0.006), steel); p.position.set(x, 0.08, z); p.castShadow = true; g.add(p)
  }
  for (const y of [0.095, 0.155]) for (const z of [-0.04, 0.04]) {
    const b = new THREE.Mesh(new THREE.BoxGeometry(0.106, 0.008, 0.006), beam); b.position.set(0, y, z); g.add(b)
  }
  const top = makePallet(); top.position.y = 0.1; g.add(top)
  return g
}

export default function Warehouse3D(props: Props) {
  const host = useRef<HTMLDivElement>(null)
  const latest = useRef(props)
  latest.current = props

  useEffect(() => {
    const el = host.current!
    const { graph } = latest.current
    const W = graph.size.width, H = graph.size.height
    const S = Math.max(W, H) / 1.5   // 1 = the 1.5 m demo map; ~0.34 for the real tabletop

    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.shadowMap.enabled = true
    renderer.shadowMap.type = THREE.PCFSoftShadowMap
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    el.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0xe9ebef)
    scene.fog = new THREE.Fog(0xe9ebef, 3 * S + 0.5, 7 * S + 1)
    const camera = new THREE.PerspectiveCamera(38, 1, 0.01, 50)
    camera.position.set(W / 2 - 0.2 * S, 1.25 * S + 0.15, -H / 2 + 1.45 * S + 0.1)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.target.set(W / 2, 0, -H / 2)
    controls.enableDamping = true
    controls.maxPolarAngle = Math.PI * 0.46
    controls.minDistance = 0.4 * S
    controls.maxDistance = 3.2 * S + 0.5
    controls.autoRotate = true
    controls.autoRotateSpeed = 0.35
    controls.addEventListener('start', () => { controls.autoRotate = false })

    scene.add(new THREE.HemisphereLight(0xffffff, 0xb8bcc6, 1.1))
    const sun = new THREE.DirectionalLight(0xffffff, 1.6)
    sun.position.set(W / 2 + 0.6 * S, 2.2 * S + 0.4, -H / 2 + 0.9 * S)
    sun.target.position.set(W / 2, 0, -H / 2)
    sun.castShadow = true
    sun.shadow.mapSize.set(2048, 2048)
    Object.assign(sun.shadow.camera, { left: -1.2 * S - 0.2, right: 1.2 * S + 0.2, top: 1.2 * S + 0.2, bottom: -1.2 * S - 0.2, near: 0.1, far: 5 })
    sun.shadow.bias = -0.0005
    scene.add(sun, sun.target)

    // floor
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(W + 0.3, H + 0.3).rotateX(-Math.PI / 2),
      new THREE.MeshStandardMaterial({ color: 0xcfd3da, roughness: 0.95 }))
    floor.position.set(W / 2, 0, -H / 2); floor.receiveShadow = true; scene.add(floor)
    const grid = new THREE.GridHelper(Math.max(W, H) + 0.3, 36, 0xb9bec7, 0xc4c8cf)
    grid.position.set(W / 2, 0.0005, -H / 2); scene.add(grid)

    const byId = Object.fromEntries(graph.nodes.map((n) => [n.id, n]))
    // lanes
    const asphalt = new THREE.MeshStandardMaterial({ color: 0x3a3f48, roughness: 0.9 })
    const paint = new THREE.MeshBasicMaterial({ color: 0xf2b705 })
    for (const e of graph.edges) {
      const a = byId[e.from], b = byId[e.to]
      const len = Math.hypot(b.x - a.x, b.y - a.y)
      const ang = Math.atan2(b.y - a.y, b.x - a.x)
      const lane = new THREE.Mesh(new THREE.BoxGeometry(len + 0.07, 0.002, 0.07), asphalt)
      lane.position.copy(V((a.x + b.x) / 2, (a.y + b.y) / 2, 0.001)); lane.rotation.y = ang; lane.receiveShadow = true; scene.add(lane)
      const dashes = Math.floor(len / 0.035)
      for (let i = 0; i < dashes; i += 2) {
        const t = (i + 0.5) / dashes
        const d = new THREE.Mesh(new THREE.BoxGeometry(0.016, 0.001, 0.003), paint)
        d.position.copy(V(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, 0.0026)); d.rotation.y = ang; scene.add(d)
      }
    }
    // nodes: racks, docks, junction pads + invisible tap targets
    const hazard = stripeTexture('#f5b400', '#1b1d22')
    const pickables: THREE.Object3D[] = []
    const palletAnchors: Record<string, THREE.Vector3> = {}
    for (const n of graph.nodes) {
      const p = V(n.x, n.y)
      if (n.type === 'slot') {
        const r = makeRack(); r.position.copy(p); scene.add(r)
        palletAnchors[n.id] = p.clone().setY(0.003)
      } else if (n.type === 'dock') {
        const pad = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.003, 0.12), new THREE.MeshStandardMaterial({ map: hazard, roughness: 0.8 }))
        pad.position.copy(p).setY(0.0015); pad.receiveShadow = true; scene.add(pad)
        const door = new THREE.Mesh(new THREE.BoxGeometry(0.01, 0.16, 0.14), new THREE.MeshStandardMaterial({ color: 0x8b93a3, roughness: 0.6, metalness: 0.4 }))
        door.position.copy(V(n.x - 0.075, n.y, 0.08)); door.castShadow = true; scene.add(door)
        palletAnchors[n.id] = p.clone().setY(0.004)
      } else {
        const pad = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.018, 0.003, 24), new THREE.MeshStandardMaterial({ color: 0x5b6270 }))
        pad.position.copy(p).setY(0.0025); scene.add(pad)
      }
      const hit = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.2, 16), new THREE.MeshBasicMaterial({ visible: false }))
      hit.position.copy(p).setY(0.1); hit.userData.node = n.id; scene.add(hit); pickables.push(hit)
    }
    // ceiling camera with its view cone: the whole pitch in one prop
    const camRig = new THREE.Group()
    const camBody = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.04, 0.05), new THREE.MeshStandardMaterial({ color: 0x1d1d1f, roughness: 0.4 }))
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.004, 0.004, 0.25), new THREE.MeshStandardMaterial({ color: 0x8b93a3 }))
    pole.position.y = 0.14; camRig.add(camBody, pole)
    const camH = 0.95 * S + 0.12
    camRig.position.set(W / 2, camH, -H / 2); scene.add(camRig)
    const cone = new THREE.Mesh(new THREE.ConeGeometry(Math.max(W, H) * 0.65, camH - 0.02, 4, 1, true),
      new THREE.MeshBasicMaterial({ color: 0x0071e3, transparent: true, opacity: 0.05, side: THREE.DoubleSide, depthWrite: false }))
    cone.rotation.y = Math.PI / 4; cone.position.set(W / 2, camH / 2, -H / 2); scene.add(cone)
    const coneEdges = new THREE.LineSegments(new THREE.EdgesGeometry(cone.geometry), new THREE.LineBasicMaterial({ color: 0x0071e3, transparent: true, opacity: 0.25 }))
    coneEdges.rotation.copy(cone.rotation); coneEdges.position.copy(cone.position); scene.add(coneEdges)

    // dynamic layers
    const units: Record<string, THREE.Group> = {}
    const shelfPallets: Record<string, THREE.Group> = {}
    const lockTiles = new THREE.Group(); scene.add(lockTiles)
    const routes = new THREE.Group(); scene.add(routes)
    const peopleLayer = new THREE.Group(); scene.add(peopleLayer)
    const personMat = new THREE.MeshStandardMaterial({ color: 0xff4d5e, roughness: 0.5 })
    const zoneMat = new THREE.MeshBasicMaterial({ color: 0xff4d5e, transparent: true, opacity: 0.18, depthWrite: false })

    const resize = () => {
      const w = el.clientWidth, h = el.clientHeight
      renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix()
    }
    const ro = new ResizeObserver(resize); ro.observe(el); resize()

    // tap (not drag) to pick a node or a forklift
    const ray = new THREE.Raycaster(), ndc = new THREE.Vector2()
    let down: { x: number; y: number } | null = null
    const onDown = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY } }
    const onUp = (e: PointerEvent) => {
      if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 8) { down = null; return }
      down = null
      const r = renderer.domElement.getBoundingClientRect()
      ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1)
      ray.setFromCamera(ndc, camera)
      const unitHits = ray.intersectObjects(Object.values(units), true)
      if (unitHits.length) {
        let o: THREE.Object3D | null = unitHits[0].object
        while (o && !o.userData.unit) o = o.parent
        if (o) { latest.current.onUnitClick?.(o.userData.unit); return }
      }
      const hit = ray.intersectObjects(pickables)[0]
      if (hit) latest.current.onNodeClick(hit.object.userData.node)
    }
    renderer.domElement.addEventListener('pointerdown', onDown)
    renderer.domElement.addEventListener('pointerup', onUp)

    let raf = 0, lastSync = 0
    const sync = () => {
      const { forklifts, pallets, locks = {}, selected } = latest.current
      // forklifts
      for (const f of Object.values(forklifts)) {
        if (!f.pose) continue
        let u = units[f.id]
        if (!u) {
          u = makeForklift(unitColour(f.id), f.id); u.userData.unit = f.id
          u.position.copy(V(f.pose.x, f.pose.y)); u.rotation.y = f.pose.theta
          scene.add(u); units[f.id] = u
        }
        u.userData.target = V(f.pose.x, f.pose.y)
        u.userData.theta = f.pose.theta
        u.getObjectByName('ring')!.visible = f.id === selected
        u.getObjectByName('load')!.visible = Object.values(pallets).includes(`on:${f.id}`)
        const stale = !f.lastSeen || Date.now() - f.lastSeen > 5000
        u.traverse((o) => {
          const m = (o as THREE.Mesh).material as THREE.Material | undefined
          if (m && o.type !== 'Sprite' && o.name !== 'ring') { m.transparent = stale; m.opacity = stale ? 0.4 : 1 }
        })
      }
      // floor pallets
      const want = Object.entries(pallets).filter(([, n]) => palletAnchors[n])
      for (const [p, n] of want) {
        let m = shelfPallets[p]
        if (!m) { m = makePallet(); scene.add(m); shelfPallets[p] = m }
        m.visible = true
        m.position.copy(palletAnchors[n])
      }
      for (const [p, m] of Object.entries(shelfPallets)) if (!palletAnchors[pallets[p]]) m.visible = false
      // reservations
      lockTiles.clear()
      for (const [n, h] of Object.entries(locks)) {
        const node = byId[n]; if (!node) continue
        const t = new THREE.Mesh(new THREE.BoxGeometry(0.085, 0.002, 0.085),
          new THREE.MeshBasicMaterial({ color: unitColour(h), transparent: true, opacity: 0.35, depthWrite: false }))
        t.position.copy(V(node.x, node.y, 0.004)); lockTiles.add(t)
      }
      // people
      peopleLayer.clear()
      for (const p of latest.current.people ?? []) {
        const g = new THREE.Group()
        const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.018, 0.07, 6, 12), personMat); body.position.y = 0.055; body.castShadow = true
        const head = new THREE.Mesh(new THREE.SphereGeometry(0.016, 16, 12), personMat); head.position.y = 0.115; head.castShadow = true
        const zone = new THREE.Mesh(new THREE.CircleGeometry(0.12, 40).rotateX(-Math.PI / 2), zoneMat); zone.position.y = 0.005
        g.add(body, head, zone); g.position.copy(V(p.x, p.y)); peopleLayer.add(g)
      }
      // routes
      routes.clear()
      for (const f of Object.values(forklifts)) {
        if (!f.path?.nodes.length || !f.pose) continue
        const pts = [V(f.pose.x, f.pose.y, 0.006), ...f.path.nodes.map((n) => V(n.x, n.y, 0.006))]
        if (pts.length < 2) continue
        const tube = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts, false, 'catmullrom', 0.05), 64, 0.006, 8),
          new THREE.MeshBasicMaterial({ color: unitColour(f.id), transparent: true, opacity: 0.85 }))
        routes.add(tube)
      }
    }

    const tick = (t: number) => {
      raf = requestAnimationFrame(tick)
      if (t - lastSync > 120) { sync(); lastSync = t }
      for (const u of Object.values(units)) {
        const target: THREE.Vector3 | undefined = u.userData.target
        if (target) u.position.lerp(target, 0.18)
        if (u.userData.theta !== undefined) {
          let d = u.userData.theta - u.rotation.y
          d = Math.atan2(Math.sin(d), Math.cos(d))
          u.rotation.y += d * 0.18
        }
        const ring = u.getObjectByName('ring')!
        ring.rotation.y = t / 900
      }
      controls.update()
      renderer.render(scene, camera)
    }
    raf = requestAnimationFrame(tick)

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      renderer.domElement.removeEventListener('pointerdown', onDown)
      renderer.domElement.removeEventListener('pointerup', onUp)
      controls.dispose()
      renderer.dispose()
      el.removeChild(renderer.domElement)
    }
  }, [props.graph])

  return <div ref={host} className="w3d" aria-label="3D warehouse view" />
}
