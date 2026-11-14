// All live fleet state: initial load from the HTTP API, then MQTT updates.
import mqtt, { type MqttClient } from 'mqtt'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, signedMqttUrl } from './aws'
import type { Config } from './config'
import type { FleetEvent, Forklift, Graph, Path, Pose, Task, TaskStatus, Telemetry } from './types'

export type Conn = 'connecting' | 'live' | 'offline'

export function useFleet(cfg: Config) {
  const [graph, setGraph] = useState<Graph>()
  const [forklifts, setForklifts] = useState<Record<string, Forklift>>({})
  const [tasks, setTasks] = useState<Task[]>([])
  const [pallets, setPallets] = useState<Record<string, string>>({})
  const [conn, setConn] = useState<Conn>('connecting')
  const [error, setError] = useState<string>()
  const client = useRef<MqttClient | null>(null)

  const patchForklift = (id: string, patch: Partial<Forklift>) =>
    setForklifts((f) => ({ ...f, [id]: { ...f[id], id, ...patch } }))

  const upsertTask = (t: Task) =>
    setTasks((ts) => {
      const i = ts.findIndex((x) => x.task_id === t.task_id)
      if (i < 0) return [t, ...ts].slice(0, 50)
      const next = [...ts]
      next[i] = { ...next[i], ...t }
      return next
    })

  const onMessage = useCallback((topic: string, raw: Uint8Array) => {
    let msg: unknown
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
        case 'pose':
          return patchForklift(id, { pose: msg as Pose, lastSeen: now })
        case 'telemetry': {
          const t = msg as Telemetry
          return patchForklift(id, { state: t.state, battery_v: t.battery_v, node: t.node, lastSeen: now })
        }
        case 'event': {
          const e = msg as FleetEvent
          return patchForklift(id, {
            lastEvent: e.type,
            ...(e.node ? { node: e.node } : {}),
            ...(e.type === 'done' ? { path: undefined, currentTask: null } : {}),
          })
        }
        case 'path': {
          const path = msg as Path
          return patchForklift(id, { path, currentTask: path.task_id })
        }
      }
    }
    if (p[0] === 'tasks' && p[2] === 'status') {
      const s = msg as TaskStatus
      upsertTask({ task_id: p[1], status: s.state, assigned_to: s.assigned_to, error: s.error })
    }
  }, [])

  // Initial state + MQTT connection (reconnects with fresh signed URL when it drops).
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
          c.subscribe(['fleet/+/pose', 'fleet/+/telemetry', 'fleet/+/event', 'fleet/+/path', 'tasks/+/status'])
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
    graph, forklifts, tasks, pallets, conn, error,
    goto: (forkliftId: string, node: string) => publish(`fleet/${forkliftId}/goto`, { node }),
    submitTask: (text: string) => publish('tasks/new', { text }),
    stop: (forkliftId: string) => api(cfg, `/forklifts/${forkliftId}/stop`, 'POST'),
  }
}
