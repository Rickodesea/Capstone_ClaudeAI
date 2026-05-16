import { BarChart, Bar, ResponsiveContainer, YAxis, Tooltip, XAxis } from 'recharts'

interface Props {
  history: number[]
}

export function PlacedHistoryChart({ history }: Props) {
  const data = history.map((v, i) => ({ i, placed: v }))
  const maxVal = Math.max(...history, 1)

  return (
    <div className="relative w-full h-full bg-slate-950 min-h-0">
      <div className="absolute top-1 left-1 text-[11px] font-semibold text-slate-400 z-10 pointer-events-none">
        Placed Jobs / Step
      </div>
      <div className="absolute top-1 right-2 text-[10px] text-slate-600 z-10 pointer-events-none tabular-nums">
        {history.length > 0 ? `${history[history.length - 1]}` : '—'}
      </div>

      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 20, right: 4, left: 28, bottom: 4 }} barCategoryGap="20%">
          <YAxis
            domain={[0, maxVal + 2]}
            tickCount={4}
            tick={{ fontSize: 10, fill: '#475569' }}
            width={26}
          />
          <XAxis dataKey="i" hide />

          <Tooltip
            content={({ active, payload }) =>
              active && payload?.length ? (
                <div className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200">
                  {payload[0].value} placed
                </div>
              ) : null
            }
          />

          <Bar
            dataKey="placed"
            fill="#38bdf8"
            isAnimationActive={false}
            radius={[2, 2, 0, 0]}
            opacity={0.75}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
