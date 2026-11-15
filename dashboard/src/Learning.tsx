// /demo: what the learning loop has actually delivered, and how the MLOps pipeline works.
// Figures are a snapshot of FleetPlanners.eval and FleetTasks.shadow taken 15 Nov 2026 (see note 4).
import './learning.css'

const GATE = {
  prod: { name: 'v1-naive', model: 'Claude Haiku 4.5, short prompt', success: 95.7, invalid: 1, cost: 9.28 },
  cand: { name: 'v2-sonnet', model: 'Claude Sonnet 4.6, full planning rules', success: 100, invalid: 0, cost: 3.44 },
  cases: 23,
}
const SHADOW = { orders: 33, valid: 30, cheaper: 24, ties: 6, declined: 3, candCost: 3.22, prodCost: 4.84 }

export function LearningResults() {
  const costCut = Math.round((1 - GATE.cand.cost / GATE.prod.cost) * 100)
  const liveCut = Math.round((1 - SHADOW.candCost / SHADOW.prodCost) * 100)
  return (
    <>
      <div className="ln-stats">
        <div><b>{costCut}%</b><span>lower plan cost</span><p>The candidate planner against prod on the same {GATE.cases} recorded orders, scored in the simulator.</p></div>
        <div><b>{SHADOW.cheaper}<small>/{SHADOW.orders}</small></b><span>live orders it beat prod</span><p>It tied on {SHADOW.ties} more. On real orders its plans cost {liveCut}% less on average.</p></div>
        <div><b>0</b><span>labels written by hand</span><p>Every job grades itself from what the forklift actually did, so the training data builds up on its own.</p></div>
      </div>

      <div className="ln-compare">
        <h3>Offline gate: same {GATE.cases} orders, two planners</h3>
        <div className="ln-rows">
          {([['Orders completed', 'success', '%', 100], ['Invalid plans', 'invalid', '', 2], ['Average plan cost', 'cost', '', 10]] as const).map(([label, k, unit, max]) => (
            <div key={k} className="ln-row">
              <span className="ln-label">{label}</span>
              {(['prod', 'cand'] as const).map((who) => {
                const v = GATE[who][k]
                return (
                  <div key={who} className={`ln-bar ${who}`}>
                    <i style={{ width: `${Math.max(2, (v / max) * 100)}%` }} />
                    <em>{v}{unit}</em>
                  </div>
                )
              })}
            </div>
          ))}
        </div>
        <div className="ln-key">
          <span className="prod"><b>{GATE.prod.name}</b> in production · {GATE.prod.model}</span>
          <span className="cand"><b>{GATE.cand.name}</b> candidate · {GATE.cand.model}</span>
        </div>
        <p className="ln-fine">Plan cost: 100 per failed order, 50 per safety stop, plus metres driven, half a point per second waiting, 2 per replan. On successful orders it’s mostly distance, so lower cost means less driving for the same job.</p>
      </div>

      <div className="ln-why">
        <h3>Why this matters for a warehouse</h3>
        <div className="ln-why-grid">
          <div><b>One fix improves every truck.</b><p>All the forklifts share one planner. Promoting a better one changes how the whole fleet works, the same day, with no visit to any truck.</p></div>
          <div><b>Improvements are tested before they go live.</b><p>A candidate never moves a forklift until it has matched prod’s success rate, made no more invalid plans and cost less, on real recorded orders.</p></div>
          <div><b>Mistakes become test cases.</b><p>Every failed or slow order is saved with a snapshot of the warehouse, so future planners are tested against exactly what went wrong.</p></div>
          <div><b>Rollback is one click.</b><p>When a planner is promoted, the old one goes back to shadow with its history kept, so you can switch back if real results disagree.</p></div>
        </div>
      </div>

      <div className="ln-caveats">
        <b>What to keep in mind</b>
        <ul>
          <li>We started production on a deliberately weak prompt, which adds a check-in detour, so the loop had something to fix on stage. Part of the 63% comes from that.</li>
          <li>The samples are small: {GATE.cases} recorded orders for the gate and {SHADOW.orders} live shadow runs, most of them “move pallet 3 to dock B”.</li>
          <li>The {SHADOW.declined} orders the candidate turned down were reasonable: twice it said a swap needs a free slot to work in, and once the pallet was already at dock B.</li>
          <li>The simulator predicts real cost for v1-naive to within 0.7 points on average (about 18%). It’s much worse for the rule-based fallback, which only has 4 episodes.</li>
        </ul>
      </div>
    </>
  )
}

