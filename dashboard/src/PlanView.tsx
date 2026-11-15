// Live behavior-tree view for the most recent AI order.
import type { Task, TreeNode } from './types'

const ICON: Record<TreeNode['type'], string> = {
  Sequence: '⇢', Fallback: '⑂', Retry: '↻', GoTo: '➜', Pick: '⤒', Drop: '⤓', WaitUntil: '⏸',
}

function label(n: TreeNode): string {
  const up = (s?: string) => (s ?? '').replace('_', ' ').toUpperCase()
  switch (n.type) {
    case 'GoTo': return `Drive to ${up(n.node)}`
    case 'Pick': return `Pick up ${up(n.pallet)}`
    case 'Drop': return `Drop at ${up(n.slot)}`
    case 'WaitUntil': return `Wait for ${up(n.clear)} (${n.timeout_s}s)`
    case 'Retry': return `Retry up to ${n.n}×${n.attempts ? ` · attempt ${n.attempts + 1}` : ''}`
    case 'Fallback': return 'Try in order'
    case 'Sequence': return 'Do in order'
  }
}

function Node({ n, depth }: { n: TreeNode; depth: number }) {
  const kids = n.children ?? (n.child ? [n.child] : [])
  const leaf = !kids.length
  return (
    <li className={`tn tn-${n.status ?? 'pending'} ${leaf ? 'leaf' : 'branch'}`}>
      <div className="tn-row">
        <span className="tn-icon">{ICON[n.type]}</span>
        <span className="tn-label">{label(n)}</span>
        {leaf && <span className="tn-state">{n.status === 'running' ? 'active' : n.status === 'success' ? '✓' : n.status === 'failure' ? '✕' : ''}</span>}
      </div>
      {n.error && <div className="tn-err">{n.error}</div>}
      {kids.length > 0 && <ul>{kids.map((c, i) => <Node key={i} n={c} depth={depth + 1} />)}</ul>}
    </li>
  )
}

export function PlanView({ task }: { task?: Task }) {
  if (!task) return <div className="plan-empty">Dispatch an order to see Claude's plan here.</div>
  return (
    <div className="plan">
      <div className="plan-head">
        <span className="plan-order">“{task.text ?? task.task_id}”</span>
        <span className={`tlabel tlabel-${task.status}`}>{task.status}</span>
      </div>
      <div className="plan-meta mono">
        {task.task_id}{task.assigned_to && ` · ${task.assigned_to.toUpperCase()}`}{task.planner_version && ` · planner ${task.planner_version}`}
      </div>
      {task.status === 'planning' && <div className="plan-thinking"><span className="spinner" />Claude is planning…</div>}
      {task.tree && <ul className="tree"><Node n={task.tree} depth={0} /></ul>}
      {task.status === 'failed' && task.error && <div className="t-err">{task.error}</div>}
    </div>
  )
}
