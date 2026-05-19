import type { ElementType } from 'react'
import type { HUDData, SimTotals } from '../types'
import { Cpu, Users, Server, HardDrive, Clock, Calendar } from 'lucide-react'

interface Props {
  hud: HUDData
  interval: number
  planAheadInterval: number
  utilMode: 0 | 1 | 2
  simTotals: SimTotals
}

function Stat({ icon: Icon, label, value, accent = false }: {
  icon: ElementType
  label: string
  value: string | number
  accent?: boolean
}) {
  return (
    <div className="flex items-center gap-1.5 leading-none">
      <Icon size={11} className={accent ? 'text-amber-400' : 'text-slate-500'} />
      <span className="text-slate-500 text-[11px]">{label}</span>
      <span className={`text-[11px] font-bold tabular-nums ml-auto ${accent ? 'text-amber-300' : 'text-white'}`}>
        {value}
      </span>
    </div>
  )
}

function TotalRow({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-1 leading-none">
      <span className="text-slate-600 text-[10px] shrink-0">{label}</span>
      <span className="text-slate-400 text-[10px] tabular-nums font-bold text-right">
        {value}{sub ? <span className="text-slate-600 font-normal ml-0.5">{sub}</span> : null}
      </span>
    </div>
  )
}

export function HUD({ hud, interval, planAheadInterval, utilMode, simTotals }: Props) {
  const displayPct = utilMode === 2 ? (hud.eff_active_utilization_pct ?? 0)
                   : utilMode === 1 ? hud.eff_utilization_pct
                   : hud.mem_utilization_pct
  const label      = utilMode === 2 ? 'ACT' : utilMode === 1 ? 'EFF' : 'CAP'
  const memColor   =
    displayPct > 80 ? 'text-red-400' :
    displayPct > 55 ? 'text-amber-400' :
    'text-emerald-400'

  return (
    <div className="h-full flex flex-col px-2 py-2 overflow-y-auto queue-scroll w-52">
      <div className="bg-slate-900 rounded-xl border border-slate-700/40 flex flex-col gap-3 px-4 py-4">

        {/* Memory % — large display */}
        <div className="flex items-baseline gap-1 border-b border-slate-800 pb-3">
          <HardDrive size={13} className={memColor} />
          <span className={`text-3xl font-black tabular-nums leading-none ${memColor}`}>
            {displayPct.toFixed(1)}
          </span>
          <span className="text-slate-400 text-sm leading-none">%</span>
          <span className="text-slate-600 text-[10px] leading-none ml-0.5">{label}</span>
        </div>

        <Stat icon={Cpu}    label="Jobs"    value={hud.total_jobs} />
        <Stat icon={Users}  label="Tenants" value={hud.total_tenants} />
        <Stat icon={Server} label="Nodes"   value={hud.total_nodes} />
        <Stat icon={Clock}  label="Wait↑"   value={`${hud.longest_wait_intervals}i`} />

        <div className="border-t border-slate-800 pt-3 mt-auto">
          <Stat
            icon={Calendar}
            label="Plan-Ahead"
            value={`${hud.intervals_to_plan_ahead} / ${planAheadInterval}i`}
            accent
          />
          <div className="text-slate-700 text-[10px] mt-2 pl-4 tabular-nums">
            #{interval}
          </div>
        </div>

      </div>

      {/* Cumulative totals — shown after at least 1 batch */}
      {simTotals.num_batches > 0 && (
        <div className="mt-2 bg-slate-900 rounded-xl border border-slate-700/40 px-4 py-3 flex flex-col gap-1.5">
          <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-0.5 border-b border-slate-800 pb-1.5">
            Totals — {simTotals.num_batches} batch{simTotals.num_batches !== 1 ? 'es' : ''}
          </div>

          <TotalRow label="Generated"    value={simTotals.total_generated} />
          <TotalRow label="Placed"       value={simTotals.total_placed} sub={`(${simTotals.placement_rate.toFixed(1)}%)`} />
          <TotalRow label="Queue left"   value={simTotals.final_queue_size} />
          <TotalRow label="Expired"      value={simTotals.total_expired} />

          <div className="border-t border-slate-800 pt-1.5 mt-0.5 flex flex-col gap-1.5">
            <TotalRow label="Viols"      value={simTotals.total_viols} />
            <TotalRow label="Spikes"     value={simTotals.total_spikes} />
            <TotalRow label="Ovrflw"     value={simTotals.total_ovrflw} />
          </div>

          <div className="border-t border-slate-800 pt-1.5 mt-0.5 flex flex-col gap-1.5">
            <TotalRow label="Avg placed" value={simTotals.avg_placed_per_batch.toFixed(1)} sub="/batch" />
            <TotalRow label="Avg queue"  value={simTotals.avg_queue_per_batch.toFixed(1)} sub="/batch" />
            <TotalRow label="Avg Act%"   value={`${(simTotals.avg_act_pct ?? 0).toFixed(1)}%`} />
            <TotalRow label="Avg Eff%"   value={`${simTotals.avg_eff_pct.toFixed(1)}%`} />
            <TotalRow label="Avg Phys%"  value={`${simTotals.avg_phys_pct.toFixed(1)}%`} />
            <TotalRow label="Avg solves" value={simTotals.avg_solver_calls.toFixed(1)} />
            <TotalRow label="K window"  value={simTotals.k_window} />
          </div>

        </div>
      )}
    </div>
  )
}
