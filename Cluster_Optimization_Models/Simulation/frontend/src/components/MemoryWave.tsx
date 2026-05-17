import { AreaChart, Area, ResponsiveContainer, YAxis, Tooltip, ReferenceLine, XAxis } from 'recharts'

interface Props {
  history:    number[]   // cap (physical) utilization %
  effHistory: number[]   // effective utilization %
}

export function MemoryWave({ history, effHistory }: Props) {
  const len  = Math.max(history.length, effHistory.length)
  const data = Array.from({ length: len }, (_, i) => ({
    i,
    cap: history[i]    ?? null,
    eff: effHistory[i] ?? null,
  }))

  const lastCap = history.length    > 0 ? history[history.length - 1]    : null
  const lastEff = effHistory.length > 0 ? effHistory[effHistory.length - 1] : null

  return (
    <div className="relative w-full h-full bg-slate-950 min-h-0">
      {/* Title */}
      <div className="absolute top-1 left-0 right-0 text-center text-[11px] font-semibold text-slate-400 z-10 pointer-events-none">
        Memory Utilization
      </div>

      {/* Legend */}
      <div className="absolute top-1 right-2 flex items-center gap-3 z-10 pointer-events-none">
        <span className="flex items-center gap-1 text-[10px] text-slate-500">
          <span className="inline-block w-3 h-0.5 bg-sky-400" />
          Eff {lastEff !== null ? `${lastEff.toFixed(1)}%` : '—'}
        </span>
        <span className="flex items-center gap-1 text-[10px] text-slate-500">
          <span className="inline-block w-3 h-0.5 bg-emerald-400" />
          Cap {lastCap !== null ? `${lastCap.toFixed(1)}%` : '—'}
        </span>
      </div>

      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 20, right: 4, left: 28, bottom: 4 }}>
          <defs>
            <linearGradient id="capGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"  stopColor="#22c55e" stopOpacity={0.40} />
              <stop offset="95%" stopColor="#22c55e" stopOpacity={0.03} />
            </linearGradient>
            <linearGradient id="effGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"  stopColor="#0ea5e9" stopOpacity={0.35} />
              <stop offset="95%" stopColor="#0ea5e9" stopOpacity={0.03} />
            </linearGradient>
          </defs>

          <YAxis
            domain={[0, 100]}
            tickCount={5}
            tick={{ fontSize: 10, fill: '#475569' }}
            tickFormatter={(v) => `${v}%`}
            width={26}
          />
          <XAxis dataKey="i" hide />

          <ReferenceLine y={80} stroke="#ef444466" strokeDasharray="4 4" />
          <ReferenceLine y={50} stroke="#f59e0b44" strokeDasharray="4 4" />

          <Tooltip
            content={({ active, payload }) =>
              active && payload?.length ? (
                <div className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 space-y-0.5">
                  {payload.map((p) => (
                    <div key={p.dataKey} style={{ color: p.color }}>
                      {p.dataKey === 'cap' ? 'Cap' : 'Eff'}: {(p.value as number)?.toFixed(1)}%
                    </div>
                  ))}
                </div>
              ) : null
            }
          />

          {/* Cap utilization — emerald, drawn first (behind) */}
          <Area
            type="monotone"
            dataKey="cap"
            stroke="#22c55e"
            strokeWidth={1.5}
            fill="url(#capGrad)"
            isAnimationActive={false}
            dot={false}
            connectNulls
          />

          {/* Eff utilization — sky blue, drawn on top */}
          <Area
            type="monotone"
            dataKey="eff"
            stroke="#0ea5e9"
            strokeWidth={2}
            fill="url(#effGrad)"
            isAnimationActive={false}
            dot={false}
            connectNulls
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
