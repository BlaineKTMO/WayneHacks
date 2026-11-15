// /demo: hand-off iPad page. A scrolling story with the live fleet embedded in the middle.
import { Authenticator, ThemeProvider } from '@aws-amplify/ui-react'
import '@aws-amplify/ui-react/styles.css'
import { lazy, Suspense, useEffect, useMemo, useState, type FormEvent } from 'react'
import type { Config } from './config'
import { PlannerLab } from './PlannerLab'
import { PlanView } from './PlanView'
import { SimSwitch } from './SimSwitch'
import { CameraFeed } from './CameraFeed'
import { Architecture } from './Architecture'
import { useFleet } from './useFleet'
import { unitColour, WarehouseMap } from './WarehouseMap'
import './demo.css'

const Warehouse3D = lazy(() => import('./Warehouse3D'))

const PRESETS = ['Move pallet 3 to dock B', 'Bring pallet 1 to dock A', 'Swap pallets 1 and 2', 'Move pallet 2 to slot A4']

export default function Demo({ cfg }: { cfg: Config }) {
  useEffect(() => {
    document.body.classList.add('light')
    document.title = 'Overhead: the warehouse is the robot'
    return () => document.body.classList.remove('light')
  }, [])
  return (
    <ThemeProvider colorMode="light">
      <Authenticator hideSignUp components={{ Header: () => <div className="d-auth-brand">Overhead</div> }}>
        {() => <Page cfg={cfg} />}
      </Authenticator>
    </ThemeProvider>
  )
}

