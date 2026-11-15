// Live floor plan, drawn in world metres. Lanes, racking, docks, forklifts with trails and routes.
import type { Forklift, Graph } from './types'

export const UNIT_COLOURS: Record<string, string> = { 'fl-01': '#ffb020', 'fl-02': '#3ec5ff', 'fl-03': '#2ee59d', 'fl-04': '#ff6bd6' }
export const unitColour = (id: string) => UNIT_COLOURS[id] ?? '#b28dff'
const STALE_MS = 5000

type Props = {
  graph: Graph
  forklifts: Record<string, Forklift>
  trails: Record<string, [number, number][]>
  pallets: Record<string, string>
  locks?: Record<string, string>
  people?: { x: number; y: number }[]
  selected?: string
  onNodeClick: (node: string) => void
  onUnitClick: (id: string) => void
}

export function WarehouseMap({ graph, forklifts, trails, pallets, locks = {}, people = [], selected, onNodeClick, onUnitClick }: Props) {
  const { width: W, height: H } = graph.size
  const byId = Object.fromEntries(graph.nodes.map((n) => [n.id, n]))
  const sy = (y: number) => H - y // world y up -> svg y down
  const palletAt = Object.entries(pallets).reduce<Record<string, string[]>>((acc, [p, n]) => {
    ;(acc[n] ??= []).push(p)
    return acc
  }, {})
  const now = Date.now()
  const pad = 0.06

  return (
    <svg className="map" viewBox={`${-pad} ${-pad} ${W + 2 * pad} ${H + 2 * pad}`} preserveAspectRatio="xMidYMid meet"
         role="img" aria-label="Warehouse floor plan">
      <defs>
        <pattern id="grid-minor" width="0.05" height="0.05" patternUnits="userSpaceOnUse">
          <path d="M0.05 0H0V0.05" fill="none" className="grid-minor" />
        </pattern>
        <pattern id="grid-major" width="0.25" height="0.25" patternUnits="userSpaceOnUse">
          <rect width="0.25" height="0.25" fill="url(#grid-minor)" />
          <path d="M0.25 0H0V0.25" fill="none" className="grid-major" />
        </pattern>
        <pattern id="hazard" width="0.03" height="0.03" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="0.015" height="0.03" fill="#f5b400" />
          <rect x="0.015" width="0.015" height="0.03" fill="#14161b" />
        </pattern>
        <pattern id="rack" width="0.016" height="0.016" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="0.016" height="0.016" className="rack-bg" />
          <rect width="0.004" height="0.016" className="rack-line" />
        </pattern>
        <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="0.008" result="b" />
          <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>

      {/* Floor */}
      <rect x={0} y={0} width={W} height={H} className="floor" />
      <rect x={0} y={0} width={W} height={H} fill="url(#grid-major)" />
      <rect x={0} y={0} width={W} height={H} className="floor-border" />

      {/* Lanes */}
      {graph.edges.map((e) => {
        const a = byId[e.from], b = byId[e.to]
        return (
          <g key={`${e.from}-${e.to}`}>
            <line x1={a.x} y1={sy(a.y)} x2={b.x} y2={sy(b.y)} className="lane" />
            <line x1={a.x} y1={sy(a.y)} x2={b.x} y2={sy(b.y)} className="lane-centre" />
          </g>
        )
      })}

      {/* Reservations: lanes where both ends are held by the same forklift */}
      {graph.edges.map((e) => {
        const h = locks[e.from]
        if (!h || locks[e.to] !== h) return null
        const a = byId[e.from], b = byId[e.to]
        return <line key={`res-${e.from}-${e.to}`} x1={a.x} y1={sy(a.y)} x2={b.x} y2={sy(b.y)}
                     className="reserved-lane" stroke={unitColour(h)} />
      })}
      {Object.entries(locks).map(([n, h]) => byId[n] && (
        <rect key={`resn-${n}`} x={byId[n].x - 0.04} y={sy(byId[n].y) - 0.04} width={0.08} height={0.08} rx={0.012}
              className="reserved-node" stroke={unitColour(h)}>
          <title>{`${n} reserved by ${h}`}</title>
        </rect>
      ))}

      {/* Trails */}
      {Object.entries(trails).map(([id, pts]) =>
        pts.length > 1 ? (
          <g key={`trail-${id}`}>
            {pts.slice(1).map((p, i) => (
              <line key={i} x1={pts[i][0]} y1={sy(pts[i][1])} x2={p[0]} y2={sy(p[1])}
                    stroke={unitColour(id)} strokeOpacity={(i / pts.length) * 0.5} className="trail" />
            ))}
          </g>
        ) : null,
      )}

      {/* Active routes */}
      {Object.values(forklifts).map((f) => {
        if (!f.path?.nodes.length) return null
        const pts = [...(f.pose ? [[f.pose.x, f.pose.y]] : []), ...f.path.nodes.map((n) => [n.x, n.y])]
        const dest = f.path.nodes[f.path.nodes.length - 1]
        return (
          <g key={`route-${f.id}`} style={{ color: unitColour(f.id) }}>
            <polyline points={pts.map(([x, y]) => `${x},${sy(y)}`).join(' ')} className="route-glow" />
            <polyline points={pts.map(([x, y]) => `${x},${sy(y)}`).join(' ')} className="route" />
            <circle cx={dest.x} cy={sy(dest.y)} r={0.03} className="dest-pulse" />
            <circle cx={dest.x} cy={sy(dest.y)} r={0.012} className="dest-dot" />
          </g>
        )
      })}

      {/* Nodes: docks, racking slots, junctions */}
      {graph.nodes.map((n) => {
        const x = n.x, y = sy(n.y)
        return (
          <g key={n.id} className={`node node-${n.type} ${selected ? 'armed' : ''}`} onClick={() => onNodeClick(n.id)}>
            <title>{selected ? `Dispatch ${selected} → ${n.id}` : n.id}</title>
            <circle cx={x} cy={y} r={0.05} className="hit" />
            {n.type === 'dock' && <>
              <rect x={x - 0.08} y={y - 0.06} width={0.04} height={0.12} fill="url(#hazard)" opacity={0.85} />
              <rect x={x - 0.04} y={y - 0.06} width={0.08} height={0.12} rx={0.006} className="dock-bay" />
            </>}
            {n.type === 'slot' && <rect x={x - 0.045} y={y - 0.035} width={0.09} height={0.07} rx={0.004} className="slot" fill="url(#rack)" />}
            {n.type === 'junction' && <rect x={x - 0.012} y={y - 0.012} width={0.024} height={0.024} transform={`rotate(45 ${x} ${y})`} className="junction" />}
            <text x={n.type === 'junction' ? x + 0.045 : x} y={n.type === 'dock' ? y + 0.09 : n.type === 'junction' ? y - 0.025 : y + 0.062}
                  className={`node-label ${n.type === 'junction' ? 'left' : ''}`}>{n.id.replace('_', ' ').toUpperCase()}</text>
            {(palletAt[n.id] ?? []).map((p, i) => (
              <g key={p}>
                <rect x={x - 0.018 + i * 0.012} y={y - 0.018} width={0.036} height={0.036} rx={0.003} className="pallet" />
                <text x={x + i * 0.012} y={y + 0.007} className="pallet-label">{p.replace('pallet_', 'P')}</text>
                <title>{p}</title>
              </g>
            ))}
          </g>
        )
      })}

      {/* Forklifts */}
      {Object.values(forklifts).map((f) => {
        if (!f.pose) return null
        const stale = !f.lastSeen || now - f.lastSeen > STALE_MS
        const deg = (-f.pose.theta * 180) / Math.PI
        const c = unitColour(f.id)
        const alert = f.state === 'STOPPED' || f.state === 'FAULT'
        return (
          <g key={f.id} className={`unit ${stale ? 'stale' : ''} ${f.id === selected ? 'selected' : ''}`}
             onClick={() => onUnitClick(f.id)} style={{ color: c }}>
            <g transform={`translate(${f.pose.x} ${sy(f.pose.y)})`}>
              {f.id === selected && <circle r={0.06} className="select-ring" />}
              {alert && <circle r={0.055} className="alert-ring" />}
              <g transform={`rotate(${deg})`} filter="url(#glow)">
                <path d="M0.02 -0.03 L0.07 -0.03 M0.02 0.03 L0.07 0.03" className="forks" />
                {Object.values(pallets).includes(`on:${f.id}`) &&
                  <rect x={0.03} y={-0.026} width={0.045} height={0.052} rx={0.004} className="pallet" />}
                <rect x={-0.04} y={-0.032} width={0.065} height={0.064} rx={0.012} fill={c} className="body" />
                <rect x={-0.035} y={-0.022} width={0.025} height={0.044} rx={0.005} className="mast" />
                <path d="M0.012 -0.012 L0.024 0 L0.012 0.012" className="chevron" />
              </g>
              <g transform="translate(0 -0.085)">
                <rect x={-0.042} y={-0.018} width={0.084} height={0.03} rx={0.006} className="tag" stroke={c} />
                <text y={0.003} className="tag-text">{f.id.toUpperCase()}</text>
              </g>
            </g>
          </g>
        )
      })}

      {/* People / obstacles from the camera detector */}
      {people.map((p, i) => (
        <g key={`person-${i}`} transform={`translate(${p.x} ${sy(p.y)})`} className="person">
          <circle r={0.12} className="person-zone" />
          <circle r={0.022} className="person-dot" />
          <circle r={0.01} cy={-0.032} className="person-dot" />
          <title>Person detected</title>
        </g>
      ))}

      {/* Scale bar + compass */}
      <g transform={`translate(${W - 0.3} ${H + 0.035})`} className="scale">
        <line x1={0} y1={0} x2={0.25} y2={0} />
        <line x1={0} y1={-0.008} x2={0} y2={0.008} />
        <line x1={0.25} y1={-0.008} x2={0.25} y2={0.008} />
        <text x={0.125} y={-0.012}>25 cm</text>
      </g>
      <text x={0.005} y={H + 0.042} className="floor-caption">FLOOR 1 · {W.toFixed(1)} × {H.toFixed(1)} m · WORLD FRAME TAG 0</text>
    </svg>
  )
}