const STEPS: { t: string; d: string; tech: string; built: boolean; later?: string }[] = [
  { t: 'Record', d: 'Each finished job is saved: the order, a snapshot of the warehouse, the plan, which planner wrote it and how it went.', tech: 'Dispatch Lambda → DynamoDB FleetEpisodes + S3', built: true },
  { t: 'Calibrate', d: 'The simulator’s drive speed and pick/drop times are fitted to real episodes so its predictions match the floor.', tech: 'mlops/calibrate.py', built: true },
  { t: 'Build the eval set', d: 'Every real episode becomes a test case that any planner can be replayed against.', tech: 'S3 evalsets/', built: true, later: 'Synthetic hard cases from Bedrock' },
  { t: 'Candidates', d: 'A new prompt or a different Claude model is added as a candidate planner.', tech: 'FleetPlanners table', built: true, later: 'Small fine-tuned model' },
  { t: 'Offline gate', d: 'The candidate plans every test case and the simulator scores it against prod.', tech: 'SageMaker Processing job · mlops/evaluate.py', built: true },
  { t: 'Shadow', d: 'On every live order the candidate also writes a plan. It’s checked and simulated but never run.', tech: 'Dispatch Lambda · Bedrock', built: true },
  { t: 'Promote', d: 'When it keeps winning, one button makes it prod. The old planner stays as a shadow for rollback.', tech: 'Planner lab → API Gateway → FleetPlanners', built: true, later: 'Automatic promotion' },
]

export function MlopsPipeline() {
  const R = 150, C = 190
  const pts = STEPS.map((_, i) => {
    const a = -Math.PI / 2 + (i / STEPS.length) * Math.PI * 2
    return { x: C + R * Math.cos(a), y: C + R * Math.sin(a) }
  })
  return (
    <div className="mo">
      <div className="mo-ring">
        <svg viewBox="0 0 380 380" role="img" aria-label="The learning loop: record, calibrate, eval set, candidates, offline gate, shadow, promote, and back to record">
          <circle cx={C} cy={C} r={R} className="mo-track" />
          <circle r={7} className="mo-dot">
            <animateMotion dur="9s" repeatCount="indefinite" path={`M${C},${C - R} a${R},${R} 0 1,1 -0.01,0`} />
          </circle>
          {pts.map((p, i) => (
            <g key={i}>
              <circle cx={p.x} cy={p.y} r={20} className="mo-node" />
              <text x={p.x} y={p.y + 5} textAnchor="middle" className="mo-num">{i + 1}</text>
            </g>
          ))}
          <text x={C} y={C - 6} textAnchor="middle" className="mo-center">Every order</text>
          <text x={C} y={C + 16} textAnchor="middle" className="mo-center sub">makes the next one better</text>
        </svg>
      </div>
      <ol className="mo-steps">
        {STEPS.map((s, i) => (
          <li key={s.t}>
            <span className="mo-n">{i + 1}</span>
            <div>
              <b>{s.t}</b>
              <p>{s.d}</p>
              <span className="mo-tech">{s.tech}</span>
              {s.later && <span className="mo-later">Next: {s.later}</span>}
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}

const SM_NOW = [
  ['Package', 'The planner code, the simulator and the evaluation script are zipped and uploaded to S3.'],
  ['Launch', 'A SageMaker Processing job starts on a small ml.t3.medium instance using AWS’s prebuilt Python image, so there’s no Docker build.'],
  ['Replay', 'Inside the job, every recorded order becomes a test case. Each planner, prod and candidate, plans every case by calling Claude on Bedrock.'],
  ['Score', 'The simulator scores each plan from the exact warehouse snapshot of that order, then the gate compares the candidate with prod.'],
  ['Publish', 'Scores go to S3 and onto the planner’s record in DynamoDB, where the dashboard shows whether it passed.'],
]
const SM_NEXT = [
  ['Pick the best examples', 'Take the (order, warehouse snapshot, plan) records that scored best in real runs and in shadow.'],
  ['Fine-tune', 'A SageMaker Training job fine-tunes a small open model on them, giving a cheaper and faster planner.'],
  ['Same gate', 'The new model goes through the same Processing job and shadow stage as any other candidate before it can be promoted.'],
]

export function SagemakerRetraining() {
  return (
    <div className="sm">
      <h3>Where SageMaker fits</h3>
      <p className="sm-lede">Right now the candidates are new prompts and different Claude models, so there are no weights to train. SageMaker’s job is to judge them: it re-runs every recorded order through each planner and decides whether the candidate is good enough to go live. Retraining an actual model is the next step, and it plugs into the same gate.</p>
      <div className="sm-cols">
        <div className="sm-col">
          <span className="sm-tag now">Running today</span>
          <b>SageMaker Processing scores every candidate</b>
          <ol>{SM_NOW.map(([t, d]) => <li key={t}><strong>{t}.</strong> {d}</li>)}</ol>
          <p className="sm-rule">Pass rule: success rate at least prod’s, no more invalid plans, and lower average cost. v2-sonnet passed over 23 orders.</p>
        </div>
        <div className="sm-col">
          <span className="sm-tag next">Next</span>
          <b>SageMaker Training builds the next planner</b>
          <ol>{SM_NEXT.map(([t, d]) => <li key={t}><strong>{t}.</strong> {d}</li>)}</ol>
          <p className="sm-rule">The labels come for free: every job is already graded by what the forklift actually did.</p>
        </div>
      </div>
    </div>
  )
}
