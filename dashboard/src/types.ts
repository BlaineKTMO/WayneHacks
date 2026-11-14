// Mirrors shared/fleet_shared/messages.py and docs/API.md. Keep in sync.
export type Pose = { x: number; y: number; theta: number; conf?: number; ts: number }
export type ForkliftState = 'IDLE' | 'DRIVING' | 'BLOCKED' | 'STOPPED' | 'FAULT'
export type Telemetry = { battery_v: number; motor_a: number; state: ForkliftState; node?: string | null; ts: number }
export type FleetEvent = { type: 'node_reached' | 'done' | 'blocked' | 'fault'; node?: string | null; ts: number }
export type PathNode = { id: string; x: number; y: number }
export type Path = { task_id: string; nodes: PathNode[]; action?: 'pick' | 'drop' | null }
export type TaskStatus = {
  state: string
  tree?: unknown
  assigned_to?: string | null
  planner_version?: string | null
  error?: string | null
}

export type GraphNode = { id: string; x: number; y: number; type: 'dock' | 'junction' | 'slot' }
export type Graph = {
  size: { width: number; height: number }
  nodes: GraphNode[]
  edges: { from: string; to: string; bidirectional?: boolean }[]
  pallets: { id: string; node: string }[]
}

export type Forklift = {
  id: string
  pose?: Pose
  state?: ForkliftState
  battery_v?: number
  node?: string | null
  lastEvent?: string
  lastSeen?: number
  path?: Path
  currentTask?: string | null
  speed?: number
}

export type LogEntry = { id: number; ts: number; level: 'info' | 'ok' | 'warn' | 'error'; unit?: string; text: string }

export type Task = {
  task_id: string
  text?: string
  status: string
  assigned_to?: string | null
  error?: string | null
  created_at?: number
}
