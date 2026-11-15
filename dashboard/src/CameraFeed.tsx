// Live ceiling-camera view, streamed by the edge bridge over MQTT (warehouse/camera, ~4 fps JPEG).
import { useEffect, useState } from 'react'

export function CameraFeed({ camera, className = '' }: { camera?: { url: string; at: number }; className?: string }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 500); return () => clearInterval(t) }, [])
  const age = camera ? (now - camera.at) / 1000 : Infinity
  const live = age < 3
  return (
    <div className={`cam ${className}`}>
      <div className="cam-head">
        <span className={`cam-dot ${live ? 'on' : ''}`} />
        <span>Ceiling camera</span>
        <span className="cam-age">{live ? 'LIVE' : camera ? `last frame ${Math.round(age)}s ago` : 'no feed'}</span>
      </div>
      {camera
        ? <img src={camera.url} alt="Live view from the ceiling camera" className={live ? '' : 'stale'} />
        : <div className="cam-empty">The camera feed appears here when the edge laptop's bridge is running with the real camera.</div>}
    </div>
  )
}
