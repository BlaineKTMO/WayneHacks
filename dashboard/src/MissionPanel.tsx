// Missions: choose which aisles empty into which truck docks, then the whole fleet does it.
import { useEffect, useState } from 'react'
import { api } from './aws'
import type { Config } from './config'
import { unitColour } from './WarehouseMap'

export type MissionSummary = {
  mission_id: string; state: 'running' | 'done' | 'cancelled'; assignments: Record<string, string[]>
  total: number; done: number; running: number; failed: number
  per_dock: Record<string, { planned: number; shipped: number }>; active: Record<string, string>
}
type Info = { map_has_aisles: boolean; aisles: Record<string, string[]>; docks: string[]; mission: MissionSummary | null }

const dockLabel = (d: string) => d.replace('dock_', 'Dock ')

export function MissionPanel({ cfg, live, pallets }: { cfg: Config; live?: MissionSummary; pallets: Record<string, string> }) {
  const [info, setInfo] = useState<Info>()
  const [plan, setPlan] = useState<Record<string, string[]>>({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string>()

  useEffect(() => {
    api<Info>(cfg, '/missions/current').then((i) => {
      setInfo(i)
      const docks = i.docks
      // default: spread aisles over docks, the last aisle gets the remaining docks
      const aisles = Object.keys(i.aisles)
      setPlan(Object.fromEntries(aisles.map((a, k) => [a, k < aisles.length - 1 ? [docks[k % docks.length]] : docks.slice(k)])))
    }).catch((e) => setErr(String(e)))
  }, [cfg])

  if (!info) return <div className="ms-empty">{err ?? 'Loading…'}</div>
  if (!info.map_has_aisles) return <div className="ms-empty">Missions need the aisle map: <code>uv run python cloud/scripts/use_map.py mission</code></div>

  const m = live ?? info.mission
  const running = m?.state === 'running'
  const inAisle = (a: string) => info.aisles[a].filter((s) => Object.values(pallets).includes(s)).length
  const toggle = (a: string, d: string) =>
    setPlan((p) => ({ ...p, [a]: p[a]?.includes(d) ? p[a].filter((x) => x !== d) : [...(p[a] ?? []), d].sort() }))
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(undefined)
    try { await fn() } catch (e) { setErr(String(e).replace('Error: ', '')) } finally { setBusy(false) }
  }
  const chosen = Object.fromEntries(Object.entries(plan).filter(([a, ds]) => ds.length && inAisle(a)))

  return (
    <div className="ms">
      <div className="ms-grid">
        {Object.keys(info.aisles).map((a) => (
          <div key={a} className="ms-row">
            <div className="ms-aisle"><b>Aisle {a}</b><span>{inAisle(a)} pallet{inAisle(a) === 1 ? '' : 's'}</span></div>
            <div className="ms-docks">
              {info.docks.map((d) => (
                <button key={d} disabled={running || busy} className={`ms-dock ${plan[a]?.includes(d) ? 'on' : ''}`}
                        onClick={() => toggle(a, d)}>{dockLabel(d)}</button>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="ms-actions">
        {running
          ? <button className="ms-btn stop" disabled={busy} onClick={() => act(() => api(cfg, '/missions/current/cancel', 'POST'))}>Cancel mission</button>
          : <button className="ms-btn go" disabled={busy || !Object.keys(chosen).length}
                    onClick={() => act(() => api(cfg, '/missions', 'POST', { assignments: chosen }))}>▶ Start mission</button>}
        <button className="ms-btn" disabled={busy || running} onClick={() => act(() => api(cfg, '/missions/restock', 'POST'))}>Restock aisles</button>
      </div>
      {err && <div className="t-err">{err}</div>}
      {m && (
        <div className="ms-progress">
          <div className="ms-bar"><div style={{ width: `${m.total ? (100 * m.done) / m.total : 0}%` }} /></div>
          <div className="ms-stat"><b>{m.done}/{m.total}</b> pallets shipped · {m.running} moving · <span className={`ms-state ${m.state}`}>{m.state}</span></div>
          <div className="ms-perdock">
            {Object.entries(m.per_dock).sort().map(([d, c]) => (
              <div key={d}><span>{dockLabel(d)}</span><b>{c.shipped}/{c.planned}</b></div>
            ))}
          </div>
          {Object.entries(m.active).map(([fid, what]) => (
            <div key={fid} className="ms-active"><i style={{ background: unitColour(fid) }} />{fid.toUpperCase()} · {what.replace(/_/g, ' ')}</div>
          ))}
        </div>
      )}
    </div>
  )
}
