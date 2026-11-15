// "Simulated fleet" switch: starts/stops the Fargate-hosted fake forklifts (POST /sim).
import { useEffect, useState } from 'react'
import { api } from './aws'
import type { Config } from './config'

type Sim = { on: boolean; running: number; pending: number }

export function SimSwitch({ cfg, className = '' }: { cfg: Config; className?: string }) {
  const [sim, setSim] = useState<Sim>()
  const [busy, setBusy] = useState(false)
  const load = () => api<Sim>(cfg, '/sim').then(setSim).catch(() => {})
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t) }, [])
  const toggle = async () => {
    if (!sim || busy) return
    setBusy(true)
    try { setSim(await api<Sim>(cfg, '/sim', 'POST', { on: !sim.on })) } finally { setBusy(false) }
  }
  const label = !sim ? '…' : sim.on ? (sim.running ? 'On' : 'Starting…') : 'Off'
  return (
    <button className={`sim-switch ${sim?.on ? 'on' : ''} ${className}`} onClick={toggle} disabled={!sim || busy}
            title="Simulated forklifts hosted in AWS. Turn off when real hardware is driving.">
      <span className="sim-knob" /> Simulated fleet: <b>{label}</b>
    </button>
  )
}
