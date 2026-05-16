import { AnimatePresence, motion } from 'framer-motion'
import type { QueuedJob } from '../types'
import { TENANT_COLORS, TENANT_NAMES } from '../types'
import { Clock, Cpu, HardDrive } from 'lucide-react'

interface Props {
  queue: QueuedJob[]
  recentPlacements: Set<string>
}

const MAX_DISPLAY = 15

export function JobQueue({ queue, recentPlacements }: Props) {
  const oldest       = queue[0]?.wait_intervals ?? 0
  const displayQueue = queue.slice(0, MAX_DISPLAY)
  const overflow     = queue.length - MAX_DISPLAY

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-3 py-2 border-b border-slate-800 shrink-0">
        <span className="text-[11px] font-bold text-slate-300 uppercase tracking-widest">
          Queue
        </span>
        <span className="text-[11px] text-slate-500 tabular-nums">
          {queue.length} pending
        </span>
      </div>

      <div className="flex-1 overflow-y-auto queue-scroll px-2 py-1 space-y-1 min-h-0">
        <AnimatePresence initial={false}>
          {displayQueue.map((job) => {
            const color    = TENANT_COLORS[job.tenant_id] ?? '#94a3b8'
            const isOldest = job.wait_intervals === oldest && oldest > 0
            const wasPlaced = recentPlacements.has(job.job_id)

            return (
              <motion.div
                key={job.job_id}
                layout
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: wasPlaced ? 0.35 : 1, x: 0 }}
                exit={{ opacity: 0, x: 24, height: 0, paddingTop: 0, paddingBottom: 0, marginBottom: 0 }}
                transition={{ duration: 0.2 }}
                className={`rounded border-l-2 bg-slate-900 px-2 py-1.5 text-xs
                  ${isOldest ? 'ring-1 ring-amber-500/40' : ''}`}
                style={{ borderColor: color }}
              >
                {/* Job ID + tenant tag */}
                <div className="flex items-center justify-between gap-1">
                  <span className="text-slate-300 font-semibold truncate text-[11px]">
                    {job.job_id}
                  </span>
                  <span
                    className="text-[9px] font-bold px-1 py-0.5 rounded shrink-0"
                    style={{ color, backgroundColor: color + '22' }}
                  >
                    {TENANT_NAMES[job.tenant_id] ?? `T${job.tenant_id}`}
                  </span>
                </div>

                {/* CPU + Memory row */}
                <div className="flex items-center gap-2 mt-1 text-[10px] text-slate-400">
                  <span className="flex items-center gap-0.5">
                    <Cpu size={9} className="text-slate-600" />
                    {job.req_cpu.toFixed(2)}c
                  </span>
                  <span className="flex items-center gap-0.5">
                    <HardDrive size={9} className="text-slate-600" />
                    {job.pred_mem_mb.toFixed(0)} MB
                  </span>
                  <span className="text-slate-700 text-[9px]">
                    req {job.req_mem_mb.toFixed(0)}
                  </span>
                </div>

                {/* Wait timer */}
                {job.wait_intervals > 0 && (
                  <div className={`flex items-center gap-0.5 mt-0.5 text-[10px]
                    ${isOldest ? 'text-amber-400' : 'text-slate-700'}`}>
                    <Clock size={9} />
                    <span className="tabular-nums">{job.wait_intervals}i</span>
                    {isOldest && (
                      <span className="text-[9px] text-amber-500 ml-0.5 animate-pulse">
                        ● LONGEST
                      </span>
                    )}
                  </div>
                )}
              </motion.div>
            )
          })}
        </AnimatePresence>

        {overflow > 0 && (
          <div className="text-center text-slate-700 text-[10px] py-1">
            +{overflow} more in queue
          </div>
        )}

        {queue.length === 0 && (
          <div className="text-center text-slate-800 text-xs py-8">
            queue empty
          </div>
        )}
      </div>
    </div>
  )
}
