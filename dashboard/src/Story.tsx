// /demo story blocks: the problem with a cost calculator, side-by-side table, tested claims, footnotes.
import { useEffect, useRef, useState } from 'react'
import './story.css'

const MAX = 40
const usd = (v: number) => v >= 1e6 ? `$${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M` : `$${Math.round(v / 1000)}K`

type Inputs = { kit: number; perTruck: number; camera: number; zones: number; edge: number }
const DEFAULTS: Inputs = { kit: 35000, perTruck: 1500, camera: 500, zones: 10, edge: 3000 }
const FIELDS: { k: keyof Inputs; label: string; side: 'a' | 'b'; note: string }[] = [
  { k: 'kit', label: 'Autonomy kit, per truck', side: 'a', note: '1' },
  { k: 'perTruck', label: 'Drive-by-wire + roof tag, per truck', side: 'b', note: '2' },
  { k: 'camera', label: 'Ceiling camera, each', side: 'b', note: '2' },
  { k: 'zones', label: 'Cameras to cover the floor', side: 'b', note: '2' },
  { k: 'edge', label: 'Edge computer, per site', side: 'b', note: '2' },
]

export function Problem() {
  const [n, setN] = useState(12)
  const [v, setV] = useState<Inputs>(DEFAULTS)
  const costA = (k: number) => k * v.kit
  const costB = (k: number) => k * v.perTruck + v.zones * v.camera + v.edge
  const top = Math.max(costA(MAX), costB(MAX)) || 1
  const W = 640, H = 260, P = { l: 56, r: 16, t: 16, b: 34 }
  const x = (k: number) => P.l + ((k - 1) / (MAX - 1)) * (W - P.l - P.r)
  const y = (c: number) => H - P.b - (c / top) * (H - P.t - P.b)
  const line = (f: (k: number) => number) => Array.from({ length: MAX }, (_, i) => `${i ? 'L' : 'M'}${x(i + 1)},${y(f(i + 1))}`).join(' ')
  const a = costA(n), b = costB(n)
  const even = Array.from({ length: MAX }, (_, i) => i + 1).find((k) => costB(k) < costA(k))

  return (
    <>
      <div className="s-problem">
        <div><b>Every truck is its own robot.</b><p>Autonomous forklifts carry their own lidar, cameras and computer. Each one sees only what’s in front of it.</p></div>
        <div><b>Scaling means buying it all again.</b><p>Truck twenty needs the same autonomy kit as truck one. The bill grows with the fleet, not the floor.</p></div>
        <div><b>Telling them what to do is hard.</b><p>Jobs are set up as waypoints and rules in fleet software. Nobody on the floor can just say “move pallet 3 to dock B.”</p></div>
      </div>

      <ScaleAnim />

      <div className="s-chart">
        <div className="s-chart-head">
          <div>
            <h3>Autonomy hardware cost as the fleet grows</h3>
            <p>Drag to add forklifts. Change any assumption below.</p>
          </div>
          <div className="s-readout">
            <span className="s-a"><b>{usd(a)}</b> per-truck kits</span>
            <span className="s-b"><b>{usd(b)}</b> Overhead</span>
          </div>
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} className="s-svg" role="img" aria-label={`With ${n} forklifts: ${usd(a)} with per-truck kits vs ${usd(b)} with Overhead`}>
          {[0, .25, .5, .75, 1].map((f) => (
            <g key={f}><line x1={P.l} x2={W - P.r} y1={y(top * f)} y2={y(top * f)} className="s-grid" />
              <text x={P.l - 8} y={y(top * f) + 4} textAnchor="end" className="s-tick">{usd(top * f)}</text></g>
          ))}
          {[1, 10, 20, 30, 40].map((k) => <text key={k} x={x(k)} y={H - 10} textAnchor="middle" className="s-tick">{k}</text>)}
          <path d={line(costA)} className="s-line a" />
          <path d={line(costB)} className="s-line b" />
          <line x1={x(n)} x2={x(n)} y1={P.t} y2={H - P.b} className="s-cursor" />
          <circle cx={x(n)} cy={y(a)} r={6} className="s-dot a" />
          <circle cx={x(n)} cy={y(b)} r={6} className="s-dot b" />
        </svg>
        <input type="range" min={1} max={MAX} value={n} onChange={(e) => setN(+e.target.value)} className="s-range" aria-label="Forklifts in the fleet" />
        <p className="s-caption"><b>{n} forklift{n > 1 ? 's' : ''}.</b>{' '}
          {a > b ? `Overhead saves ${usd(a - b)} (${Math.round((1 - b / a) * 100)}%). Each extra truck adds ${usd(v.perTruck)}, not ${usd(v.kit)}.`
            : even ? `Per-truck kits are cheaper this small. Overhead pulls ahead at ${even} trucks.` : 'With these numbers, per-truck kits stay cheaper.'}</p>

        <details className="s-assume">
          <summary>Assumptions</summary>
          <div className="s-fields">
            {FIELDS.map((f) => (
              <label key={f.k} className={`s-field s-${f.side}`}>
                <span>{f.label}<sup><a href="#notes">{f.note}</a></sup></span>
                <input type="number" min={0} step={f.k === 'zones' ? 1 : 100} value={v[f.k]}
                       onChange={(e) => setV({ ...v, [f.k]: Math.max(0, +e.target.value || 0) })} />
              </label>
            ))}
          </div>
          <button className="s-reset" onClick={() => setV(DEFAULTS)}>Reset to defaults</button>
        </details>
      </div>
    </>
  )
}

