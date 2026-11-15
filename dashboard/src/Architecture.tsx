// Architecture diagram for /demo: every component and the technology behind it.
// Pick a flow to light up the path a message takes; tap a box for details.
import { useState } from 'react'

type Group = 'truck' | 'edge' | 'aws' | 'client'
type Flow = 'order' | 'pose' | 'learn'
type Node = { id: string; x: number; y: number; w: number; h: number; g: Group; title: string; tech: string; detail: string; icon: string }
type Edge = { a: string; b: string; flows: Flow[]; label: string; both?: boolean }

const W = 170, H = 68
const NODES: Node[] = [
  // forklift
  { id: 'fw', x: 34, y: 112, w: 184, h: H, g: 'truck', icon: '◼', title: 'Free-WiLi OG', tech: 'C firmware · 0.5 s watchdog',
    detail: 'The forklift’s controller. Reads W/F/S command lines, drives PWM to the motor driver, reports battery and state at 1 Hz, and stops on its own if commands go quiet for 0.5 s.' },
  { id: 'drv', x: 34, y: 222, w: 184, h: H, g: 'truck', icon: '⚡', title: 'L298N driver', tech: 'Dual H-bridge · PWM',
    detail: 'Turns the Free-WiLi’s logic-level PWM into motor current for both wheels and the fork lift.' },
  { id: 'mot', x: 34, y: 332, w: 184, h: H, g: 'truck', icon: '⚙', title: 'Drive + fork motors', tech: 'Differential drive · lift',
    detail: 'Two DC drive motors (skid steer) and one fork motor. No sensors on the truck: the ceiling camera is its eyes.' },
  // edge
  { id: 'cam', x: 282, y: 72, w: 224, h: H, g: 'edge', icon: '◉', title: 'Ceiling camera', tech: 'USB webcam · 15–30 fps',
    detail: 'Looks straight down at the floor. Corner AprilTags anchor the map, one tag on each forklift’s roof gives its pose.' },
  { id: 'loc', x: 282, y: 172, w: 224, h: H, g: 'edge', icon: '⌖', title: 'AprilTag localizer', tech: 'OpenCV · pupil-apriltags · homography',
    detail: 'Undistorts each frame, detects tag36h11 tags, maps pixels to floor metres through a homography and corrects for the raised roof tag.' },
  { id: 'ctl', x: 282, y: 272, w: 224, h: H, g: 'edge', icon: '↻', title: 'Drive controller', tech: 'Turn-then-drive · pure pursuit · people-stop',
    detail: 'Follows the reserved path one short chunk at a time. Stops for people, lost tags and the camera border. Works with no internet.' },
  { id: 'brg', x: 282, y: 372, w: 224, h: H, g: 'edge', icon: '⇅', title: 'Fleet bridge', tech: 'AWS IoT Device SDK · X.509 · MQTT/TLS',
    detail: 'Publishes pose, telemetry, events and JPEG camera frames; receives paths and stops. The device policy only allows fleet/* and warehouse/* topics.' },
  // client
  { id: 'ipad', x: 282, y: 512, w: 224, h: 76, g: 'client', icon: '▭', title: 'iPad / browser', tech: 'React · Vite · Three.js · Amplify UI',
    detail: 'This page. It shows the live 3D floor over MQTT WebSockets and has the order box, stop buttons and planner lab.' },
  // aws
  { id: 'sim', x: 582, y: 62, w: W, h: H, g: 'aws', icon: '▤', title: 'Sim fleet', tech: 'ECS Fargate · scales 0 → 1',
    detail: 'Simulated forklifts that drive real routes on the real cloud. The “Simulated fleet” switch scales the service from 0 to 1.' },
  { id: 'iot', x: 582, y: 162, w: W, h: 168, g: 'aws', icon: '✦', title: 'AWS IoT Core', tech: 'MQTT broker · topic rules · device policy',
    detail: 'All messages go through here: fleet/+/pose, telemetry, event, goto, path, tasks/new, warehouse/camera. Topic rules fan out to Lambda and S3.' },
  { id: 's3', x: 582, y: 412, w: W, h: H, g: 'aws', icon: '▥', title: 'Amazon S3', tech: 'Data lake · raw/ by date',
    detail: 'IoT rules archive every telemetry message and event. Planner episodes land here for evaluation.' },
  { id: 'ing', x: 790, y: 62, w: W, h: H, g: 'aws', icon: 'λ', title: 'Ingest', tech: 'Lambda · Python 3.12 · arm64',
    detail: 'Topic rule target for telemetry, events and a 10 % sample of poses. Keeps FleetForklifts current.' },
  { id: 'rp', x: 790, y: 162, w: W, h: H, g: 'aws', icon: 'λ', title: 'RoutePlanner', tech: 'Lambda · A* · lane locks',
    detail: 'fleet/+/goto → A* over the warehouse graph → fleet/{id}/path. Reserves lanes in FleetEdgeLocks (TTL) so two forklifts never share one.' },
  { id: 'dsp', x: 790, y: 262, w: W, h: H, g: 'aws', icon: 'λ', title: 'Dispatch', tech: 'Lambda · validator · tree runner',
    detail: 'tasks/new → ask the OE for a behavior tree → replay it against the live warehouse → run it step by step. One retry, then fail safely.' },
  { id: 'api', x: 790, y: 362, w: W, h: H, g: 'aws', icon: 'λ', title: 'Api', tech: 'Lambda · REST handlers',
    detail: 'Forklifts, pallets, tasks, stop, planner compare / promote, sim switch, hazards and demo reset.' },
  { id: 'gw', x: 790, y: 462, w: W, h: H, g: 'aws', icon: '⇄', title: 'API Gateway', tech: 'HTTP API · JWT authorizer',
    detail: 'HTTPS front door for the dashboard. Requests carry the Cognito ID token.' },
  { id: 'ddb', x: 998, y: 62, w: W, h: 168, g: 'aws', icon: '▦', title: 'DynamoDB', tech: 'Forklifts · Tasks · Pallets · EdgeLocks · Episodes · Planners',
    detail: 'All live state. EdgeLocks expire by TTL; Episodes grade every job; Planners holds prod and shadow prompts / models.' },
  { id: 'brk', x: 998, y: 262, w: W, h: H, g: 'aws', icon: '✧', title: 'Amazon Bedrock', tech: 'OE planner model · Converse · tool use',
    detail: 'Turns an order into a behavior tree built from 7 fixed primitives. It only sees node names, never coordinates or motors.' },
  { id: 'sm', x: 998, y: 362, w: W, h: H, g: 'aws', icon: '◇', title: 'SageMaker', tech: 'Processing job · offline gate',
    detail: 'Replays candidate planners over the eval set in the simulator. A candidate is promoted only if it matches prod’s success with no new safety violations.' },
  { id: 'cog', x: 998, y: 462, w: W, h: H, g: 'aws', icon: '⚿', title: 'Cognito', tech: 'User pool · identity pool',
    detail: 'Invite-only sign-in. The identity pool hands the browser short-lived IAM credentials for MQTT over WebSockets.' },
  { id: 'amp', x: 582, y: 512, w: W, h: 76, g: 'aws', icon: '▲', title: 'Amplify Hosting', tech: 'Static SPA · CDN',
    detail: 'Serves this React app. Deployed by dashboard/deploy.py.' },
]

