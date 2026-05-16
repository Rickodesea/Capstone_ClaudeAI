import { motion } from 'framer-motion'
import type { NodeInfo } from '../types'

interface Props {
  nodes: NodeInfo[]
  newJobNodeIds: Set<number>
  useEffective: boolean
}

function memBarColor(pct: number): string {
  if (pct > 85) return '#ef4444'
  if (pct > 60) return '#f59e0b'
  return '#22c55e'
}

function fmtMB(mb: number): string {
  return mb >= 1024 ? `${(mb / 1024).toFixed(0)} GB` : `${mb.toFixed(0)} MB`
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-baseline">
      <span className="text-slate-600">{label}</span>
      <span className="text-slate-500 tabular-nums">{value}</span>
    </div>
  )
}

export function NodeGrid({ nodes, newJobNodeIds, useEffective }: Props) {
  return (
    <div className="h-full overflow-y-auto queue-scroll p-2">
      <div className="grid grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-2 auto-rows-min">
        {nodes.map((node) => {
          const utilPct  = useEffective ? node.eff_pct : node.mem_pct
          const barColor = memBarColor(utilPct)
          const hasNew   = newJobNodeIds.has(node.node_id)
          const isActive = node.running_jobs.length > 0
          const isHot    = node.violation_rate > 0.3

          return (
            <motion.div
              key={node.node_id}
              className={`bg-slate-900 border rounded-md p-2 flex flex-col gap-1.5
                ${isHot ? 'border-red-700/60' : 'border-slate-800'}
                ${hasNew ? 'ring-1 ring-emerald-500/50' : ''}`}
              animate={hasNew ? { scale: [1, 1.016, 1] } : {}}
              transition={{ duration: 0.3 }}
            >
              {/* Header */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <div
                    className={`w-1.5 h-1.5 rounded-full transition-colors duration-500
                      ${isHot    ? 'bg-red-500 animate-pulse-fast' :
                        isActive ? 'bg-emerald-500' :
                                   'bg-slate-700'}`}
                  />
                  <span className="text-[11px] font-bold text-slate-300">Node {node.node_id}</span>
                </div>
                {isHot && <span className="text-[9px] text-red-500 font-bold">VIOL</span>}
              </div>

              {/* Memory Utilization bar */}
              <div>
                <div className="flex justify-between items-baseline mb-0.5">
                  <span className="text-[10px] text-slate-500">Memory Utilization</span>
                  <span className="text-xs font-black tabular-nums" style={{ color: barColor }}>
                    {utilPct.toFixed(1)}%
                  </span>
                </div>
                <div className="h-1.5 bg-slate-800 rounded overflow-hidden">
                  <motion.div
                    className="h-full rounded"
                    style={{ backgroundColor: barColor }}
                    animate={{ width: `${Math.min(100, utilPct)}%` }}
                    transition={{ duration: 0.4, ease: 'easeOut' }}
                  />
                </div>
              </div>

              {/* Specs — 2-column aligned */}
              <div className="text-[10px] space-y-0.5">
                <Row label="Used RAM"  value={fmtMB(node.used_mb)} />
                <Row label="Cap. RAM"  value={fmtMB(node.capacity_mb)} />
                <Row label="Cap. CPU"  value={`${node.cpu_cores.toFixed(0)} cores`} />
                <Row label="Eff. RAM"  value={fmtMB(node.m_cap)} />
                {node.violation_rate > 0 && (
                  <div className="flex justify-between items-baseline">
                    <span className="text-red-700">Viol rate</span>
                    <span className="text-red-600 tabular-nums">
                      {(node.violation_rate * 100).toFixed(0)}%
                    </span>
                  </div>
                )}
              </div>

              {/* Running jobs count */}
              <div className="flex items-baseline gap-1">
                <span
                  className="text-base font-black tabular-nums leading-none"
                  style={{ color: isActive ? '#94a3b8' : '#334155' }}
                >
                  {node.running_jobs.length}
                </span>
                <span className="text-[10px] text-slate-600 leading-none">
                  {node.running_jobs.length === 1 ? 'job' : 'jobs'}
                </span>
              </div>
            </motion.div>
          )
        })}
      </div>
    </div>
  )
}