const ROWS: [string, string, string][] = [
  ['Seeing', 'Lidar and cameras on every truck', 'Ceiling cameras. Zero sensors on the truck'],
  ['Adding a truck', 'Another full autonomy kit', 'Drive-by-wire and a printed AprilTag'],
  ['Giving an order', 'Waypoints and rules in fleet software', 'Plain English, checked before anything moves'],
  ['Two trucks, one aisle', 'Each truck reacts to what it sees', 'Lanes reserved centrally, before driving'],
  ['Network drops', 'Depends on the vendor', 'Edge keeps steering and stops in under 0.5 s'],
  ['Getting better', 'Wait for a software update', 'Every job graded; a shadow planner earns promotion'],
]

export function SideBySide() {
  return (
    <div className="s-table-wrap">
      <table className="s-table">
        <thead><tr><th /><th>Per-truck autonomy</th><th>Overhead</th></tr></thead>
        <tbody>{ROWS.map(([k, a, b]) => <tr key={k}><th>{k}</th><td>{a}</td><td><b>{b}</b></td></tr>)}</tbody>
      </table>
    </div>
  )
}

export function Notes() {
  return (
    <ol id="notes" className="s-notes">
      <li>Low end of the $35K–$65K quoted for a Balyo kit plus conversion (<a href="https://www.grabarobot.com/blog/autonomous-forklift-robot-warehouse-2026/" target="_blank" rel="noreferrer">Grab a Robot, 2026 buying guide</a>). Complete autonomous forklifts run about $75K–$150K for standard pallet handling (<a href="https://www.agvnetwork.com/agv-cost-estimation-how-much-does-an-automated-guided-vehicle-cost" target="_blank" rel="noreferrer">AGV Network</a>). We use the low figure on purpose.</li>
      <li>Our estimates, not quotes: an industrial ceiling camera per aisle zone, one edge PC per site, and a drive-by-wire interface plus printed AprilTag per truck. Both sides exclude integration (often ~30% more), installation and running costs, including Overhead’s AWS bill.</li>
      <li>“~2 s” is typical time from order to validated plan with Claude on Amazon Bedrock; “&lt;0.5 s” is the Free-WiLi watchdog timeout.</li>
    </ol>
  )
}

// ---------------------------------------------------------------- scaling animation
const FLEET = 12
const COLS = 5, GRID_ROWS = 2                      // ceiling camera grid (matches DEFAULTS.zones = 10)
const FW = 300, FH = 170
const spot = (i: number) => ({ x: 34 + (i % 4) * 72, y: 30 + Math.floor(i / 4) * 54 })

