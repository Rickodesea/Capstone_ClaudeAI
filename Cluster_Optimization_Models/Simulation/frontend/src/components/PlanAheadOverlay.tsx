import { motion } from 'framer-motion'
import type { PlanAheadResult } from '../types'
import { TENANT_COLORS, TENANT_NAMES } from '../types'
import { X, CalendarCheck } from 'lucide-react'

interface Props {
  result: PlanAheadResult
  numNodes: number
  onClose: () => void
}

export function PlanAheadOverlay({ result, numNodes, onClose }: Props) {
  const tenants = Object.keys(result.tenant_schedule).map(Number).sort()
  const slots   = Array.from({ length: result.num_slots }, (_, i) => i)
  const nodes   = Array.from({ length: numNodes }, (_, i) => i)

  // Invert tenant_schedule → nodeSchedule[node][slot] = [tenant_ids]
  const nodeSchedule: Record<number, Record<number, number[]>> = {}
  for (const n of nodes) nodeSchedule[n] = {}
  for (const t of tenants) {
    for (const s of slots) {
      const assigned = result.tenant_schedule[String(t)]?.[String(s)] ?? []
      if (assigned.includes) {
        for (const n of assigned) {
          if (nodeSchedule[n]) {
            if (!nodeSchedule[n][s]) nodeSchedule[n][s] = []
            nodeSchedule[n][s].push(t)
          }
        }
      }
    }
  }

  const slotLabels = result.slot_labels ?? slots.map((s) => `Slot ${s}`)

  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 backdrop-blur-sm"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onClick={onClose}
    >
      <motion.div
        className="bg-slate-900 border border-slate-700 rounded-xl shadow-2xl p-5 w-full max-w-4xl mx-4 max-h-[88vh] overflow-y-auto"
        initial={{ y: -40, opacity: 0, scale: 0.95 }}
        animate={{ y: 0, opacity: 1, scale: 1 }}
        exit={{ y: -20, opacity: 0, scale: 0.97 }}
        transition={{ type: 'spring', damping: 22, stiffness: 250 }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <CalendarCheck size={17} className="text-amber-400" />
            <span className="text-sm font-bold text-white">Node Access Schedule</span>
            <span className="text-xs text-slate-500">
              Week #{result.summary.week_number} · Interval {result.interval}
            </span>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Meta row */}
        <div className="flex flex-wrap gap-4 mb-4 text-xs text-slate-400">
          <span>
            Horizon: <span className="text-white font-bold">{result.planning_horizon}i</span>
          </span>
          <span>
            Access period: <span className="text-white font-bold">{result.access_period}i / slot</span>
          </span>
          <span>
            Slots: <span className="text-white font-bold">{result.num_slots}</span>
          </span>
          <span>
            Avg nodes/tenant: <span className="text-white font-bold">{result.summary.avg_nodes_per_tenant.toFixed(1)}</span>
          </span>
          <span>
            Exclusivity: <span className="text-white font-bold">{(result.summary.isolation_score * 100).toFixed(0)}%</span>
          </span>
        </div>

        {/* Gantt table: rows = nodes, cols = time slots */}
        <div className="overflow-x-auto">
          <table className="w-full text-xs border-collapse min-w-max">
            <thead>
              <tr>
                {/* Node column header */}
                <th className="text-left text-slate-600 font-normal pb-2 pr-3 w-14 sticky left-0 bg-slate-900 z-10">
                  Node
                </th>
                {slots.map((s) => {
                  const isCurrent = s === result.current_slot
                  return (
                    <th
                      key={s}
                      className={`text-center font-normal pb-2 px-1 min-w-[72px]
                        ${isCurrent ? 'text-amber-400' : 'text-slate-600'}`}
                    >
                      <div className="flex flex-col items-center gap-0.5">
                        {isCurrent && <span className="text-[9px] text-amber-500">▶ NOW</span>}
                        <span className={isCurrent ? 'font-bold' : ''}>{slotLabels[s]}</span>
                      </div>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {nodes.map((n) => (
                <tr key={n} className="border-t border-slate-800/60">
                  {/* Node label */}
                  <td className="py-2 pr-3 sticky left-0 bg-slate-900 z-10">
                    <span className="font-bold text-[11px] text-slate-400">N{n}</span>
                  </td>
                  {slots.map((s) => {
                    const tenantsHere = nodeSchedule[n]?.[s] ?? []
                    const isCurrent   = s === result.current_slot
                    return (
                      <td
                        key={s}
                        className={`py-1.5 px-1 text-center align-middle
                          ${isCurrent ? 'bg-amber-950/25' : ''}`}
                      >
                        <div className="flex flex-wrap gap-0.5 justify-center">
                          {tenantsHere.length > 0 ? tenantsHere.map((t) => {
                            const tc = TENANT_COLORS[t] ?? '#94a3b8'
                            return (
                              <motion.span
                                key={t}
                                initial={{ opacity: 0, scale: 0.7 }}
                                animate={{ opacity: 1, scale: 1 }}
                                transition={{ delay: (n * slots.length + s) * 0.008 }}
                                className="text-[10px] font-bold px-1.5 py-0.5 rounded-full"
                                style={{ color: tc, backgroundColor: tc + '28' }}
                              >
                                {TENANT_NAMES[t] ?? `T${t}`}
                              </motion.span>
                            )
                          }) : (
                            <span className="text-slate-800 text-[10px]">—</span>
                          )}
                        </div>
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Tenant legend */}
        <div className="mt-4 border-t border-slate-800 pt-3 flex items-center gap-3 flex-wrap">
          <span className="text-[10px] text-slate-600">Tenants:</span>
          {tenants.map((t) => {
            const tc = TENANT_COLORS[t] ?? '#94a3b8'
            return (
              <span key={t} className="flex items-center gap-1 text-[11px] font-bold" style={{ color: tc }}>
                <span className="w-2 h-2 rounded-full inline-block" style={{ backgroundColor: tc }} />
                {TENANT_NAMES[t] ?? `T${t}`}
              </span>
            )
          })}
          <span className="text-[10px] text-slate-700 ml-auto">▶ NOW = active slot · click anywhere to dismiss</span>
        </div>
      </motion.div>
    </motion.div>
  )
}
