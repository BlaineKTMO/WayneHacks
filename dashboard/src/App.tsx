import { Authenticator } from '@aws-amplify/ui-react'
import '@aws-amplify/ui-react/styles.css'
import { useEffect, useState, type FormEvent } from 'react'
import type { Config } from './config'
import { useFleet } from './useFleet'
import { FORKLIFT_COLOURS, WarehouseMap } from './WarehouseMap'

export default function App({ cfg }: { cfg: Config }) {
  return (
    <Authenticator hideSignUp>
      {({ signOut, user }) => <Dashboard cfg={cfg} email={user?.signInDetails?.loginId} signOut={signOut} />}
    </Authenticator>
  )
}

function Dashboard({ cfg, email, signOut }: { cfg: Config; email?: string; signOut?: () => void }) {
  const fleet = useFleet(cfg)
  const ids = Object.keys(fleet.forklifts).sort()
  const [selected, setSelected] = useState<string>()
  const [text, setText] = useState('')
  const [, tick] = useState(0)

  useEffect(() => {
    if (!selected && ids.length) setSelected(ids[0])
  }, [ids.join(), selected])
  useEffect(() => { // re-render each second so "offline" badges update
    const t = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!text.trim()) return
    fleet.submitTask(text.trim())
    setText('')
  }

  return (
    <div className="app">
      <header>
        <h1>Forklift fleet</h1>
        <span className={`conn conn-${fleet.conn}`}>{fleet.conn}</span>
        <span className="spacer" />
        <span className="muted">{email}</span>
        <button className="ghost" onClick={signOut}>Sign out</button>
      </header>
      {fleet.error && <div className="error">{fleet.error}</div>}

      <main>
        <section className="map-panel">
          <div className="hint">
            {selected ? <>Click a node to send <b style={{ color: FORKLIFT_COLOURS[selected] }}>{selected}</b> there</> : 'Loading…'}
          </div>
          {fleet.graph
            ? <WarehouseMap graph={fleet.graph} forklifts={fleet.forklifts} pallets={fleet.pallets}
                selected={selected} onNodeClick={(n) => selected && fleet.goto(selected, n)} />
            : <div className="placeholder">Loading map…</div>}
        </section>

        <aside>
          <form className="command" onSubmit={submit}>
            <label htmlFor="cmd">Command</label>
            <div className="row">
              <input id="cmd" value={text} onChange={(e) => setText(e.target.value)}
                     placeholder="move pallet 3 to dock B" autoComplete="off" />
              <button type="submit" disabled={fleet.conn !== 'live'}>Send</button>
            </div>
          </form>

          <h2>Forklifts</h2>
          {ids.map((id) => {
            const f = fleet.forklifts[id]
            const online = f.lastSeen && Date.now() - f.lastSeen < 5000
            return (
              <div key={id} className={`card ${id === selected ? 'selected' : ''}`} onClick={() => setSelected(id)}>
                <div className="card-head">
                  <span className="dot" style={{ background: FORKLIFT_COLOURS[id] }} />
                  <b>{id}</b>
                  <span className={`state state-${f.state ?? 'UNKNOWN'}`}>{online ? f.state ?? '—' : 'offline'}</span>
                  <button className="stop" onClick={(e) => { e.stopPropagation(); fleet.stop(id) }}>Stop</button>
                </div>
                <div className="card-body muted">
                  at {f.node ?? '—'}
                  {f.battery_v != null && <> · {f.battery_v.toFixed(1)} V</>}
                  {f.path && <> · → {f.path.nodes[f.path.nodes.length - 1]?.id}</>}
                </div>
              </div>
            )
          })}

          <h2>Tasks</h2>
          <ul className="tasks">
            {fleet.tasks.slice(0, 12).map((t) => (
              <li key={t.task_id}>
                <span className={`badge badge-${t.status}`}>{t.status}</span>
                <span className="task-text">{t.text ?? t.task_id}</span>
                {t.assigned_to && <span className="muted"> · {t.assigned_to}</span>}
                {t.error && <div className="task-error">{t.error}</div>}
              </li>
            ))}
            {!fleet.tasks.length && <li className="muted">No tasks yet</li>}
          </ul>
        </aside>
      </main>
    </div>
  )
}
