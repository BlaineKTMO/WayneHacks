// Planner lab: prod vs shadow candidates, scored by the simulator on every live order.
import { useEffect, useState } from 'react'
import { api } from './aws'
import type { Config } from './config'

type Planner = {
  version: string
  stage: 'prod' | 'shadow' | 'retired'
  model_id?: string
  description?: string
  shadow_runs: number
  shadow_wins: number
  avg_sim_cost: number | null
}
type Row = {
  task_id: string
  text?: string
  prod: { version?: string; sim_cost?: number }
  candidates: { planner_version: string; sim_cost: number; valid: boolean; win: boolean; error?: string }[]
}

const model = (id?: string) => 'OE · ' + (id ?? '').replace(/^us\.anthropic\./, '').replace(/^claude-/, '').replace(/-\d{8}-v\d:\d$/, '')

export function PlannerLab({ cfg, refreshKey }: { cfg: Config; refreshKey: number }) {
  const [planners, setPlanners] = useState<Planner[]>([])
  const [rows, setRows] = useState<Row[]>([])
  const [busy, setBusy] = useState<string>()
  const [err, setErr] = useState<string>()

  const load = () =>
    Promise.all([api<Planner[]>(cfg, '/planners'), api<Row[]>(cfg, '/planners/compare?limit=6')])
      .then(([p, r]) => { setPlanners(p); setRows(r) })
      .catch((e) => setErr(String(e)))

  useEffect(() => { load() }, [refreshKey])

  const promote = async (v: string) => {
    setBusy(v)
    setErr(undefined)
    try {
      await api(cfg, `/planners/${v}/promote`, 'POST')
      await load()
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(undefined)
    }
  }

  const prod = planners.find((p) => p.stage === 'prod')
  return (
    <div className="lab">
      <div className="lab-planners">
        {planners.filter((p) => p.stage !== 'retired').map((p) => {
          const rate = p.shadow_runs ? Math.round((100 * p.shadow_wins) / p.shadow_runs) : null
          return (
            <div key={p.version} className={`pl pl-${p.stage}`}>
              <div className="pl-top">
                <span className="pl-v mono">{p.version}</span>
                <span className={`pl-stage pl-stage-${p.stage}`}>{p.stage === 'prod' ? 'PRODUCTION' : 'SHADOW'}</span>
              </div>
              <div className="pl-model mono">{model(p.model_id)}</div>
              {p.description && <div className="pl-desc">{p.description}</div>}
              {p.stage === 'shadow' && (
                <div className="pl-stats">
                  <div><label>Head-to-head</label><b className="mono">{p.shadow_wins}/{p.shadow_runs}</b>{rate != null && <span className="muted"> ({rate}%)</span>}</div>
                  <div><label>Avg sim cost</label><b className="mono">{p.avg_sim_cost ?? '—'}</b></div>
                  <button className="btn-promote" disabled={!!busy || !p.shadow_runs} onClick={() => promote(p.version)}
                          title={p.shadow_runs ? `Replace ${prod?.version ?? 'prod'} with ${p.version}` : 'Needs at least one shadow run'}>
                    {busy === p.version ? 'Promoting…' : '▲ Promote'}
                  </button>
                </div>
              )}
            </div>
          )
        })}
        {!planners.length && <div className="muted small">No planners registered. Run cloud/scripts/seed.py.</div>}
      </div>

      <div className="lab-head">Recent orders · simulated cost (lower is better)</div>
      {rows.length === 0 && <div className="muted small">Shadow results appear here after each order.</div>}
      <ul className="lab-rows">
        {rows.map((r) => (
          <li key={r.task_id}>
            <div className="lr-text">{r.text ?? r.task_id}</div>
            <div className="lr-scores mono">
              <span className="lr-prod" title={r.prod.version}>{r.prod.version}: <b>{r.prod.sim_cost ?? '—'}</b></span>
              {r.candidates.map((c) => (
                <span key={c.planner_version} className={`lr-cand ${c.win ? 'win' : 'lose'}`} title={c.error ?? ''}>
                  {c.planner_version}: <b>{c.valid ? c.sim_cost : 'invalid'}</b> {c.win ? '✓' : ''}
                </span>
              ))}
              {r.candidates.length === 0 && <span className="muted">no shadow run</span>}
            </div>
          </li>
        ))}
      </ul>
      {err && <div className="t-err">{err}</div>}
    </div>
  )
}
