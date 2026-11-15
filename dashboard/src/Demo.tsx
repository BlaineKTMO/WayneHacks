// /demo: hand-off iPad page. A scrolling story with the live fleet embedded in the middle.
import { Authenticator, ThemeProvider } from '@aws-amplify/ui-react'
import '@aws-amplify/ui-react/styles.css'
import { lazy, Suspense, useEffect, useMemo, useState, type FormEvent } from 'react'
import type { Config, SiteInfo } from './config'
import { SiteSwitcher, useSite } from './sites'
import { PlannerLab } from './PlannerLab'
import { PlanView } from './PlanView'
import { SimSwitch } from './SimSwitch'
import { CameraFeed } from './CameraFeed'
import { Devices } from './Devices'
import { MissionPanel } from './MissionPanel'
import { Architecture } from './Architecture'
import { Notes, Problem, SideBySide } from './Story'
import { useFleet } from './useFleet'
import { unitColour, WarehouseMap } from './WarehouseMap'
import './demo.css'

const Warehouse3D = lazy(() => import('./Warehouse3D'))

const DEMO_PRESETS = ['Move pallet 3 to dock B', 'Bring pallet 1 to dock A', 'Swap pallets 1 and 2', 'Move pallet 2 to slot A4']
const MISSION_PRESETS = ['Move pallet 1 to dock 4', 'Move pallet 5 to dock 1', 'Bring pallet 9 to dock 2', 'Swap pallets 1 and 9']

export default function Demo({ cfg }: { cfg: Config }) {
  useEffect(() => {
    document.body.classList.add('light')
    document.title = 'FLOE: Factory Logistics and Orchestration Engine'
    return () => document.body.classList.remove('light')
  }, [])
  return (
    <ThemeProvider colorMode="light">
      <Authenticator hideSignUp components={{ Header: () => <div className="d-auth-brand">FLOE</div> }}>
        {() => <SitePage cfg={cfg} />}
      </Authenticator>
    </ThemeProvider>
  )
}

function SitePage({ cfg }: { cfg: Config }) {
  const [siteCfg, sites, setSite] = useSite(cfg, 'demo')   // the demo page opens on the simulated site
  return <Page key={siteCfg.site} cfg={siteCfg} sites={sites} onSite={setSite} />
}