function Floor({ n, mode }: { n: number; mode: 'a' | 'b' }) {
  return (
    <svg viewBox={`0 0 ${FW} ${FH}`} className={`sa-floor sa-${mode}`} aria-hidden>
      <rect x={1} y={1} width={FW - 2} height={FH - 2} rx={12} className="sa-bg" />
      {[0, 1, 2].map((r) => <line key={r} x1={14} x2={FW - 14} y1={46 + r * 54} y2={46 + r * 54} className="sa-lane" />)}
      {mode === 'b' && Array.from({ length: COLS * GRID_ROWS }, (_, i) => {
        const w = (FW - 16) / COLS, h = (FH - 16) / GRID_ROWS, cx = 8 + (i % COLS) * w, cy = 8 + Math.floor(i / COLS) * h
        return <g key={i}><rect x={cx + 2} y={cy + 2} width={w - 4} height={h - 4} rx={8} className="sa-zone" style={{ animationDelay: `${i * 60}ms` }} />
          <circle cx={cx + w / 2} cy={cy + 9} r={3.2} className="sa-camdot" /></g>
      })}
      {Array.from({ length: n }, (_, i) => {
        const p = spot(i)
        return (
          <g key={i} transform={`translate(${p.x},${p.y})`}>
            <g className="sa-truck" style={{ animationDelay: `${(i * 0.37) % 2}s` }}>
              {mode === 'a' && <circle r={22} className="sa-lidar" />}
              <g className="sa-pop">
                <rect x={-11} y={-7} width={22} height={14} rx={3} className="sa-body" />
                <rect x={11} y={-5} width={7} height={2.4} className="sa-fork" /><rect x={11} y={2.6} width={7} height={2.4} className="sa-fork" />
                {mode === 'a' ? <circle r={3.4} className="sa-sensor" /> : <rect x={-3.5} y={-3.5} width={7} height={7} className="sa-tag" />}
              </g>
              <text y={-14} textAnchor="middle" className="sa-cost">+{mode === 'a' ? '$35K' : '$1.5K'}</text>
            </g>
          </g>
        )
      })}
    </svg>
  )
}

function ScaleAnim() {
  const [n, setN] = useState(0)
  const [run, setRun] = useState(0)
  const ref = useRef<HTMLDivElement>(null)
  const [seen, setSeen] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver(([e]) => e.isIntersecting && setSeen(true), { threshold: 0.4 })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  useEffect(() => {
    if (!seen) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { setN(FLEET); return }
    setN(0)
    let i = 0
    const t = setInterval(() => { i += 1; setN(i); if (i >= FLEET) clearInterval(t) }, 650)
    return () => clearInterval(t)
  }, [seen, run])
  const fixed = DEFAULTS.zones * DEFAULTS.camera + DEFAULTS.edge
  const a = n * DEFAULTS.kit, b = n ? fixed + n * DEFAULTS.perTruck : fixed

  return (
    <div className="sa" ref={ref}>
      <div className="sa-head">
        <h3>Watch a fleet grow</h3>
        <div className="sa-ctl">
          <span className="sa-n">{n} forklift{n === 1 ? '' : 's'}</span>
          <button onClick={() => setN((k) => Math.min(FLEET, k + 1))} disabled={n >= FLEET}>+ Add one</button>
          <button onClick={() => setRun((r) => r + 1)}>↺ Replay</button>
        </div>
      </div>
      <div className="sa-panels">
        <div className="sa-panel">
          <div className="sa-label"><span className="sa-pill a">Per-truck autonomy</span><b key={a} className="sa-total a">{usd(a)}</b></div>
          <Floor n={n} mode="a" />
          <p>Every truck brings its own lidar, cameras and computer.</p>
        </div>
        <div className="sa-panel">
          <div className="sa-label"><span className="sa-pill b">Overhead</span><b key={b} className="sa-total b">{usd(b)}</b></div>
          <Floor n={n} mode="b" />
          <p>Cameras already cover the floor. A new truck just needs a tag.</p>
        </div>
      </div>
    </div>
  )
}
