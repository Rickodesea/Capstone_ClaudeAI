import type { ElementType } from 'react'
import type { HUDData } from '../types'
import { Cpu, Users, Server, HardDrive, Clock, Calendar } from 'lucide-react'

interface Props {
  hud: HUDData
  interval: number
  planAheadInterval: number
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

export function HUD({ hud, interval, planAheadInterval }: Props) {
  const memColor =
    hud.mem_utilization_pct > 80 ? 'text-red-400' :
    hud.mem_utilization_pct > 55 ? 'text-amber-400' :
    'text-emerald-400'

  return (
    <div className="h-full flex flex-col px-2 py-2">
      <div className="flex-1 bg-slate-900 rounded-xl border border-slate-700/40 flex flex-col gap-3 px-4 py-4">

        {/* Memory % — large display */}
        <div className="flex items-baseline gap-1 border-b border-slate-800 pb-3">
          <HardDrive size={13} className={memColor} />
          <span className={`text-3xl font-black tabular-nums leading-none ${memColor}`}>
            {hud.mem_utilization_pct.toFixed(1)}
          </span>
          <span className="text-slate-400 text-sm leading-none">%</span>
          <span className="text-slate-600 text-[10px] leading-none ml-0.5">MEM</span>
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
    </div>
  )
}