function Page({ cfg, sites, onSite }: { cfg: Config; sites: SiteInfo[]; onSite: (id: string) => void }) {
  const fleet = useFleet(cfg)
  const ids = Object.keys(fleet.forklifts).sort()
  const [selected, setSelected] = useState<string>()
  const [text, setText] = useState('')
  const [toast, setToast] = useState<string>()
  const [view, setView] = useState<'3d' | '2d'>('3d')
  const [personMode, setPersonMode] = useState(false)
  const [armReset, setArmReset] = useState(false)
  const [resetting, setResetting] = useState(false)
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
        <a href="#top" className="d-logo">FLOE</a>
        <div className="d-links">
          <a href="#problem">Problem</a><a href="#live">Live</a><a href="#mission">Mission</a><a href="#how">How it works</a><a href="#architecture">Architecture</a><a href="#safety">Safety</a><a href="#learning">Learning</a><a href="#faq">FAQ</a>
        </div>
        <SiteSwitcher sites={sites} current={cfg.site} onChange={onSite} className="d-site" />
        <a className="d-nav-cta" href="#live">Try it</a>
      </nav>

      {/* ------------------------------------------------ hero */}
      <header id="top" className="d-hero">
        <p className="d-eyebrow"><span className={`d-dot ${live ? 'on' : ''}`} />{status}</p>
        <h1>The warehouse<br />is the robot.</h1>
        <p className="d-lede">Cameras in the ceiling track every forklift and the cloud plans the work, so the forklifts themselves can stay cheap and simple. We built it with toy forklifts, a webcam and AWS.</p>
        <div className="d-hero-cta">
          <a className="d-btn primary" href="#live">Run the fleet ↓</a>
          <a className="d-btn" href="#problem">Why it matters</a>
        </div>
      </header>

      {/* ------------------------------------------------ highlights */}
      <section className="d-highlights">
        <div><b>0</b><span>sensors on the forklift</span><p>Each forklift has an AprilTag on its roof. A camera in the ceiling reads it, so the truck doesn’t need lidar.</p></div>
        <div><b>~2 s</b><span>from words to a plan</span><p>You type an order, the OE (Orchestration Engine) on Amazon Bedrock writes a plan, and we check that plan before any forklift moves.<sup><a href="#notes">3</a></sup></p></div>
        <div><b>&lt;0.5 s</b><span>to stop, even offline</span><p>Stopping is handled on the forklift and the laptop beside the camera. If the Wi-Fi drops, the Free-WiLi’s watchdog cuts the motors anyway.</p></div>
      </section>

      {/* ------------------------------------------------ problem */}
      <section id="problem" className="d-section">
        <h2>Self-driving forklifts<br /><span>are expensive.</span></h2>
        <p className="d-sub">Today each autonomous forklift carries its own sensors and computer. If you want twenty of them, you pay for that hardware twenty times.</p>
        <Problem />
      </section>

      {/* ------------------------------------------------ live */}
      <section id="live" className="d-section">
        <h2>Run the fleet<br /><span>from right here.</span></h2>
        <p className="d-sub">This is the live system. Tap one of the orders, or pick a forklift and tap a spot on the floor to send it there.</p>

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
              {cfg.site !== 'wh1' && <SimSwitch cfg={cfg} />}
              {cfg.site !== 'wh1' && (
                <button className={`d-reset-sim ${armReset ? 'armed' : ''}`} disabled={resetting}
                        onClick={async () => {
                          if (!armReset) { setArmReset(true); setTimeout(() => setArmReset(false), 4000); return }
                          setArmReset(false); setResetting(true)
                          try {
                            await fleet.resetDemo()
                            setToast('Resetting: forklifts return home, aisles restocked (~1 min)')
                          } finally { setTimeout(() => setResetting(false), 60_000) }
                        }}>
                  {resetting ? '↺ Resetting… (~1 min)' : armReset ? 'Tap again to reset' : '↺ Reset sim'}
                </button>
              )}
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
              {(fleet.graph?.nodes.some((n) => n.id === 'dock_4') ? MISSION_PRESETS : DEMO_PRESETS).map((p) => (
                <button key={p} className="d-preset" disabled={!canSend} onClick={() => send(p)}>{p}</button>
              ))}
            </div>
            <form className="d-input" onSubmit={onSubmit}>
              <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Or type your own…" enterKeyHint="send" />
              <button type="submit" disabled={!canSend || !text.trim()}>Send</button>
            </form>
            {!canSend && live && <p className="d-busy">A job is in progress. New orders unlock when it finishes.</p>}
            <div className="d-devs"><h4>Devices</h4><Devices health={fleet.health} /></div>
            <CameraFeed camera={fleet.camera} className="d-cam" />
            <div id="plan" className="d-plan">
              <h4>Orchestration Engine plan</h4>
              <PlanView task={latest} />
            </div>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------ missions */}
      {fleet.graph?.nodes.some((n) => n.id === 'dock_4') && (
        <section id="mission" className="d-section">
          <h2>Empty the aisles.<br /><span>Pick the docks.</span></h2>
          <p className="d-sub">Choose which aisles ship to which truck docks. The whole fleet splits the work: every free forklift takes the nearest pallet, lanes are reserved, and docks clear as trucks are loaded.</p>
          <div className="d-mission"><MissionPanel cfg={cfg} live={fleet.mission} pallets={fleet.pallets} /></div>
        </section>
      )}

      {/* ------------------------------------------------ how */}
      <section id="how" className="d-section">
        <h2>How it works.<br /><span>The edge drives, the cloud plans.</span></h2>
        <p className="d-sub">Anything time-sensitive runs on the laptop next to the camera. The cloud handles planning, storage and learning, so a slow connection can’t make a forklift swerve.</p>
        <div className="d-flow">
          {[
            ['Ceiling camera', 'Reads the AprilTags and finds each forklift 15–30 times a second.'],
            ['Edge laptop', 'Works out positions, steers the forklifts and stops them if a person is in the way. Keeps working offline.'],
            ['AWS IoT Core', 'The MQTT broker everything talks through: positions, paths and events.'],
            ['Orchestration Engine (OE)', 'Runs on Amazon Bedrock and turns the order into a behavior tree using 7 fixed actions.'],
            ['Validator', 'Simulates the plan first and rejects it if it grabs the wrong pallet, targets a full slot or takes a bad route.'],
            ['Forklift', 'Drives the lanes it has reserved, a short section at a time.'],
          ].map(([t, d], i) => (
            <div key={t} className="d-step"><span className="d-step-n">{i + 1}</span><b>{t}</b><p>{d}</p></div>
          ))}
        </div>
      </section>

      {/* ------------------------------------------------ architecture */}
      <section id="architecture" className="d-section alt">
        <h2>Architecture.<br /><span>Everything we built.</span></h2>
        <p className="d-sub">Pick a flow to see the path a message takes, or tap a box to read what that part does.</p>
        <Architecture />
      </section>

      {/* ------------------------------------------------ side by side */}
      <section id="compare" className="d-section">
        <h2>Side by side.</h2>
        <p className="d-sub">The same job, done two ways.</p>
        <SideBySide />
      </section>

      {/* ------------------------------------------------ safety */}
      <section id="safety" className="d-section alt">
        <h2>The AI never<br /><span>touches the motors.</span></h2>
        <div className="d-cards">
          <div><b>No coordinates.</b><p>The OE only works with place names like “dock B”. It never sees coordinates and has no way to steer.</p></div>
          <div><b>Checked before moving.</b><p>Each plan is simulated against the current warehouse first. If it fails, the OE gets one more try, and after that the order is cancelled.</p></div>
          <div><b>Lanes are reserved.</b><p>A forklift has to reserve the next few stops before it drives them. If it’s blocked for 5 seconds it finds another route, and a parked forklift that’s in the way moves aside.</p></div>
        </div>
      </section>


      {/* ------------------------------------------------ learning */}
      <section id="learning" className="d-section">
        <h2>It gets better<br /><span>with every order.</span></h2>
        <p className="d-sub">We grade every job on what actually happened. A second, candidate planner also writes a plan for each order. Its plan is scored in a simulator but never run, and if it keeps scoring better you can promote it.</p>
        <div className="d-lab"><PlannerLab cfg={cfg} refreshKey={fleet.labTick} /></div>
      </section>

      {/* ------------------------------------------------ faq */}
      <section id="faq" className="d-section alt">
        <h2>Questions?<br /><span>Answers.</span></h2>
        <div className="d-faq">
          {[
            ['Why not put sensors on each forklift?', 'Putting lidar and a computer on every truck is the expensive part. One ceiling camera covers a whole aisle, and one planner serves the whole fleet.'],
            ['What happens if the internet goes down?', 'Each forklift finishes the stretch it has reserved and then stops. Tracking, steering and the people-stop all run on the edge laptop, so they keep working.'],
            ['Can the AI do something dangerous?', 'It can only arrange 7 fixed building blocks (go to, pick, drop, wait, retry…). A validator checks the plan against the real warehouse before anything moves.'],
            ['What if the OE is unavailable?', 'A rule-based backup planner handles common orders, and the dashboard shows which planner wrote each plan.'],
            ['What’s it built on?', 'AWS IoT Core, Lambda, DynamoDB, S3, Amazon Bedrock (the OE’s planner), Cognito and Amplify. A Free-WiLi drives each forklift’s motors.'],
          ].map(([q, a]) => (
            <details key={q}><summary>{q}</summary><p>{a}</p></details>
          ))}
        </div>
      </section>

      <section className="d-section d-notes-sec"><h4 className="s-notes-h">Notes</h4><Notes /></section>

      <footer className="d-foot">
        <span>FLOE · Factory Logistics and Orchestration Engine · WayneHacks 2026</span>
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
