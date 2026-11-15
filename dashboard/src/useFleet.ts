// All live fleet state: initial load from the HTTP API, then MQTT updates.
import mqtt, { type MqttClient } from 'mqtt'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, signedMqttUrl } from './aws'
import type { Config } from './config'
import type { FleetEvent, Forklift, Graph, LogEntry, Path, Pose, Task, TaskStatus, Telemetry } from './types'

export type Conn = 'connecting' | 'live' | 'offline'
const TRAIL_LEN = 40
const LOG_LEN = 200

export function useFleet(cfg: Config) {
  const [graph, setGraph] = useState<Graph>()
  const [forklifts, setForklifts] = useState<Record<string, Forklift>>({})
  const [trails, setTrails] = useState<Record<string, [number, number][]>>({})
  const [tasks, setTasks] = useState<Task[]>([])
  const [pallets, setPallets] = useState<Record<string, string>>({})
  const [log, setLog] = useState<LogEntry[]>([])
  const [stops, setStops] = useState(0)
  const [locks, setLocks] = useState<Record<string, string>>({})
  const [conn, setConn] = useState<Conn>('connecting')
  const [error, setError] = useState<string>()
  const client = useRef<MqttClient | null>(null)
  const logId = useRef(0)
  const lastPose = useRef<Record<string, Pose & { at: number }>>({})
  const lastState = useRef<Record<string, string | undefined>>({})
  const refreshPallets = useRef<() => void>(undefined)

  const addLog = (level: LogEntry['level'], text: string, unit?: string) =>
    setLog((l) => [{ id: ++logId.current, ts: Date.now(), level, unit, text }, ...l].slice(0, LOG_LEN))

  const patchForklift = (id: string, patch: Partial<Forklift>) =>
    setForklifts((f) => ({ ...f, [id]: { ...f[id], id, ...patch } }))

  const upsertTask = (t: Task) =>
    setTasks((ts) => {
      const i = ts.findIndex((x) => x.task_id === t.task_id)
      if (i < 0) return [{ created_at: Date.now(), ...t }, ...ts].slice(0, 50)
      const next = [...ts]
      next[i] = { ...next[i], ...Object.fromEntries(Object.entries(t).filter(([, v]) => v != null)) }
      return next
    })

  const onMessage = useCallback((topic: string, raw: Uint8Array) => {
    let msg: any
    try {
      msg = JSON.parse(new TextDecoder().decode(raw))
    } catch {
      return
    }
    const p = topic.split('/')
    const now = Date.now()
    if (p[0] === 'fleet') {
      const id = p[1]
      switch (p[2]) {
        case 'pose': {
          const pose = msg as Pose
          const prev = lastPose.current[id]
          let speed: number | undefined
          if (prev && now - prev.at > 0) {
            const inst = Math.hypot(pose.x - prev.x, pose.y - prev.y) / ((now - prev.at) / 1000)
            speed = inst
          }
          lastPose.current[id] = { ...pose, at: now }
          setTrails((t) => {
            const tr = t[id] ?? []
            const last = tr[tr.length - 1]
            if (last && Math.hypot(last[0] - pose.x, last[1] - pose.y) < 0.01) return t
            return { ...t, [id]: [...tr, [pose.x, pose.y] as [number, number]].slice(-TRAIL_LEN) }
          })
          return setForklifts((f) => {
            const old = f[id]?.speed ?? 0
            const s = speed === undefined ? old : old * 0.7 + speed * 0.3
            return { ...f, [id]: { ...f[id], id, pose, lastSeen: now, speed: s } }
          })
        }
        case 'telemetry': {
          const t = msg as Telemetry
          if (lastState.current[id] !== t.state) {
            if (lastState.current[id] !== undefined)
              addLog(t.state === 'STOPPED' || t.state === 'FAULT' ? 'error' : t.state === 'BLOCKED' ? 'warn' : 'info',
                `State ${lastState.current[id]} → ${t.state}`, id)
            lastState.current[id] = t.state
          }
          return patchForklift(id, { state: t.state, battery_v: t.battery_v, node: t.node, lastSeen: now })
        }
        case 'event': {
          const e = msg as FleetEvent
          if (e.type === 'node_reached') addLog('info', `Reached ${e.node}`, id)
          if (e.type === 'done') addLog('ok', `Task complete at ${e.node}`, id)
          if (e.type === 'blocked') addLog('warn', `Blocked at ${e.node}, waiting for lane`, id)
          if (e.type === 'fault') addLog('error', `Fault at ${e.node ?? 'unknown'}`, id)
          return patchForklift(id, {
            lastEvent: e.type,
            ...(e.node ? { node: e.node } : {}),
            ...(e.type === 'done' ? { path: undefined, currentTask: null } : {}),
          })
        }
        case 'path': {
          const path = msg as Path
          addLog('info', `Route assigned: ${path.nodes.map((n) => n.id).join(' → ')}`, id)
          return patchForklift(id, { path, currentTask: path.task_id })
        }
        case 'stop': {
          setStops((s) => s + 1)
          addLog('error', `SAFETY STOP: ${msg.reason}`, id)
          return patchForklift(id, { path: undefined })
        }
      }
    }
    if (topic === 'warehouse/locks') {
      setLocks((msg as { locks: Record<string, string> }).locks ?? {})
      return
    }
    if (p[0] === 'tasks' && p[2] === 'status') {
      const s = msg as TaskStatus
      upsertTask({ task_id: p[1], status: s.state, assigned_to: s.assigned_to, error: s.error,
                   text: s.text ?? undefined, tree: s.tree ?? undefined, planner_version: s.planner_version })
      if (s.state === 'failed') addLog('error', `Task ${p[1]} failed: ${s.error}`, s.assigned_to ?? undefined)
      if (s.state === 'planning') addLog('info', `Planner ${s.planner_version ?? ''} is writing a plan…`)
      if (s.state === 'done') addLog('ok', `Task ${p[1]} complete`, s.assigned_to ?? undefined)
      refreshPallets.current?.()
    }
  }, [])

  useEffect(() => {
    let stopped = false
    let retry: ReturnType<typeof setTimeout>

    async function load() {
      const [g, fl, ts, pl] = await Promise.all([
        api<Graph>(cfg, '/graph'),
        api<any[]>(cfg, '/forklifts'),
        api<Task[]>(cfg, '/tasks'),
        api<{ pallet_id: string; node: string }[]>(cfg, '/pallets'),
      ])
      setGraph(g)
      setForklifts(Object.fromEntries(fl.map((f) => [f.forklift_id, {
        id: f.forklift_id, pose: f.pose, state: f.state, battery_v: f.battery_v,
        node: f.node, lastEvent: f.last_event, currentTask: f.current_task,
      }])))
      setTasks(ts)
      setPallets(Object.fromEntries(pl.map((x) => [x.pallet_id, x.node])))
    }

    async function connect() {
      if (stopped) return
      setConn('connecting')
      try {
        await api(cfg, '/iot/attach', 'POST')
        const c = mqtt.connect(await signedMqttUrl(cfg), {
          clientId: `dash-${Math.random().toString(16).slice(2, 10)}`,
          protocolVersion: 4,
          reconnectPeriod: 0, // we reconnect ourselves with a freshly signed URL
          connectTimeout: 10_000,
        })
        client.current = c
        c.on('connect', () => {
          setConn('live')
          setError(undefined)
          addLog('ok', 'Connected to fleet network')
          c.subscribe(['fleet/+/pose', 'fleet/+/telemetry', 'fleet/+/event', 'fleet/+/path', 'fleet/+/stop', 'tasks/+/status', 'warehouse/locks'])
        })
        c.on('message', onMessage)
        c.on('error', (e) => setError(`MQTT: ${e.message}`))
        c.on('close', () => {
          setConn('offline')
          c.removeAllListeners()
          if (!stopped) retry = setTimeout(connect, 3000)
        })
      } catch (e) {
        setConn('offline')
        setError(String(e))
        retry = setTimeout(connect, 5000)
      }
    }

    let palletTimer: ReturnType<typeof setTimeout>
    refreshPallets.current = () => {
      clearTimeout(palletTimer)
      palletTimer = setTimeout(() => {
        api<{ pallet_id: string; node: string }[]>(cfg, '/pallets')
          .then((pl) => setPallets(Object.fromEntries(pl.map((x) => [x.pallet_id, x.node]))))
          .catch(() => {})
      }, 400)
    }
    load().catch((e) => setError(String(e)))
    connect()
    return () => {
      stopped = true
      clearTimeout(retry)
      client.current?.end(true)
    }
  }, [cfg, onMessage])

  const publish = (topic: string, payload: object) => client.current?.publish(topic, JSON.stringify(payload), { qos: 1 })

  return {
    graph, forklifts, trails, tasks, pallets, locks, log, stops, conn, error,
    goto: (forkliftId: string, node: string) => {
      addLog('info', `Dispatch to ${node}`, forkliftId)
      publish(`fleet/${forkliftId}/goto`, { node })
    },
    submitTask: (text: string) => {
      addLog('info', `Order: “${text}”`)
      publish('tasks/new', { text })
    },
    stop: (forkliftId: string) => {
      addLog('warn', 'Operator stop requested', forkliftId)
      return api(cfg, `/forklifts/${forkliftId}/stop`, 'POST')
    },
  }
}
