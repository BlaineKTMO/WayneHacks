import { Authenticator, ThemeProvider, type Theme } from '@aws-amplify/ui-react'
import '@aws-amplify/ui-react/styles.css'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import type { Config } from './config'
import type { Forklift, LogEntry, Task } from './types'
import { PlannerLab } from './PlannerLab'
import { PlanView } from './PlanView'
import { SimSwitch } from './SimSwitch'
import { useFleet } from './useFleet'
import { unitColour, WarehouseMap } from './WarehouseMap'

const PRESETS = ['Move pallet 3 to dock B', 'Bring pallet 1 to dock A', 'Swap pallets 1 and 2', 'Clear slot A2']

// 2S LiPo: 6.6 V empty, 8.4 V full.
const batteryPct = (v?: number) => (v == null ? undefined : Math.max(0, Math.min(100, ((v - 6.6) / 1.8) * 100)))
const clock = (ts: number) => new Date(ts).toLocaleTimeString([], { hour12: false })
const ago = (ts?: number) => {
  if (!ts) return ''
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000))
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : `${Math.floor(s / 3600)}h ago`
}

const authTheme: Theme = {
  name: 'overhead',
  tokens: {
    colors: {
      background: { primary: { value: '#11141a' }, secondary: { value: '#0b0d11' } },
      font: { primary: { value: '#e8ebf2' }, secondary: { value: '#8a93a6' }, interactive: { value: '#ffb020' } },
      border: { primary: { value: '#262b36' } },
      brand: { primary: { 10: '#2a1f05', 80: '#ffb020', 90: '#ffc451', 100: '#ffd27a' } },
    },
    components: { button: { primary: { color: { value: '#111' } } } },
  },
}

export default function App({ cfg }: { cfg: Config }) {
  return (
    <ThemeProvider theme={authTheme} colorMode="dark">
      <Authenticator hideSignUp components={{ Header: () => <div className="auth-brand"><Logo /><span>OVERHEAD</span><small>Fleet Control</small></div> }}>
        {({ signOut, user }) => <Console cfg={cfg} email={user?.signInDetails?.loginId} signOut={signOut} />}
      </Authenticator>
    </ThemeProvider>
  )
}

function Logo() {
  return (
    <svg viewBox="0 0 32 32" className="logo" aria-hidden>
      <rect x="2" y="2" width="28" height="28" rx="7" fill="#ffb020" />
      <circle cx="16" cy="16" r="7.5" fill="none" stroke="#111" strokeWidth="2.4" />
      <circle cx="16" cy="16" r="2.4" fill="#111" />
      <path d="M16 3.5v5M16 23.5v5M3.5 16h5M23.5 16h5" stroke="#111" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  )
}

export type Fleet = ReturnType<typeof useFleet>

function Console({ cfg, email, signOut }: { cfg: Config; email?: string; signOut?: () => void }) {
  return <ConsoleView fleet={useFleet(cfg)} email={email} signOut={signOut} cfg={cfg} />
}