function Page({ cfg }: { cfg: Config }) {
  const fleet = useFleet(cfg)
  const ids = Object.keys(fleet.forklifts).sort()
  const [selected, setSelected] = useState<string>()
  const [text, setText] = useState('')
  const [toast, setToast] = useState<string>()
  const [view, setView] = useState<'3d' | '2d'>('3d')
  const [personMode, setPersonMode] = useState(false)
  const [confirmReset, setConfirmReset] = useState(false)
  const nodeById = useMemo(() => Object.fromEntries((fleet.graph?.nodes ?? []).map((n) => [n.id, n])), [fleet.graph])
  const onFloorTap = (n: string) => {
    if (!live) return
    if (personMode) {
      const node = nodeById[n]
      fleet.placePerson(node.x, node.y)
      setPersonMode(false)
      setToast('Person on the floor: forklifts heading there will stop')
      return
    }
    if (selected) { fleet.goto(selected, n); setToast(`${selected.toUpperCase()} → ${n.replace('_', ' ').toUpperCase()}`) }
  }
  const [now, setNow] = useState(Date.now())
  const firstId = ids[0]
  useEffect(() => { if (!selected && firstId) setSelected(firstId) }, [firstId, selected])
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [])
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(undefined), 2600); return () => clearTimeout(t) }, [toast])

  const online = Object.values(fleet.forklifts).filter((f) => f.lastSeen && now - f.lastSeen < 5000).length
  const done = fleet.tasks.filter((t) => t.status === 'done').length
  const latest = fleet.tasks.find((t) => t.task_id.startsWith('t-'))
  const live = fleet.conn === 'live'
  const canSend = live && !(latest && ['planning', 'running'].includes(latest.status))

  const send = (t: string) => {
    if (!t.trim()) return
    fleet.submitTask(t.trim())
    setText('')
    setToast(`Sent: “${t.trim()}”`)
    document.getElementById('plan')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
  const onSubmit = (e: FormEvent) => { e.preventDefault(); send(text) }

  const status = useMemo(() => {
    if (!live) return 'Connecting to the fleet…'
    return `${online} of ${ids.length} forklifts online · ${done} tasks completed`
  }, [live, online, ids.length, done])

  return (
    <div className="d">
      <nav className="d-nav">
        <a href="#top" className="d-logo">Overhead</a>
        <div className="d-links">
          <a href="#live">Live</a><a href="#how">How it works</a><a href="#architecture">Architecture</a><a href="#safety">Safety</a><a href="#learning">Learning</a><a href="#faq">FAQ</a>
        </div>
        <a className="d-nav-cta" href="#live">Try it</a>
      </nav>

      {/* ------------------------------------------------ hero */}
      <header id="top" className="d-hero">
        <p className="d-eyebrow"><span className={`d-dot ${live ? 'on' : ''}`} />{status}</p>
        <h1>The warehouse<br />is the robot.</h1>
        <p className="d-lede">Ceiling cameras see every forklift. One AI plans every job. The forklifts just follow the route, so simple robot chassis become an autonomous fleet.</p>
        <div className="d-hero-cta">
          <a className="d-btn primary" href="#live">Run the fleet ↓</a>
          <a className="d-btn" href="#how">How it works</a>
        </div>
      </header>

      {/* ------------------------------------------------ highlights */}
      <section className="d-highlights">
        <div><b>0</b><span>sensors on the forklift</span><p>Position comes from AprilTags seen by a camera in the ceiling, not lidar on every truck.</p></div>
        <div><b>~2 s</b><span>from words to a plan</span><p>Claude on Amazon Bedrock turns an order into a behavior tree that a validator checks before anything moves.</p></div>
        <div><b>&lt;0.5 s</b><span>to stop, even offline</span><p>Safety runs at the edge. If the Wi-Fi drops, the watchdog still stops the motors.</p></div>
      </section>

      {/* ------------------------------------------------ live */}
      <section id="live" className="d-section">
        <h2>Run the fleet<br /><span>from right here.</span></h2>
        <p className="d-sub">This is the real system, live. Tap an order, or pick a forklift and tap anywhere on the floor.</p>

        <div className="d-live">
          <div className="d-map-card">
            <div className="d-units">
              {ids.map((id) => {
                const f = fleet.forklifts[id]
                const on = f.lastSeen && now - f.lastSeen < 5000
                return (
                  <button key={id} className={`d-unit ${id === selected ? 'sel' : ''}`} style={{ ['--u' as string]: unitColour(id) }}
                          onClick={() => setSelected(id)}>
                    <span className="d-unit-dot" />
                    <span className="d-unit-id">{id.toUpperCase()}</span>
                    <span className="d-unit-state">{on ? (f.state ?? 'IDLE').toLowerCase() : 'offline'}</span>
                  </button>
                )
              })}
              <div className="d-seg">
                <button className={view === '3d' ? 'on' : ''} onClick={() => setView('3d')}>3D</button>
                <button className={view === '2d' ? 'on' : ''} onClick={() => setView('2d')}>2D</button>
              </div>
            </div>
            <p className="d-hint">{personMode ? <b className="d-warn">Tap where the person should stand</b> : selected ? <>Tap a spot on the floor to send <b>{selected.toUpperCase()}</b>{view === '3d' ? ' · drag to orbit, pinch to zoom' : ''}</> : 'Pick a forklift'}</p>
            <div className={`d-map ${view}`}>
              {fleet.graph && view === '3d' && (
                <Suspense fallback={<div className="d-empty">Loading 3D…</div>}>
                  <Warehouse3D graph={fleet.graph} forklifts={fleet.forklifts} pallets={fleet.pallets} locks={fleet.locks}
                    selected={selected} onUnitClick={setSelected}
                    people={fleet.people} onNodeClick={onFloorTap} />
                </Suspense>
              )}
              {fleet.graph && view === '2d'
                ? <WarehouseMap graph={fleet.graph} forklifts={fleet.forklifts} trails={fleet.trails} pallets={fleet.pallets}
                    locks={fleet.locks} people={fleet.people} selected={selected}
                    onNodeClick={onFloorTap}
                    onUnitClick={setSelected} />
                : !fleet.graph && <div className="d-empty">Loading the floor…</div>}
            </div>
            <div className="d-actions">
              {fleet.people.length
                ? <button className="d-person on" onClick={() => { fleet.clearPerson(); setToast('Person removed: forklifts resume') }}>🚶 Remove the person</button>
                : <button className={`d-person ${personMode ? 'arming' : ''}`} onClick={() => setPersonMode((m) => !m)}>
                    🚶 {personMode ? 'Cancel' : 'Put a person on the floor'}
                  </button>}
              <SimSwitch cfg={cfg} />
            </div>
            <div className="d-stops">
              {ids.map((id) => (
                <button key={id} className="d-stop" onClick={() => { fleet.stop(id); setToast(`Stop sent to ${id.toUpperCase()}`) }}>
                  ■ Stop {id.toUpperCase()}
                </button>
              ))}
            </div>
          </div>

          <div className="d-control">
            <h3>Give an order</h3>
            <div className="d-presets">
              {PRESETS.map((p) => (
                <button key={p} className="d-preset" disabled={!canSend} onClick={() => send(p)}>{p}</button>
              ))}
            </div>
            <form className="d-input" onSubmit={onSubmit}>
              <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Or type your own…" enterKeyHint="send" />
              <button type="submit" disabled={!canSend || !text.trim()}>Send</button>
            </form>
            {!canSend && live && <p className="d-busy">A job is in progress. New orders unlock when it finishes.</p>}
            <CameraFeed camera={fleet.camera} className="d-cam" />
            <div id="plan" className="d-plan">
              <h4>Claude's plan</h4>
              <PlanView task={latest} />
            </div>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------ how */}
      <section id="how" className="d-section">
        <h2>One loop.<br /><span>Edge sees, cloud thinks.</span></h2>
        <p className="d-sub">Anything real-time runs next to the cameras. The cloud plans, stores and learns, so a slow network never moves a forklift.</p>
        <div className="d-flow">
          {[
            ['Ceiling camera', 'AprilTags give every forklift’s position 15–30× a second.'],
            ['Edge laptop', 'Localizes, steers, and stops for people. Works offline.'],
            ['AWS IoT Core', 'Every message meets here: poses, paths, events.'],
            ['Claude on Bedrock', 'Turns the order into a behavior tree from 7 fixed building blocks.'],
            ['Validator', 'Replays the plan. Wrong pallet, full slot, bad route: rejected.'],
            ['Forklift', 'Follows reserved lanes, one short chunk at a time.'],
          ].map(([t, d], i) => (
            <div key={t} className="d-step"><span className="d-step-n">{i + 1}</span><b>{t}</b><p>{d}</p></div>
          ))}
        </div>
      </section>

      {/* ------------------------------------------------ architecture */}
      <section id="architecture" className="d-section alt">
        <h2>Under the hood.<br /><span>Every part, every wire.</span></h2>
        <p className="d-sub">Four places, one message bus. Pick a flow to trace it, or tap any box to see what it does.</p>
        <Architecture />
      </section>

      {/* ------------------------------------------------ safety */}
      <section id="safety" className="d-section alt">
        <h2>The AI never<br /><span>touches the motors.</span></h2>
        <div className="d-cards">
          <div><b>No coordinates.</b><p>Claude only sees node names like “dock B”. It can’t steer, and it can’t invent a place.</p></div>
          <div><b>Checked before moving.</b><p>Every plan is replayed against the live warehouse. Invalid plans go back to Claude once, then fail safely.</p></div>
          <div><b>Two forklifts, one lane.</b><p>A forklift must reserve the next stops before driving them. Blocked for 5 s? It reroutes. A parked forklift in the way moves aside.</p></div>
        </div>
      </section>

      {/* ------------------------------------------------ learning */}
      <section id="learning" className="d-section">
        <h2>It gets better<br /><span>with every order.</span></h2>
        <p className="d-sub">Every job is graded by what really happened. A candidate planner quietly writes its own plan for each order, scored in a simulator but never run. When it keeps winning, promote it.</p>
        <div className="d-lab"><PlannerLab cfg={cfg} refreshKey={fleet.labTick} /></div>
      </section>

      {/* ------------------------------------------------ faq */}
      <section id="faq" className="d-section alt">
        <h2>Questions?<br /><span>Answers.</span></h2>
        <div className="d-faq">
          {[
            ['Why not put sensors on each forklift?', 'Lidar and compute on every truck is the expensive part. One camera covers a whole aisle, and the same model serves the whole fleet.'],
            ['What happens if the internet goes down?', 'Forklifts finish the stretch they’ve reserved and stop. Seeing, steering and the people-stop all run on the edge laptop.'],
            ['Can the AI do something dangerous?', 'It can only arrange 7 fixed building blocks (go to, pick, drop, wait, retry…). A validator checks the plan against the real warehouse before anything moves.'],
            ['What if Claude is unavailable?', 'A rule-based backup planner handles common orders, and the dashboard shows which planner wrote each plan.'],
            ['What’s it built on?', 'AWS IoT Core, Lambda, DynamoDB, S3, Amazon Bedrock (Claude), Cognito and Amplify. A Free-WiLi drives each forklift’s motors.'],
          ].map(([q, a]) => (
            <details key={q}><summary>{q}</summary><p>{a}</p></details>
          ))}
        </div>
      </section>

      <footer className="d-foot">
        <span>Overhead · WayneHacks 2026</span>
        {confirmReset
          ? <span className="d-reset">Reset pallets, tasks and planners?{' '}
              <button onClick={async () => { setConfirmReset(false); await fleet.resetDemo(); setToast('Demo reset'); setTimeout(() => window.location.reload(), 900) }}>Yes, reset</button>{' '}
              <button onClick={() => setConfirmReset(false)}>Cancel</button></span>
          : <button className="d-reset-btn" onClick={() => setConfirmReset(true)}>Reset demo</button>}
        <a href="/">Open the operator console →</a>
      </footer>

      {toast && <div className="d-toast">{toast}</div>}
    </div>
  )
}
