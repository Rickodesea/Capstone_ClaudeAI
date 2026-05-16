import { motion } from 'framer-motion'
import type { PlanAheadResult } from '../types'
import { TENANT_COLORS, TENANT_NAMES } from '../types'
import { X, CalendarCheck } from 'lucide-react'

interface Props {
  result: PlanAheadResult
  numNodes: number
  onClose: () => void
}

function intensityColor(v: number): string {
  if (v === 0) return '#1e293b'
  const r = Math.round(30  + v * 225)
  const g = Math.round(150 - v * 100)
  const b = Math.round(60  - v * 40)
  return `rgb(${r},${g},${b})`
}

export function PlanAheadOverlay({ result, numNodes, onClose }: Props) {
  const tenants = Object.keys(result.tenant_node_access).map(Number).sort()
  const nodes   = Array.from({ length: numNodes }, (_, i) => i)

  const cellMap: Record<string, { intensity: number; authorized: boolean }> = {}
  for (const cell of result.heatmap) {
    cellMap[`${cell.tenant_id}_${cell.node_id}`] = {
      intensity: cell.intensity,
      authorized: cell.authorized,
    }
  }

  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 backdrop-blur-sm"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onClick={onClose}
    >
      <motion.div
        className="bg-slate-900 border border-slate-700 rounded-xl shadow-2xl p-6 w-full max-w-2xl mx-4"
        initial={{ y: -40, opacity: 0, scale: 0.95 }}
        animate={{ y: 0, opacity: 1, scale: 1 }}
        exit={{ y: -20, opacity: 0, scale: 0.97 }}
        transition={{ type: 'spring', damping: 22, stiffness: 250 }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-2">
            <CalendarCheck size={18} className="text-amber-400" />
            <span className="text-sm font-bold text-white">Plan-Ahead Model</span>
            <span className="text-xs text-slate-500">
              Week #{result.summary.week_number} · Interval {result.interval}
            </span>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Summary stats */}
        <div className="flex gap-4 mb-5 text-xs text-slate-400">
          <span>
            Avg nodes/tenant:{' '}
            <span className="text-white font-bold">
              {result.summary.avg_nodes_per_tenant.toFixed(1)}
            </span>
          </span>
          <span>
            Isolation score:{' '}
            <span className="text-white font-bold">
              {(result.summary.isolation_score * 100).toFixed(0)}%
            </span>
          </span>
        </div>

        {/* Heatmap grid */}
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr>
                <th className="text-left text-slate-500 font-normal pb-2 pr-3 w-20">Tenant</th>
                {nodes.map((n) => (
                  <th key={n} className="text-center text-slate-500 font-normal pb-2 px-1">
                    N{n}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tenants.map((t) => {
                const tc = TENANT_COLORS[t] ?? '#94a3b8'
                return (
                  <tr key={t}>
                    <td className="pr-3 py-1">
                      <span className="font-bold text-[11px]" style={{ color: tc }}>
                        {TENANT_NAMES[t] ?? `T${t}`}
                      </span>
                    </td>
                    {nodes.map((n) => {
                      const cell = cellMap[`${t}_${n}`]
                      const auth = cell?.authorized ?? false
                      const val  = cell?.intensity ?? 0
                      const bg   = intensityColor(val)
                      return (
                        <td key={n} className="px-1 py-1">
                          <motion.div
                            initial={{ opacity: 0, scale: 0.5 }}
                            animate={{ opacity: 1, scale: 1 }}
                            transition={{ delay: (t * nodes.length + n) * 0.03 }}
                            className="h-10 w-14 rounded flex flex-col items-center justify-center border"
                            style={{
                              backgroundColor: bg,
                              borderColor: auth ? tc + '60' : '#1e293b',
                            }}
                            title={auth
                              ? `${TENANT_NAMES[t]} → Node ${n}: ${(val * 100).toFixed(0)}% predicted load`
                              : 'Not authorized'}
                          >
                            {auth ? (
                              <>
                                <span className="text-[10px] font-bold" style={{ color: tc }}>
                                  {(val * 100).toFixed(0)}%
                                </span>
                                <span className="text-[8px] text-slate-400">load</span>
                              </>
                            ) : (
                              <span className="text-slate-700 text-[10px]">—</span>
                            )}
                          </motion.div>
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {/* Legend */}
        <div className="mt-4 flex items-center gap-4 text-[10px] text-slate-500">
          <div className="flex items-center gap-1">
            <div className="w-3 h-3 rounded" style={{ backgroundColor: intensityColor(0.9) }} />
            <span>High load</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="w-3 h-3 rounded" style={{ backgroundColor: intensityColor(0.3) }} />
            <span>Low load</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="w-3 h-3 rounded bg-slate-800 border border-slate-700" />
            <span>Not authorized</span>
          </div>
          <span className="ml-auto text-slate-600">Auto-closes in 8s · click anywhere to dismiss</span>
        </div>
      </motion.div>
    </motion.div>
  )
}