const EDGES: Edge[] = [
  { a: 'cam', b: 'loc', flows: ['pose'], label: 'frames' },
  { a: 'loc', b: 'ctl', flows: ['pose'], label: 'pose 30 Hz' },
  { a: 'brg', b: 'ctl', flows: ['order'], label: 'path chunks' },
  { a: 'ctl', b: 'fw', flows: ['order', 'pose'], label: 'USB serial / Wi-Fi · W,F,S @20 Hz' },
  { a: 'fw', b: 'drv', flows: ['order'], label: 'PWM' },
  { a: 'drv', b: 'mot', flows: ['order'], label: 'current' },
  { a: 'brg', b: 'iot', flows: ['pose', 'order'], label: 'MQTT/TLS', both: true },
  { a: 'sim', b: 'iot', flows: ['pose'], label: 'sim poses' },
  { a: 'iot', b: 'ing', flows: ['pose'], label: 'rule' },
  { a: 'ing', b: 'ddb', flows: ['pose'], label: 'state' },
  { a: 'iot', b: 'rp', flows: ['order'], label: 'goto → path', both: true },
  { a: 'rp', b: 'ddb', flows: ['order'], label: 'lane locks' },
  { a: 'iot', b: 'dsp', flows: ['order'], label: 'tasks/new', both: true },
  { a: 'dsp', b: 'brk', flows: ['order', 'learn'], label: 'behavior tree', both: true },
  { a: 'dsp', b: 'ddb', flows: ['order', 'learn'], label: 'tasks · episodes' },
  { a: 'iot', b: 's3', flows: ['pose', 'learn'], label: 'archive rule' },
  { a: 's3', b: 'sm', flows: ['learn'], label: 'episodes' },
  { a: 'sm', b: 'brk', flows: ['learn'], label: 'shadow plans' },
  { a: 'api', b: 'ddb', flows: ['learn'], label: 'promote' },
  { a: 'gw', b: 'api', flows: ['learn'], label: 'invoke' },
  { a: 'ipad', b: 'gw', flows: ['learn'], label: 'HTTPS + JWT' },
  { a: 'ipad', b: 'iot', flows: ['order', 'pose'], label: 'MQTT over WSS', both: true },
  { a: 'amp', b: 'ipad', flows: [], label: 'static site' },
  { a: 'cog', b: 'ipad', flows: [], label: 'sign-in + creds' },
]

