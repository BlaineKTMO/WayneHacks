// Live map: the warehouse graph drawn to scale, forklifts as arrows, active paths highlighted.
// Click a node to send the selected forklift there.
import type { Forklift, Graph } from './types'

export const FORKLIFT_COLOURS: Record<string, string> = { 'fl-01': '#f59e0b', 'fl-02': '#38bdf8' }
const colour = (id: string) => FORKLIFT_COLOURS[id] ?? '#a78bfa'
const STALE_MS = 5000

type Props = {
  graph: Graph
  forklifts: Record<string, Forklift>
  pallets: Record<string, string>
  selected?: string
  onNodeClick: (node: string) => void
}

export function WarehouseMap({ graph, forklifts, pallets, selected, onNodeClick }: Props) {
  const { width: W, height: H } = graph.size
  const byId = Object.fromEntries(graph.nodes.map((n) => [n.id, n]))
  // World y is up; SVG y is down. Flip once here.
  const sy = (y: number) => H - y
  const palletAt = Object.entries(pallets).reduce<Record<string, string[]>>((acc, [p, n]) => {
    ;(acc[n] ??= []).push(p)
    return acc
  }, {})
  const now = Date.now()

  return (
    <svg className="map" viewBox={`-0.05 -0.05 ${W + 0.1} ${H + 0.1}`} role="img" aria-label="Warehouse map">
      <rect x={0} y={0} width={W} height={H} className="floor" />

      {graph.edges.map((e) => {
        const a = byId[e.from], b = byId[e.to]
        return <line key={`${e.from}-${e.to}`} x1={a.x} y1={sy(a.y)} x2={b.x} y2={sy(b.y)} className="edge" />
      })}

      {Object.values(forklifts).map((f) =>
        f.path && f.path.nodes.length > 0 ? (
          <polyline
            key={`path-${f.id}`}
            points={[...(f.pose ? [[f.pose.x, f.pose.y]] : []), ...f.path.nodes.map((n) => [n.x, n.y])]
              .map(([x, y]) => `${x},${sy(y)}`).join(' ')}
            className="path"
            stroke={colour(f.id)}
          />
        ) : null,
      )}

      {graph.nodes.map((n) => (
        <g key={n.id} className={`node node-${n.type}`} onClick={() => onNodeClick(n.id)}>
          <title>{selected ? `Send ${selected} to ${n.id}` : n.id}</title>
          {n.type === 'junction'
            ? <circle cx={n.x} cy={sy(n.y)} r={0.018} />
            : <rect x={n.x - 0.03} y={sy(n.y) - 0.03} width={0.06} height={0.06} rx={0.008} />}
          <text x={n.x} y={sy(n.y) + 0.065} textAnchor="middle">{n.id}</text>
          {(palletAt[n.id] ?? []).map((p, i) => (
            <g key={p}>
              <rect x={n.x + 0.035 + i * 0.03} y={sy(n.y) - 0.045} width={0.025} height={0.025} className="pallet" />
              <title>{p}</title>
            </g>
          ))}
        </g>
      ))}

      {Object.values(forklifts).map((f) => {
        if (!f.pose) return null
        const stale = !f.lastSeen || now - f.lastSeen > STALE_MS
        const deg = (-f.pose.theta * 180) / Math.PI
        return (
          <g key={f.id} transform={`translate(${f.pose.x} ${sy(f.pose.y)}) rotate(${deg})`}
             className={`forklift ${stale ? 'stale' : ''} ${f.id === selected ? 'selected' : ''}`}>
            <circle r={0.045} className="halo" />
            <polygon points="0.04,0 -0.025,0.025 -0.025,-0.025" fill={colour(f.id)} />
            <title>{f.id} {f.state ?? ''}</title>
          </g>
        )
      })}
    </svg>
  )
}
