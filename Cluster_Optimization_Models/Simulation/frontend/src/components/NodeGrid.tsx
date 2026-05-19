import { motion } from 'framer-motion'
import type { NodeInfo } from '../types'

interface Props {
  nodes: NodeInfo[]
  newJobNodeIds: Set<number>
  useEffective: boolean
  effGreenThreshold?: number  // % below which eff util is green (default 95)
  capGreenThreshold?: number  // % below which cap util is green (default 85)
}

function memBarColor(pct: number, isEff: boolean, effThresh: number, capThresh: number): string {
  const greenThresh  = isEff ? effThresh : capThresh
  const yellowThresh = isEff ? Math.min(100, effThresh + 5) : Math.min(100, capThresh + 10)
  if (pct > yellowThresh) return '#ef4444'
  if (pct > greenThresh)  return '#f59e0b'
  return '#22c55e'
}

function fmtMB(mb: number): string {
  return mb >= 1024 ? `${(mb / 1024).toFixed(0)} GB` : `${mb.toFixed(0)} MB`
}

export function NodeGrid({
  nodes,
  newJobNodeIds,
  useEffective,
  effGreenThreshold = 95,
  capGreenThreshold = 85,
}: Props) {
  return (
    <div className="h-full overflow-y-auto queue-scroll p-2">
      <div className="grid grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-2 auto-rows-min">
        {nodes.map((node) => {
          const utilPct  = useEffective ? node.eff_pct : node.mem_pct
          const barColor = memBarColor(utilPct, useEffective, effGreenThreshold, capGreenThreshold)
          const hasNew   = newJobNodeIds.has(node.node_id)
          const isActive = node.running_jobs.length > 0
          const isHot    = node.viols_count > 3

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
                {isHot && <span className="text-[9px] text-red-500 font-bold">HOT</span>}
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

              {/* Specs — 2-col grid for value alignment */}
              <div className="text-[10px] grid grid-cols-2 gap-x-2">
                <span className="text-slate-600">Cap. CPU</span>
                <span className="text-slate-500 tabular-nums text-right">{node.cpu_cores.toFixed(0)} cores</span>
                <span className="text-slate-600">Cap. RAM</span>
                <span className="text-slate-500 tabular-nums text-right">{fmtMB(node.capacity_mb)}</span>
                <span className="text-slate-600">Eff. RAM</span>
                <span className="text-slate-500 tabular-nums text-right">{fmtMB(node.m_cap)}</span>
                <span className="text-slate-600">Viols</span>
                <span className={`tabular-nums text-right ${node.viols_total > 0 ? 'text-amber-500' : 'text-slate-700'}`}>
                  {node.viols_total}
                </span>
                <span className="text-slate-600">Ovrflw</span>
                <span className={`tabular-nums text-right ${node.ovrflw_count > 0 ? 'text-red-500' : 'text-slate-700'}`}>
                  {node.ovrflw_count}
                </span>
              </div>

              {/* Running jobs count — highlighted when active */}
              <div className="flex items-baseline gap-1">
                <span
                  className={`text-lg font-black tabular-nums leading-none ${isActive ? 'text-emerald-400' : 'text-slate-700'}`}
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