const FLOWS: { id: Flow | 'all'; name: string; steps: string[] }[] = [
  { id: 'all', name: 'Everything', steps: ['Tap a flow to trace one message through the system, or tap any box for what it does.'] },
  { id: 'order', name: 'An order', steps: [
    'You type “Move pallet 3 to dock B”. The iPad publishes tasks/new over MQTT.',
    'Dispatch asks the OE (Orchestration Engine, on Bedrock) for a behavior tree, then the validator replays it against live pallets and slots.',
    'Each GoTo publishes fleet/{id}/goto. RoutePlanner runs A*, reserves lanes and returns a path.',
    'The edge bridge feeds the controller short chunks; the Free-WiLi turns them into PWM.'] },
  { id: 'pose', name: 'Live position', steps: [
    'The ceiling camera sees each forklift’s roof tag; the localizer turns pixels into floor metres.',
    'The controller steers on that pose locally, so steering never waits on the network.',
    'The bridge publishes pose and telemetry to IoT Core; rules update DynamoDB and archive to S3.',
    'The iPad subscribes over WebSockets and draws the 3D floor in real time.'] },
  { id: 'learn', name: 'Learning loop', steps: [
    'Every finished job is graded and saved as an episode (DynamoDB + S3).',
    'A shadow planner on Bedrock writes its own plan for every order; it is scored, never run.',
    'A SageMaker Processing job replays candidates over the eval set as an offline gate.',
    'When a candidate keeps winning, promote it from the planner lab (API Gateway → Api → FleetPlanners).'] },
]

const GROUPS: { g: Group; x: number; y: number; w: number; h: number; label: string }[] = [
  { g: 'truck', x: 16, y: 72, w: 220, h: 348, label: 'On the forklift' },
  { g: 'edge', x: 264, y: 32, w: 260, h: 428, label: 'Edge laptop · Python' },
  { g: 'client', x: 264, y: 482, w: 260, h: 124, label: 'Operator' },
  { g: 'aws', x: 562, y: 22, w: 624, h: 584, label: 'AWS · us-east-2 · CDK (Python)' },
]

const byId = Object.fromEntries(NODES.map((n) => [n.id, n]))

function route(a: Node, b: Node) {
  const ax = a.x + a.w / 2, ay = a.y + a.h / 2, bx = b.x + b.w / 2, by = b.y + b.h / 2
  const gapX = Math.max(a.x, b.x) - Math.min(a.x + a.w, b.x + b.w)
  if (gapX > 8) {
    const r = bx > ax
    const x1 = r ? a.x + a.w : a.x, x2 = r ? b.x : b.x + b.w
    const y1 = Math.max(a.y + 14, Math.min(a.y + a.h - 14, by)), y2 = Math.max(b.y + 14, Math.min(b.y + b.h - 14, ay))
    const c = (x2 - x1) / 2
    return { d: `M${x1},${y1} C${x1 + c},${y1} ${x2 - c},${y2} ${x2},${y2}`, mx: (x1 + x2) / 2, my: (y1 + y2) / 2 }
  }
  const dn = by > ay
  const y1 = dn ? a.y + a.h : a.y, y2 = dn ? b.y : b.y + b.h
  const c = (y2 - y1) / 2
  return { d: `M${ax},${y1} C${ax},${y1 + c} ${bx},${y2 - c} ${bx},${y2}`, mx: (ax + bx) / 2, my: (y1 + y2) / 2 }
}