export function ConsoleView({ fleet, email, signOut, cfg }: { fleet: Fleet; email?: string; signOut?: () => void; cfg?: Config }) {
  const ids = Object.keys(fleet.forklifts).sort()
  const [selected, setSelected] = useState<string>()
  const [text, setText] = useState('')
  const [now, setNow] = useState(Date.now())

  const firstId = ids[0]
  useEffect(() => {
    if (!selected && firstId) setSelected(firstId)
  }, [firstId, selected])
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  const online = (f: Forklift) => !!f.lastSeen && now - f.lastSeen < 5000
  const kpi = useMemo(() => {
    const units = Object.values(fleet.forklifts)
    const live = units.filter(online)
    const batts = live.map((f) => batteryPct(f.battery_v)).filter((b): b is number => b != null)
    return {
      online: live.length,
      total: units.length,
      moving: live.filter((f) => f.state === 'DRIVING').length,
      running: fleet.tasks.filter((t) => t.status === 'running').length,
      done: fleet.tasks.filter((t) => t.status === 'done').length,
      battery: batts.length ? Math.round(batts.reduce((a, b) => a + b, 0) / batts.length) : undefined,
    }
  }, [fleet.forklifts, fleet.tasks, now])

  const submit = (e?: FormEvent, preset?: string) => {
    e?.preventDefault()
    const t = (preset ?? text).trim()
    if (!t) return
    fleet.submitTask(t)
    setText('')
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand"><Logo /><span className="brand-name">OVERHEAD</span><span className="brand-sub">Fleet Control</span></div>
        <div className="site"><span className="site-label">SITE</span>Demo Warehouse · Floor 1</div>
        <div className="spacer" />
        {cfg && <SimSwitch cfg={cfg} />}
        <a className="btn-ghost" href="/demo">Demo page</a>
        <div className={`link link-${fleet.conn}`}><span className="pulse" />{fleet.conn === 'live' ? 'LIVE' : fleet.conn === 'connecting' ? 'CONNECTING' : 'OFFLINE'}</div>
        <div className="clock mono">{clock(now)}</div>
        <div className="user" title={email}>{(email ?? '?')[0].toUpperCase()}</div>
        <button className="btn-ghost" onClick={signOut}>Sign out</button>
      </header>

      {fleet.error && <div className="banner">{fleet.error}</div>}

      <section className="kpis">
        <Kpi label="Units online" value={`${kpi.online}`} sub={`of ${kpi.total} registered`} tone={kpi.online ? 'ok' : 'muted'} />
        <Kpi label="In motion" value={`${kpi.moving}`} sub="driving now" />
        <Kpi label="Active tasks" value={`${kpi.running}`} sub="in progress" tone={kpi.running ? 'accent' : undefined} />
        <Kpi label="Completed" value={`${kpi.done}`} sub="tasks this session" tone="ok" />
        <Kpi label="Avg battery" value={kpi.battery != null ? `${kpi.battery}%` : '—'} sub="online units" />
        <Kpi label="Safety stops" value={`${fleet.stops}`} sub="this session" tone={fleet.stops ? 'danger' : undefined} />
      </section>

      <div className="workspace">
        <section className="panel map-panel">
          <div className="panel-head">
            <h2>Live floor</h2>
            <div className="legend">
              <span><i className="lg-dock" />Dock</span><span><i className="lg-slot" />Racking</span>
              <span><i className="lg-pallet" />Pallet</span><span><i className="lg-route" />Route</span><span><i className="lg-res" />Reserved</span>
            </div>
            <div className="spacer" />
            {selected
              ? <div className="armed-chip" style={{ borderColor: unitColour(selected) }}>
                  <span className="dot" style={{ background: unitColour(selected) }} />
                  Click any location to dispatch <b>{selected.toUpperCase()}</b>
                </div>
              : <div className="muted small">Select a unit</div>}
          </div>
          <div className="map-wrap">
            {fleet.graph
              ? <WarehouseMap graph={fleet.graph} forklifts={fleet.forklifts} trails={fleet.trails} pallets={fleet.pallets} locks={fleet.locks} people={fleet.people}
                  selected={selected} onNodeClick={(n) => selected && fleet.goto(selected, n)} onUnitClick={setSelected} />
              : <div className="empty">Loading floor plan…</div>}
          </div>
          <EventLog log={fleet.log} />
        </section>

        <aside className="side">
          <section className="panel">
            <div className="panel-head"><h2>AI dispatch</h2><span className="badge-ai">Claude · Bedrock</span></div>
            <form className="console" onSubmit={submit}>
              <span className="prompt mono">&gt;</span>
              <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Tell the fleet what to do…" autoComplete="off" />
              <button className="btn-primary" type="submit" disabled={fleet.conn !== 'live' || !text.trim()}>Dispatch</button>
            </form>
            <div className="presets">
              {PRESETS.map((p) => <button key={p} className="chip" disabled={fleet.conn !== 'live'} onClick={() => submit(undefined, p)}>{p}</button>)}
            </div>
            <PlanView task={fleet.tasks.find((t) => t.task_id.startsWith('t-'))} />
          </section>

          <section className="panel">
            <div className="panel-head"><h2>Units</h2><span className="count">{ids.length}</span></div>
            <div className="units">
              {ids.map((id) => <UnitCard key={id} f={fleet.forklifts[id]} online={online(fleet.forklifts[id])}
                                         selected={id === selected} onSelect={() => setSelected(id)} onStop={() => fleet.stop(id)} />)}
            </div>
          </section>

          {cfg && (
            <section className="panel">
              <div className="panel-head"><h2>Planner lab</h2><span className="badge-ai">self-improving</span></div>
              <PlannerLab cfg={cfg} refreshKey={fleet.labTick} />
            </section>
          )}

          <section className="panel grow">
            <div className="panel-head"><h2>Task queue</h2><span className="count">{fleet.tasks.length}</span></div>
            <TaskList tasks={fleet.tasks} />
          </section>
        </aside>
      </div>
    </div>
  )
}

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub: string; tone?: 'ok' | 'accent' | 'danger' | 'muted' }) {
  return (
    <div className={`kpi ${tone ? `kpi-${tone}` : ''}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value mono">{value}</div>
      <div className="kpi-sub">{sub}</div>
    </div>
  )
}

function UnitCard({ f, online, selected, onSelect, onStop }: { f: Forklift; online: boolean; selected: boolean; onSelect: () => void; onStop: () => void }) {
  const pct = batteryPct(f.battery_v)
  const state = online ? f.state ?? 'IDLE' : 'OFFLINE'
  const dest = f.path?.nodes[f.path.nodes.length - 1]?.id
  return (
    <div className={`unit-card ${selected ? 'selected' : ''}`} onClick={onSelect} style={{ ['--unit' as string]: unitColour(f.id) }}>
      <div className="uc-top">
        <span className="uc-id mono">{f.id.toUpperCase()}</span>
        <span className={`pill pill-${state}`}>{state}</span>
        <div className="spacer" />
        <button className="btn-stop" onClick={(e) => { e.stopPropagation(); onStop() }} title="Emergency stop">■ STOP</button>
      </div>
      <div className="uc-grid">
        <div><label>Location</label><span className="mono">{f.node?.toUpperCase() ?? '—'}</span></div>
        <div><label>Speed</label><span className="mono">{online && f.speed != null ? `${f.speed.toFixed(2)} m/s` : '—'}</span></div>
        <div><label>Heading</label><span className="mono">{f.pose ? `${Math.round(((f.pose.theta * 180) / Math.PI + 360) % 360)}°` : '—'}</span></div>
      </div>
      <div className="uc-battery">
        <label>Battery</label>
        <div className="bar"><div className={`fill ${pct != null && pct < 25 ? 'low' : ''}`} style={{ width: `${pct ?? 0}%` }} /></div>
        <span className="mono">{pct != null ? `${Math.round(pct)}%` : '—'}</span>
      </div>
      <div className="uc-task">{dest ? <>En route to <b className="mono">{dest.toUpperCase()}</b></> : <span className="muted">No active task</span>}</div>
    </div>
  )
}

function TaskList({ tasks }: { tasks: Task[] }) {
  if (!tasks.length) return <div className="empty small">No tasks yet. Dispatch one above or click the map.</div>
  return (
    <ul className="tasks">
      {tasks.slice(0, 15).map((t) => (
        <li key={t.task_id}>
          <span className={`tstat tstat-${t.status}`} />
          <div className="t-main">
            <div className="t-text">{t.text ? t.text.replace(/^goto /, 'Go to ') : t.task_id}</div>
            <div className="t-meta mono">{t.task_id}{t.assigned_to && ` · ${t.assigned_to.toUpperCase()}`} · {ago(t.created_at)}</div>
            {t.error && <div className="t-err">{t.error}</div>}
          </div>
          <span className={`tlabel tlabel-${t.status}`}>{t.status}</span>
        </li>
      ))}
    </ul>
  )
}

function EventLog({ log }: { log: LogEntry[] }) {
  return (
    <div className="log">
      <div className="log-head"><span>EVENT STREAM</span><span className="muted">{log.length} events</span></div>
      <div className="log-body mono">
        {log.length === 0 && <div className="muted">Waiting for fleet activity…</div>}
        {log.map((e) => (
          <div key={e.id} className={`log-row lv-${e.level}`}>
            <span className="log-ts">{clock(e.ts)}</span>
            <span className="log-unit" style={e.unit ? { color: unitColour(e.unit) } : undefined}>{e.unit?.toUpperCase() ?? 'SYSTEM'}</span>
            <span className="log-text">{e.text}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
