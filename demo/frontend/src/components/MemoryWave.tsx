import { AreaChart, Area, ResponsiveContainer, YAxis, Tooltip, ReferenceLine, XAxis } from 'recharts'

interface Props {
  history: number[]
}

export function MemoryWave({ history }: Props) {
  const data = history.map((v, i) => ({ i, mem: v }))

  return (
    <div className="relative w-full h-full bg-slate-950 min-h-0">
      <div className="absolute top-1 left-1 text-[11px] font-semibold text-slate-400 z-10 pointer-events-none">
        Memory Utilization
      </div>
      <div className="absolute top-1 right-2 text-[10px] text-slate-600 z-10 pointer-events-none tabular-nums">
        {history.length > 0 ? `${history[history.length - 1].toFixed(1)}%` : '—'}
      </div>

      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 20, right: 4, left: 28, bottom: 4 }}>
          <defs>
            <linearGradient id="memGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor="#22c55e" stopOpacity={0.55} />
              <stop offset="95%"  stopColor="#22c55e" stopOpacity={0.04} />
            </linearGradient>
            <linearGradient id="memGradHigh" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor="#ef4444" stopOpacity={0.6} />
              <stop offset="95%"  stopColor="#ef4444" stopOpacity={0.04} />
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
                <div className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200">
                  {(payload[0].value as number)?.toFixed(1)}% mem
                </div>
              ) : null
            }
          />

          <Area
            type="monotone"
            dataKey="mem"
            stroke="#22c55e"
            strokeWidth={2}
            fill="url(#memGrad)"
            isAnimationActive={false}
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