export function Architecture() {
  const [flow, setFlow] = useState<Flow | 'all'>('all')
  const [sel, setSel] = useState<string>()
  const active = (e: Edge) => flow === 'all' || e.flows.includes(flow as Flow)
  const litNodes = new Set(flow === 'all' ? NODES.map((n) => n.id) : EDGES.filter(active).flatMap((e) => [e.a, e.b]))
  const node = sel ? byId[sel] : undefined
  const f = FLOWS.find((x) => x.id === flow)!

  return (
    <div className="arch">
      <div className="arch-tabs" role="tablist">
        {FLOWS.map((x) => (
          <button key={x.id} role="tab" aria-selected={flow === x.id} className={flow === x.id ? 'on' : ''}
                  onClick={() => setFlow(x.id)}>{x.name}</button>
        ))}
      </div>

      <div className="arch-scroll">
        <svg className={`arch-svg flow-${flow}`} viewBox="0 0 1200 620" role="img"
             aria-label="Architecture: forklift, edge laptop, AWS cloud and operator iPad">
          <defs>
            <marker id="arch-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,1 L9,5 L0,9 z" className="arch-arrowhead" />
            </marker>
          </defs>

          {GROUPS.map((g) => (
            <g key={g.g + g.y} className={`arch-group g-${g.g}`}>
              <rect x={g.x} y={g.y} width={g.w} height={g.h} rx={20} />
              <text x={g.x + 16} y={g.y + 24}>{g.label}</text>
            </g>
          ))}

          {EDGES.map((e) => {
            const r = route(byId[e.a], byId[e.b])
            const on = active(e)
            return (
              <g key={e.a + e.b} className={`arch-edge ${on ? 'on' : 'off'} ${flow !== 'all' && on ? 'live' : ''}`}>
                <path d={r.d} markerEnd="url(#arch-arrow)" markerStart={e.both ? 'url(#arch-arrow)' : undefined} />
                {flow !== 'all' && on && (
                  <g transform={`translate(${r.mx},${r.my})`}>
                    <rect x={-e.label.length * 3.1 - 7} y={-10} width={e.label.length * 6.2 + 14} height={20} rx={10} />
                    <text textAnchor="middle" y={4}>{e.label}</text>
                  </g>
                )}
              </g>
            )
          })}

          {NODES.map((n) => (
            <g key={n.id} className={`arch-node g-${n.g} ${litNodes.has(n.id) ? '' : 'dim'} ${sel === n.id ? 'sel' : ''}`}
               onClick={() => setSel(sel === n.id ? undefined : n.id)} role="button" tabIndex={0}
               onKeyDown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') setSel(n.id) }}>
              <title>{`${n.title}: ${n.detail}`}</title>
              <rect x={n.x} y={n.y} width={n.w} height={n.h} rx={14} />
              <circle cx={n.x + 22} cy={n.y + 24} r={12} className="arch-icon-bg" />
              <text x={n.x + 22} y={n.y + 28.5} textAnchor="middle" className="arch-icon">{n.icon}</text>
              <text x={n.x + 42} y={n.y + 29} className="arch-title">{n.title}</text>
              <foreignObject x={n.x + 10} y={n.y + 40} width={n.w - 20} height={n.h - 42}>
                <div className="arch-tech">{n.tech}</div>
              </foreignObject>
            </g>
          ))}
        </svg>
      </div>

      <div className="arch-detail">
        {node
          ? <><b className={`g-${node.g}`}>{node.title}</b><span className="arch-detail-tech">{node.tech}</span><p>{node.detail}</p></>
          : <ol>{f.steps.map((s) => <li key={s}>{s}</li>)}</ol>}
      </div>

      <div className="arch-legend">
        <span className="g-truck">Forklift hardware</span>
        <span className="g-edge">Edge (real-time, offline-safe)</span>
        <span className="g-aws">AWS cloud</span>
        <span className="g-client">Operator</span>
      </div>
    </div>
  )
}
