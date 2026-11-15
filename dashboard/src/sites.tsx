// Sites (warehouses): pick one, remember it, allow ?site=wh1 in the URL to pin a screen.
import { useState } from 'react'
import type { Config, SiteInfo } from './config'

const KEY = 'floe.site'

export function siteList(cfg: Config): SiteInfo[] {
  return cfg.sites?.length ? cfg.sites : [{ id: 'demo', name: 'Demo site (simulated)', apiUrl: cfg.apiUrl, simulated: true }]
}

export function useSite(cfg: Config, fallback = 'demo'): [Config, SiteInfo[], (id: string) => void] {
  const sites = siteList(cfg)
  const initial = (() => {
    const fromUrl = new URLSearchParams(window.location.search).get('site')
    let saved: string | null = null
    try { saved = localStorage.getItem(KEY) } catch { /* private mode */ }
    return [fromUrl, saved, fallback].find((s) => s && sites.some((x) => x.id === s)) ?? sites[0].id
  })()
  const [id, setId] = useState(initial)
  const site = sites.find((s) => s.id === id) ?? sites[0]
  const choose = (next: string) => {
    try { localStorage.setItem(KEY, next) } catch { /* ignore */ }
    const u = new URL(window.location.href)
    u.searchParams.set('site', next)
    window.history.replaceState(null, '', u)
    setId(next)
  }
  return [{ ...cfg, apiUrl: site.apiUrl, site: site.id }, sites, choose]
}

export function SiteSwitcher({ sites, current, onChange, className = '' }:
  { sites: SiteInfo[]; current?: string; onChange: (id: string) => void; className?: string }) {
  if (sites.length < 2) return null
  return (
    <label className={`site-switch ${className}`}>
      <span className="site-switch-label">SITE</span>
      <select value={current} onChange={(e) => onChange(e.target.value)} aria-label="Warehouse site">
        {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select>
    </label>
  )
}
