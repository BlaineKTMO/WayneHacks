// Devices panel: is each piece of hardware online, and if not, why. Fed by warehouse/health/<source>
// (1 Hz, retained, with an MQTT last will so a crashed/disconnected edge shows offline on its own).
import { useEffect, useState } from 'react'
import type { DeviceHealth } from './useFleet'

type Tone = 'ok' | 'warn' | 'bad' | 'sim'
const STALE_S = 5

function Row({ tone, name, detail }: { tone: Tone; name: string; detail: string }) {
  return (
    <div className={`dev dev-${tone}`}>
      <span className="dev-dot" />
      <span className="dev-name">{name}</span>
      <span className="dev-detail">{detail}</span>
    </div>
  )
}

export function Devices({ health }: { health: Record<string, DeviceHealth> }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [])
  const sources = Object.values(health).sort((a, b) => a.source.localeCompare(b.source))
  if (!sources.length) return <div className="dev-empty">No devices reporting. Start the edge bridge or turn on the simulated fleet.</div>

  return (
    <div className="devs">
      {sources.map((h) => {
        const age = (now - h.receivedAt) / 1000
        const fresh = h.online && age < STALE_S
        const isSim = h.sim || h.kind === 'sim'
        const label = h.source === 'sim-fleet' ? 'Simulated fleet (AWS)' : `Edge laptop ${h.source}`
        const rows = [
          <Row key="edge" tone={!h.online ? 'bad' : !fresh ? 'warn' : isSim ? 'sim' : 'ok'} name={label}
               detail={!h.online ? `offline: ${h.reason ?? 'disconnected'}` : !fresh ? `no heartbeat for ${Math.round(age)}s`
                 : `connected · map ${h.map ?? '?'} · up ${Math.floor((h.uptime_s ?? 0) / 60)}m`} />,
        ]
        if (h.online && h.camera) {
          const c = h.camera
          if (c.mode === 'simulated') rows.push(<Row key="cam" tone="sim" name="Ceiling camera" detail="simulated (no camera)" />)
          else {
            const camOk = (c.frame_age_s ?? 99) < 1 && (c.fps ?? 0) > 5
            const tagsOk = c.map_locked && (c.corner_tags_seen ?? 0) >= 3
            rows.push(<Row key="cam" tone={!camOk ? 'bad' : !tagsOk ? 'warn' : 'ok'} name="Ceiling camera"
              detail={!camOk ? `no frames (${c.frame_age_s ?? '—'}s)` :
                `${c.fps} fps · corner tags ${c.corner_tags_seen}/${c.corner_tags_total}${c.map_locked ? '' : ' · map NOT locked'}`} />)
          }
        }
        for (const [fid, f] of Object.entries(h.online ? h.forklifts ?? {} : {})) {
          if (f.link === 'simulated') { rows.push(<Row key={fid} tone="sim" name={`${fid.toUpperCase()} controller`} detail={`simulated · ${f.state ?? ''}`} />); continue }
          const linkOk = f.link === 'ok'
          const tagOk = f.tag_age_s != null && f.tag_age_s < 1
          rows.push(<Row key={fid} tone={!linkOk ? 'bad' : !tagOk ? 'warn' : 'ok'} name={`${fid.toUpperCase()} Free-WiLi`}
            detail={!linkOk ? `${f.link}${f.telemetry_age_s != null ? ` (${f.telemetry_age_s}s)` : ''} · ${f.port ?? ''}` :
              `${f.fw_state ?? '?'} · ${f.battery_v != null ? f.battery_v.toFixed(2) + ' V' : '—'} · ${tagOk ? 'tag seen' : `tag not seen ${f.tag_age_s ?? '—'}s`}`} />)
        }
        return <div key={h.source} className="dev-group">{rows}</div>
      })}
    </div>
  )
}
