import { useState, useEffect, useRef, useCallback } from 'react'
import { AnimatePresence } from 'framer-motion'
import type { SimState, BatchStats } from './types'
import { TENANT_COLORS, TENANT_NAMES } from './types'
import { api } from './api'
import { HUD } from './components/HUD'
import { JobQueue } from './components/JobQueue'
import { NodeGrid } from './components/NodeGrid'
import { MemoryWave } from './components/MemoryWave'
import { PlanAheadOverlay } from './components/PlanAheadOverlay'
import { Play, Pause, RotateCcw, Zap, ChevronUp, Users, Settings, Info } from 'lucide-react'

async function fetchWithRetry(fn: () => Promise<SimState>, retries = 5, delay = 1000): Promise<SimState> {
  for (let i = 0; i < retries; i++) {
    try {
      return await fn()
    } catch {
      if (i === retries - 1) throw new Error('Backend unreachable')
      await new Promise((r) => setTimeout(r, delay))
    }
  }
  throw new Error('Backend unreachable')
}

const BATCH_INFO_ROWS: { key: keyof BatchStats; label: string; desc: string }[] = [
  { key: 'batch_id',                label: 'Batch',   desc: 'Scheduling epoch index (each batch = 60 s simulated time)' },
  { key: 'jobs_generated',          label: 'New',     desc: 'New jobs generated and added to the queue this batch' },
  { key: 'jobs_placed',             label: 'Placed',  desc: 'Jobs successfully assigned to a node this batch' },
  { key: 'queue_size_after',        label: 'Queue',   desc: 'Jobs still waiting in the queue after this batch ends' },
  { key: 'nodes_assigned',          label: 'Assign',  desc: 'Unique nodes that received at least one new job this batch' },
  { key: 'total_nodes_used',        label: 'Used',    desc: 'Total nodes with at least one running job at end of batch' },
  { key: 'spike_count',             label: 'Spike',   desc: 'Jobs whose actual runtime memory exceeded the P95 prediction' },
  { key: 'physical_overflow_count', label: 'Ovrflw',  desc: 'Nodes where tenant job memory + OS overhead exceeded physical RAM' },
  { key: 'node_violations',         label: 'Viols',   desc: 'Nodes where U_n^mem > M_n^cap (SLA breach: exceeds schedulable ceiling)' },
  { key: 'avg_phys_mem_pct',        label: 'Util%',   desc: 'Cluster-average memory utilization = U_n^mem / M_n (physical RAM), all nodes' },
  { key: 'avg_eff_mem_pct',         label: 'Eff%',    desc: 'Effective utilization = U_n^mem / M_n^cap (schedulable capacity), all nodes' },
  { key: 'avg_eff_active_pct',      label: 'Eff%(A)', desc: 'Same as Eff% but averaged only over nodes currently in use (used_mb > 0)' },
]

function fmt(key: keyof BatchStats, val: number | undefined): string {
  if (val == null) return '—'
  if (key === 'avg_phys_mem_pct' || key === 'avg_eff_mem_pct' || key === 'avg_eff_active_pct')
    return `${val.toFixed(1)}%`
  return String(val)
}

// ── Config row types ──────────────────────────────────────────────────────────
type CfgRow = {
  label: string
  val: number
  set: (v: number) => void
  key: string
  min: number
  max: number
  step?: number
  isFloat?: boolean
}

export default function App() {
  const [state,         setState]         = useState<SimState | null>(null)
  const [isRunning,     setIsRunning]     = useState(false)
  const [baseSeconds,   setBaseSeconds]   = useState(1)
  // 0 = Cap Util (physical), 1 = Eff Util (all nodes), 2 = Act Util (active nodes only)
  const [utilMode,      setUtilMode]      = useState<0 | 1 | 2>(1)
  const [showPlanAhead, setShowPlanAhead] = useState(false)
  const [planAheadData, setPlanAheadData] = useState<SimState['plan_ahead']>(null)
  const [showMore,         setShowMore]         = useState(false)
  const [moreTab,          setMoreTab]          = useState<'batch' | 'glossary'>('batch')
  const [planAheadLoading, setPlanAheadLoading] = useState(false)
  const [showConfig,    setShowConfig]    = useState(false)
  const [showTenants,   setShowTenants]   = useState(false)
  const [error,         setError]         = useState<string | null>(null)
  const [recentNodeIds, setRecentNodeIds] = useState<Set<number>>(new Set())
  const [recentJobIds,  setRecentJobIds]  = useState<Set<string>>(new Set())
  const [loading,       setLoading]       = useState(true)

  // ── Backend sim config (applied on next Reset) ─────────────────────────────
  // Topology
  const [cfgNumNodes,           setCfgNumNodes]           = useState(5)
  const [cfgNumTenants,         setCfgNumTenants]         = useState(3)
  const [cfgNodeMemMinGb,       setCfgNodeMemMinGb]       = useState(16)
  const [cfgNodeMemMaxGb,       setCfgNodeMemMaxGb]       = useState(64)
  const [cfgNodeCpuMin,         setCfgNodeCpuMin]         = useState(8)
  const [cfgNodeCpuMax,         setCfgNodeCpuMax]         = useState(64)
  // Workload
  const [cfgJobsMinPerRound,    setCfgJobsMinPerRound]    = useState(5)
  const [cfgJobsMaxPerRound,    setCfgJobsMaxPerRound]    = useState(20)
  const [cfgReqMemMinMb,        setCfgReqMemMinMb]        = useState(512)
  const [cfgReqMemMaxMb,        setCfgReqMemMaxMb]        = useState(1024)
  const [cfgReqCpuMin,          setCfgReqCpuMin]          = useState(0.25)
  const [cfgReqCpuMax,          setCfgReqCpuMax]          = useState(4.0)
  const [cfgSpikeProb,          setCfgSpikeProb]          = useState(10)
  const [cfgMinLifetimeSec,     setCfgMinLifetimeSec]     = useState(60)
  const [cfgMaxLifetimeSec,     setCfgMaxLifetimeSec]     = useState(600)
  // Scheduler
  const [cfgBatchDurationSec,   setCfgBatchDurationSec]   = useState(60)
  const [cfgMaxJobsPerSolve,    setCfgMaxJobsPerSolve]    = useState(0)
  const [cfgKWindow,            setCfgKWindow]            = useState(10)
  const [cfgMemThresholdFrac,   setCfgMemThresholdFrac]   = useState(0.10)
  // Plan-Ahead
  const [cfgPlanAheadInterval,  setCfgPlanAheadInterval]  = useState(50)
  const [cfgAccessPeriod,       setCfgAccessPeriod]       = useState(4)
  const [cfgTenantUsageMin,     setCfgTenantUsageMin]     = useState(0.8)
  const [cfgTenantUsageMax,     setCfgTenantUsageMax]     = useState(6.0)
  const [cfgPlanTimeLimit,      setCfgPlanTimeLimit]      = useState(30)
  const [cfgPlanMipGap,         setCfgPlanMipGap]         = useState(0.05)
  const [cfgPriorityBoost,      setCfgPriorityBoost]      = useState(2.0)
  const [cfgUseSocp,            setCfgUseSocp]            = useState(0)   // 0=MILP, 1=SOCP
  const [cfgSigmaFrac,          setCfgSigmaFrac]          = useState(0.20)
  const [cfgCantelliEpsilon,    setCfgCantelliEpsilon]    = useState(0.10)

  // Frontend-only color thresholds
  const [actGreenThreshold, setActGreenThreshold] = useState(99)
  const [effGreenThreshold, setEffGreenThreshold] = useState(95)
  const [capGreenThreshold, setCapGreenThreshold] = useState(85)

  const timerRef   = useRef<ReturnType<typeof setInterval> | null>(null)
  const paTimer    = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isStepping = useRef(false)
  const moreRef    = useRef<HTMLDivElement>(null)
  const configRef  = useRef<HTMLDivElement>(null)
  const tenantsRef = useRef<HTMLDivElement>(null)

  const stepMs = Math.max(0, Math.round(baseSeconds * 1000))

  // ── Initial load ────────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    fetchWithRetry(() => api.getState())
      .then((s) => {
        if (!cancelled) {
          setState(s)
          if (s.sim_config) {
            const c = s.sim_config
            // Topology
            if (c.num_nodes            != null) setCfgNumNodes(c.num_nodes)
            if (c.num_tenants          != null) setCfgNumTenants(c.num_tenants)
            if (c.node_mem_min_gb      != null) setCfgNodeMemMinGb(c.node_mem_min_gb)
            if (c.node_mem_max_gb      != null) setCfgNodeMemMaxGb(c.node_mem_max_gb)
            if (c.node_cpu_min         != null) setCfgNodeCpuMin(c.node_cpu_min)
            if (c.node_cpu_max         != null) setCfgNodeCpuMax(c.node_cpu_max)
            // Workload
            if (c.jobs_min_per_round   != null) setCfgJobsMinPerRound(c.jobs_min_per_round)
            if (c.jobs_max_per_round   != null) setCfgJobsMaxPerRound(c.jobs_max_per_round)
            if (c.req_mem_min_mb       != null) setCfgReqMemMinMb(c.req_mem_min_mb)
            if (c.req_mem_max_mb       != null) setCfgReqMemMaxMb(c.req_mem_max_mb)
            if (c.req_cpu_min          != null) setCfgReqCpuMin(c.req_cpu_min)
            if (c.req_cpu_max          != null) setCfgReqCpuMax(c.req_cpu_max)
            if (c.spike_prob_pct       != null) setCfgSpikeProb(c.spike_prob_pct)
            if (c.min_lifetime_sec     != null) setCfgMinLifetimeSec(c.min_lifetime_sec)
            if (c.max_lifetime_sec     != null) setCfgMaxLifetimeSec(c.max_lifetime_sec)
            // Scheduler
            if (c.batch_duration_sec   != null) setCfgBatchDurationSec(c.batch_duration_sec)
            if (c.max_jobs_per_solve   != null) setCfgMaxJobsPerSolve(c.max_jobs_per_solve)
            if (c.k_window             != null) setCfgKWindow(c.k_window)
            if (c.mem_threshold_frac   != null) setCfgMemThresholdFrac(c.mem_threshold_frac)
            // Plan-Ahead
            if (c.plan_ahead_interval  != null) setCfgPlanAheadInterval(c.plan_ahead_interval)
            if (c.access_period        != null) setCfgAccessPeriod(c.access_period)
            if (c.tenant_usage_min     != null) setCfgTenantUsageMin(c.tenant_usage_min)
            if (c.tenant_usage_max     != null) setCfgTenantUsageMax(c.tenant_usage_max)
            if (c.plan_time_limit      != null) setCfgPlanTimeLimit(c.plan_time_limit)
            if (c.plan_mip_gap         != null) setCfgPlanMipGap(c.plan_mip_gap)
            if (c.priority_boost       != null) setCfgPriorityBoost(c.priority_boost)
            if (c.use_socp             != null) setCfgUseSocp(c.use_socp)
            if (c.sigma_frac           != null) setCfgSigmaFrac(c.sigma_frac)
            if (c.cantelli_epsilon     != null) setCfgCantelliEpsilon(c.cantelli_epsilon)
          }
          if (s.plan_ahead) {
            setPlanAheadData(s.plan_ahead)
            setShowPlanAhead(true)
            paTimer.current = setTimeout(() => setShowPlanAhead(false), 8000)
          }
          setLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError('Backend not reachable — start uvicorn first')
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [])

  // Outside-click handlers
  useEffect(() => {
    if (!showMore) return
    const h = (e: MouseEvent) => { if (moreRef.current && !moreRef.current.contains(e.target as Node)) setShowMore(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [showMore])

  useEffect(() => {
    if (!showConfig) return
    const h = (e: MouseEvent) => { if (configRef.current && !configRef.current.contains(e.target as Node)) setShowConfig(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [showConfig])

  useEffect(() => {
    if (!showTenants) return
    const h = (e: MouseEvent) => { if (tenantsRef.current && !tenantsRef.current.contains(e.target as Node)) setShowTenants(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [showTenants])

  // ── Step / run loop ─────────────────────────────────────────────────────────
  const advance = useCallback(async () => {
    if (isStepping.current) return
    isStepping.current = true
    try {
      const next = await api.step()
      setState(next)
      setError(null)

      if (next.recent_placements.length > 0) {
        const nodeIds = new Set(next.recent_placements.map((p) => p.node_id))
        const jobIds  = new Set(next.recent_placements.map((p) => p.job_id))
        setRecentNodeIds(nodeIds)
        setRecentJobIds(jobIds)
        setTimeout(() => { setRecentNodeIds(new Set()); setRecentJobIds(new Set()) }, 700)
      }

      if (next.plan_ahead) {
        setPlanAheadData(next.plan_ahead)
        setShowPlanAhead(true)
        if (paTimer.current) clearTimeout(paTimer.current)
        paTimer.current = setTimeout(() => setShowPlanAhead(false), 8000)
      }
    } catch {
      setError('Step failed — is the backend running?')
      setIsRunning(false)
    } finally {
      isStepping.current = false
    }
  }, [])

  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current)
    if (!isRunning) return
    timerRef.current = setInterval(advance, stepMs)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [isRunning, stepMs, advance])

  // ── Config posting ──────────────────────────────────────────────────────────
  const postConfig = useCallback(async (patch: Record<string, number>) => {
    try {
      await fetch('http://localhost:8000/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
    } catch { /* backend may be unreachable */ }
  }, [])

  const handleReset = async () => {
    setIsRunning(false)
    isStepping.current = false
    const fresh = await api.reset()
    setState(fresh)
    setError(null)
    // Auto-run plan-ahead after every reset so the schedule is always fresh
    try {
      const pa = await api.triggerPlanAhead()
      setPlanAheadData(pa)
      setShowPlanAhead(true)
      if (paTimer.current) clearTimeout(paTimer.current)
      paTimer.current = setTimeout(() => setShowPlanAhead(false), 8000)
    } catch { /* plan-ahead is optional — ignore failures */ }
  }

  const handleStep = () => { setIsRunning(false); advance() }

  // ── Config section renderer ─────────────────────────────────────────────────
  function CfgSection({ title, rows }: { title: string; rows: CfgRow[] }) {
    return (
      <>
        <div className="text-[10px] text-slate-500 mb-1 mt-3">{title}</div>
        <div className="space-y-1.5">
          {rows.map(({ label, val, set, key, min, max, step, isFloat }) => (
            <div key={key} className="flex items-center justify-between gap-2 text-[11px]">
              <span className="text-slate-400 shrink-0">{label}</span>
              <input
                type="number"
                min={min} max={max}
                step={step ?? (isFloat ? 0.01 : 1)}
                value={val}
                onChange={(e) => {
                  const v = isFloat ? parseFloat(e.target.value) : parseInt(e.target.value)
                  if (!isNaN(v)) { set(v); postConfig({ [key]: v }) }
                }}
                className="w-16 bg-slate-700 border border-slate-600 rounded px-1.5 py-0.5 text-xs text-white tabular-nums text-right focus:outline-none"
              />
            </div>
          ))}
        </div>
      </>
    )
  }

  // ── Loading / error screens ─────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="h-screen flex items-center justify-center text-slate-500 text-sm">
        <span className="animate-pulse">Connecting to backend...</span>
      </div>
    )
  }

  if (!state) {
    return (
      <div className="h-screen flex flex-col items-center justify-center gap-3">
        <div className="text-red-400 text-sm">{error}</div>
        <div className="text-slate-600 text-xs font-mono bg-slate-900 px-3 py-2 rounded">
          cd Simulation/api &amp;&amp; uvicorn main:app --reload --port 8000
        </div>
        <button
          onClick={() => { setLoading(true); setError(null); window.location.reload() }}
          className="text-xs text-slate-500 hover:text-white mt-2"
        >Retry</button>
      </div>
    )
  }

  const activeNodes = state.nodes.filter((n) => n.running_jobs.length > 0).length
  const batchStats  = state.batch_stats
  const defaultTotals = {
    num_batches: 0, k_window: 10, total_generated: 0, total_placed: 0, placement_rate: 0,
    final_queue_size: 0, total_viols: 0, total_spikes: 0, total_ovrflw: 0,
    total_expired: 0, avg_placed_per_batch: 0, avg_queue_per_batch: 0,
    avg_eff_pct: 0, avg_phys_pct: 0, avg_act_pct: 0, avg_solver_calls: 0, final_w_t: {},
  }
  const simTotals = state.sim_totals ?? defaultTotals

  return (
    <div className="h-screen flex flex-col bg-slate-950 overflow-hidden select-none">

      {/* ── Top: Memory wave ─────────────────────────────────────────────── */}
      <div className="shrink-0 h-28 border-b border-slate-800">
        <MemoryWave history={state.mem_history} effHistory={state.eff_history} actHistory={state.eff_active_history ?? []} />
      </div>

      {/* ── Section headers ──────────────────────────────────────────────── */}
      <div className="shrink-0 flex border-b border-slate-800">
        <div className="w-52 shrink-0 border-r border-slate-800 px-3 py-1.5 flex items-center">
          <span className="text-[11px] font-bold text-slate-300 uppercase tracking-widest">Queue</span>
          <span className="text-[11px] text-slate-500 ml-auto tabular-nums">{state.queue.length} pending</span>
        </div>
        <div className="flex-1 px-3 py-1.5 flex items-center">
          <span className="text-[11px] font-bold text-slate-300 uppercase tracking-widest">Cluster Nodes</span>
          <span className="text-[11px] text-slate-500 ml-auto">{activeNodes}/{state.nodes.length} active</span>
        </div>
        <div className="w-52 shrink-0 border-l border-slate-800 px-3 py-1.5 flex items-center">
          <span className="text-[11px] font-bold text-slate-300 uppercase tracking-widest">Summary</span>
        </div>
      </div>

      {/* ── Main content ─────────────────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0">

        <div className="w-52 shrink-0 border-r border-slate-800 flex flex-col min-h-0">
          <JobQueue queue={state.queue} recentPlacements={recentJobIds} />
        </div>

        <div className="flex-1 min-h-0">
          <NodeGrid
            nodes={state.nodes}
            newJobNodeIds={recentNodeIds}
            useEffective={utilMode > 0}
            effGreenThreshold={utilMode === 2 ? actGreenThreshold : effGreenThreshold}
            capGreenThreshold={capGreenThreshold}
          />
        </div>

        <div className="border-l border-slate-800 shrink-0 flex flex-col min-h-0">
          <HUD
            hud={state.hud}
            interval={state.interval}
            planAheadInterval={state.plan_ahead_interval}
            utilMode={utilMode}
            simTotals={simTotals}
          />
        </div>
      </div>

      {/* ── Controls bar ─────────────────────────────────────────────────── */}
      <div className="shrink-0 border-t border-slate-800 bg-slate-900 px-3 py-2 flex items-center gap-2 flex-wrap">

        <button
          onClick={() => setIsRunning((r) => !r)}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-bold transition-colors
            ${isRunning ? 'bg-slate-700 hover:bg-slate-600 text-white' : 'bg-emerald-600 hover:bg-emerald-500 text-white'}`}
        >
          {isRunning ? <><Pause size={11} /> Pause</> : <><Play size={11} /> Play</>}
        </button>

        <button
          onClick={handleStep}
          className="flex items-center gap-1 px-2 py-1.5 rounded text-xs text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
        >
          <Zap size={11} /> Step
        </button>

        <div className="w-px h-4 bg-slate-700" />

        <div className="flex items-center gap-1">
          <input
            type="number" min={0} step={0.5} value={baseSeconds}
            onChange={(e) => { const v = parseFloat(e.target.value); if (!isNaN(v) && v >= 0) setBaseSeconds(v) }}
            className="w-14 bg-slate-800 border border-slate-700 rounded px-1.5 py-1 text-xs text-white tabular-nums text-right focus:outline-none focus:border-slate-500"
          />
          <span className="text-[11px] text-slate-500">s delay</span>
        </div>

        <div className="w-px h-4 bg-slate-700" />

        <button
          onClick={() => setUtilMode((m) => ((m + 1) % 3) as 0 | 1 | 2)}
          className={`px-2 py-1.5 rounded text-xs border transition-colors
            ${utilMode === 1 ? 'border-sky-600 text-sky-400 bg-sky-950'
            : utilMode === 2 ? 'border-amber-600 text-amber-400 bg-amber-950'
            : 'border-slate-700 text-slate-500 hover:text-white'}`}
        >
          {utilMode === 0 ? 'Cap Util' : utilMode === 1 ? 'Eff Util' : 'Act Util'}
        </button>

        <button
          onClick={async () => {
            if (planAheadLoading) return
            if (planAheadData) {
              setShowPlanAhead(true)
              return
            }
            setPlanAheadLoading(true)
            try {
              const result = await api.triggerPlanAhead()
              setPlanAheadData(result)
              setShowPlanAhead(true)
              if (paTimer.current) clearTimeout(paTimer.current)
              paTimer.current = setTimeout(() => setShowPlanAhead(false), 8000)
            } catch {
              setError('Plan-ahead failed — is the backend running?')
            } finally {
              setPlanAheadLoading(false)
            }
          }}
          disabled={planAheadLoading}
          className={`px-2 py-1.5 rounded text-xs border transition-colors
            ${planAheadLoading
              ? 'border-amber-800 text-amber-700 cursor-wait'
              : planAheadData
                ? 'border-amber-700 text-amber-400 hover:bg-amber-950'
                : 'border-slate-600 text-slate-400 hover:text-amber-400 hover:border-amber-800'}`}
        >
          {planAheadLoading ? 'Running…' : 'Plan Ahead'}
        </button>

        {/* Tenants popup */}
        <div className="relative" ref={tenantsRef}>
          <button
            onClick={() => setShowTenants((v) => !v)}
            className={`flex items-center gap-1 px-2 py-1.5 rounded text-xs border transition-colors
              ${showTenants ? 'border-slate-500 text-white bg-slate-800' : 'border-slate-700 text-slate-500 hover:text-white'}`}
          >
            <Users size={10} /> Tenants
          </button>

          {showTenants && (
            <div className="absolute bottom-full mb-2 left-0 w-72 bg-slate-800 border border-slate-700 rounded-lg shadow-xl p-3 z-50">
              <div className="text-[11px] font-bold text-slate-300 mb-2 uppercase tracking-widest">Tenants</div>
              <div className="space-y-2">
                {state.tenants.map((t) => {
                  const color = TENANT_COLORS[t.tenant_id] ?? '#94a3b8'
                  const name  = TENANT_NAMES[t.tenant_id] ?? `T${t.tenant_id}`
                  // Horizon priority: union of all nodes across all plan periods
                  const horizonNodes = planAheadData
                    ? [...new Set(
                        Object.values(
                          planAheadData.tenant_schedule[String(t.tenant_id)] ?? {}
                        ).flat()
                      )].sort((a, b) => a - b)
                    : []
                  return (
                    <div key={t.tenant_id} className="bg-slate-900 rounded p-2 text-[11px]">
                      <div className="font-bold mb-1.5" style={{ color }}>{name}</div>
                      <div className="space-y-0.5">
                        <div className="flex gap-1">
                          <span className="text-slate-600 shrink-0">Now priority:</span>
                          <span className="text-slate-400">
                            {t.authorized_nodes.length > 0 ? t.authorized_nodes.map((n) => `N${n}`).join(', ') : '—'}
                          </span>
                        </div>
                        <div className="flex gap-1">
                          <span className="text-slate-600 shrink-0">Horizon priority:</span>
                          <span className="text-slate-400">
                            {horizonNodes.length > 0 ? horizonNodes.map((n) => `N${n}`).join(', ') : '—'}
                          </span>
                        </div>
                      </div>
                      <div className="mt-1.5 pt-1.5 border-t border-slate-800 flex gap-3 text-[10px]">
                        <span className="text-slate-600">Running <span className="text-emerald-400 font-bold">{t.running_jobs_count}</span></span>
                        <span className="text-slate-600">Queued <span className="text-amber-400 font-bold">{t.queued_jobs_count}</span></span>
                        <span className="text-slate-600">Avg wait <span className="text-slate-400 font-bold">{t.avg_wait_sec.toFixed(1)}s</span></span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>

        {/* More — batch info popup */}
        <div className="relative" ref={moreRef}>
          <button
            onClick={() => setShowMore((v) => !v)}
            className={`flex items-center gap-1 px-2 py-1.5 rounded text-xs border transition-colors
              ${showMore ? 'border-slate-500 text-white bg-slate-800' : 'border-slate-700 text-slate-500 hover:text-white'}`}
          >
            <Info size={10} /> More <ChevronUp size={10} className={`transition-transform ${showMore ? '' : 'rotate-180'}`} />
          </button>

          {showMore && (
            <div className="absolute bottom-full mb-2 right-0 w-[26rem] bg-slate-800 border border-slate-700 rounded-lg shadow-xl z-50 flex flex-col max-h-[80vh]">
              {/* Tab bar */}
              <div className="flex border-b border-slate-700 shrink-0">
                {(['batch', 'glossary'] as const).map((tab) => (
                  <button
                    key={tab}
                    onClick={() => setMoreTab(tab)}
                    className={`flex-1 py-2 text-[11px] font-bold uppercase tracking-widest transition-colors
                      ${moreTab === tab ? 'text-white border-b-2 border-sky-500 -mb-px' : 'text-slate-500 hover:text-slate-300'}`}
                  >
                    {tab === 'batch' ? 'Last Batch' : 'Glossary'}
                  </button>
                ))}
              </div>

              {/* Batch tab */}
              {moreTab === 'batch' && (
                <div className="p-3 overflow-y-auto">
                  <div className="text-[10px] text-slate-600 mb-2">
                    {batchStats ? `Batch #${batchStats.batch_id}` : 'Run at least one step to see batch stats.'}
                  </div>
                  {batchStats && (
                    <table className="w-full text-[11px] border-collapse">
                      <thead>
                        <tr className="border-b border-slate-700">
                          <th className="text-left text-slate-500 font-semibold pb-1.5 pr-2 w-14">Metric</th>
                          <th className="text-right text-slate-500 font-semibold pb-1.5 pr-3 w-12">Value</th>
                          <th className="text-left text-slate-500 font-semibold pb-1.5">Description</th>
                        </tr>
                      </thead>
                      <tbody>
                        {BATCH_INFO_ROWS.map(({ key, label, desc }) => (
                          <tr key={key} className="border-b border-slate-700/40 hover:bg-slate-700/20">
                            <td className="py-1 pr-2 font-bold text-slate-300 tabular-nums align-top">{label}</td>
                            <td className="py-1 pr-3 tabular-nums text-right text-emerald-400 font-bold align-top">
                              {fmt(key, batchStats[key] as number)}
                            </td>
                            <td className="py-1 text-slate-500 leading-relaxed align-top">{desc}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}

              {/* Glossary tab */}
              {moreTab === 'glossary' && (
                <div className="p-3 overflow-y-auto space-y-2.5 text-[11px]">
                  {([
                    { term: 'Cap Util',         def: 'Physical memory utilization: used_mb / total RAM. Measures how full the node actually is.' },
                    { term: 'Eff Util',          def: 'Effective utilization: used_mb / M_n^cap (schedulable capacity). M_n^cap = RAM − OS tax − safety threshold. This is what the scheduler sees as "full".' },
                    { term: 'Act Util',          def: 'Same as Eff Util but averaged only over nodes that currently have running jobs. Shows how hard the active nodes are working.' },
                    { term: 'M_n^cap',           def: 'Schedulable capacity of node n. Physical RAM minus OS overhead tax minus the safety buffer (mem_threshold_frac × RAM).' },
                    { term: 'M_n^eff',           def: 'Effective offered capacity for new jobs. M_n^cap × (1 − violation rate). Shrinks automatically when a node has recent SLA breaches.' },
                    { term: 'SLA Violation (Viol)', def: 'A scheduling round where used_mb > M_n^cap. The node exceeded its schedulable ceiling. Counted cumulatively on the node card.' },
                    { term: 'Overflow (Ovrflw)', def: 'A round where used_mb > physical RAM. More severe than a Viol — actual memory pressure on the host.' },
                    { term: 'HOT node',          def: 'A node card shown with a red border. Triggered when viols_count > 3 in the rolling K window.' },
                    { term: 'Spike',             def: 'A placed job whose actual runtime memory exceeded the P95 prediction. Contributes to overflow risk.' },
                    { term: 'K Window',          def: 'Rolling window of K recent steps used to compute the SLA violation rate (v̄_n) and tenant delay weights (ω_delay).' },
                    { term: 'ω_delay (omega)',   def: 'Tenant delay weight in the real-time objective. Tenants waiting longer than average get a higher weight so their jobs are prioritized.' },
                    { term: 'Plan-Ahead',        def: 'Optimization model (MILP or MISOCP) that runs periodically to decide which nodes each tenant should be prioritized on for the upcoming horizon.' },
                    { term: 'Priority Boost',    def: 'Multiplier applied to the real-time objective coefficient for (job, node) pairs endorsed by the plan-ahead. Soft hint — no node is blocked.' },
                    { term: 'SOCP (Cantelli)',   def: 'Second-order cone capacity constraint in MISOCP mode. Ensures actual usage stays within node capacity with at least (1−ε) probability, accounting for prediction uncertainty.' },
                    { term: 'sigma_frac',        def: 'Uncertainty fraction for SOCP mode. Demand std dev is modelled as sigma_frac × u[i,h]. Higher value = larger safety buffer.' },
                    { term: 'Cantelli ε',        def: 'Tail probability for SOCP capacity constraint. ε = 0.10 means the capacity guarantee holds 90% of the time.' },
                    { term: 'Batch / Epoch',     def: 'One scheduling round. New jobs arrive, the real-time optimizer runs, jobs are placed or return to queue. Duration set by Batch Duration (s).' },
                    { term: 'Max Jobs / Solve',  def: 'How many jobs from the front of the queue are passed to the MILP solver per batch. 0 = send all queued jobs at once.' },
                  ] as { term: string; def: string }[]).map(({ term, def }) => (
                    <div key={term}>
                      <span className="font-bold text-slate-300">{term}</span>
                      <span className="text-slate-500"> — {def}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Config — dedicated gear button */}
        <div className="relative" ref={configRef}>
          <button
            onClick={() => setShowConfig((v) => !v)}
            className={`flex items-center gap-1 px-2 py-1.5 rounded text-xs border transition-colors
              ${showConfig ? 'border-slate-500 text-white bg-slate-800' : 'border-slate-700 text-slate-500 hover:text-white'}`}
          >
            <Settings size={10} />
          </button>

          {showConfig && (
            <div className="absolute bottom-full mb-2 right-0 w-80 bg-slate-800 border border-slate-700 rounded-lg shadow-xl p-3 z-50 max-h-[80vh] overflow-y-auto">
              <div className="text-[11px] font-bold text-slate-300 mb-0.5 uppercase tracking-widest">Sim Config</div>
              <div className="text-[10px] text-slate-600 mb-1">Changes apply after Reset</div>

              <CfgSection title="Topology" rows={[
                { label: 'Num Nodes',            val: cfgNumNodes,         set: setCfgNumNodes,         key: 'num_nodes',         min: 1,    max: 20     },
                { label: 'Num Tenants',           val: cfgNumTenants,       set: setCfgNumTenants,       key: 'num_tenants',       min: 1,    max: 100    },
                { label: 'Node RAM Min (GB)',      val: cfgNodeMemMinGb,     set: setCfgNodeMemMinGb,     key: 'node_mem_min_gb',   min: 4,    max: 256    },
                { label: 'Node RAM Max (GB)',      val: cfgNodeMemMaxGb,     set: setCfgNodeMemMaxGb,     key: 'node_mem_max_gb',   min: 4,    max: 512    },
                { label: 'Node CPU Min (cores)',   val: cfgNodeCpuMin,       set: setCfgNodeCpuMin,       key: 'node_cpu_min',      min: 1,    max: 128    },
                { label: 'Node CPU Max (cores)',   val: cfgNodeCpuMax,       set: setCfgNodeCpuMax,       key: 'node_cpu_max',      min: 1,    max: 256    },
              ]} />

              <CfgSection title="Workload" rows={[
                { label: 'Jobs Min / Round',       val: cfgJobsMinPerRound,  set: setCfgJobsMinPerRound,  key: 'jobs_min_per_round', min: 1,   max: 200    },
                { label: 'Jobs Max / Round',       val: cfgJobsMaxPerRound,  set: setCfgJobsMaxPerRound,  key: 'jobs_max_per_round', min: 1,   max: 200    },
                { label: 'Job RAM Min (MB)',        val: cfgReqMemMinMb,      set: setCfgReqMemMinMb,      key: 'req_mem_min_mb',    min: 64,   max: 8192   },
                { label: 'Job RAM Max (MB)',        val: cfgReqMemMaxMb,      set: setCfgReqMemMaxMb,      key: 'req_mem_max_mb',    min: 64,   max: 16384  },
                { label: 'Job CPU Min (cores)',     val: cfgReqCpuMin,        set: setCfgReqCpuMin,        key: 'req_cpu_min',       min: 0.1,  max: 16,    step: 0.1, isFloat: true },
                { label: 'Job CPU Max (cores)',     val: cfgReqCpuMax,        set: setCfgReqCpuMax,        key: 'req_cpu_max',       min: 0.1,  max: 32,    step: 0.1, isFloat: true },
                { label: 'Spike Prob %',            val: cfgSpikeProb,        set: setCfgSpikeProb,        key: 'spike_prob_pct',    min: 0,    max: 100    },
                { label: 'Min Lifetime (s)',         val: cfgMinLifetimeSec,   set: setCfgMinLifetimeSec,   key: 'min_lifetime_sec',  min: 10,   max: 3600   },
                { label: 'Max Lifetime (s)',         val: cfgMaxLifetimeSec,   set: setCfgMaxLifetimeSec,   key: 'max_lifetime_sec',  min: 10,   max: 7200   },
              ]} />

              <CfgSection title="Scheduler" rows={[
                { label: 'Batch Duration (s)',      val: cfgBatchDurationSec, set: setCfgBatchDurationSec, key: 'batch_duration_sec', min: 1,   max: 3600   },
                { label: 'Max Jobs / Solve (0=all)', val: cfgMaxJobsPerSolve,  set: setCfgMaxJobsPerSolve,  key: 'max_jobs_per_solve', min: 0,   max: 1000   },
                { label: 'K Window',                val: cfgKWindow,          set: setCfgKWindow,          key: 'k_window',           min: 1,   max: 50     },
                { label: 'Safety Buffer',           val: cfgMemThresholdFrac, set: setCfgMemThresholdFrac, key: 'mem_threshold_frac', min: 0.01,max: 0.5,   step: 0.01, isFloat: true },
              ]} />

              <CfgSection title="Plan-Ahead" rows={[
                { label: 'Horizon (intervals)',     val: cfgPlanAheadInterval, set: setCfgPlanAheadInterval, key: 'plan_ahead_interval', min: 5,    max: 500                         },
                { label: 'Period Width (intervals)',val: cfgAccessPeriod,      set: setCfgAccessPeriod,      key: 'access_period',       min: 1,    max: 50                          },
                { label: 'Usage Min (cap units)',   val: cfgTenantUsageMin,    set: setCfgTenantUsageMin,    key: 'tenant_usage_min',    min: 0.1,  max: 10,   step: 0.1, isFloat: true },
                { label: 'Usage Max (cap units)',   val: cfgTenantUsageMax,    set: setCfgTenantUsageMax,    key: 'tenant_usage_max',    min: 0.1,  max: 10,   step: 0.1, isFloat: true },
                { label: 'Gurobi Time Limit (s)',   val: cfgPlanTimeLimit,     set: setCfgPlanTimeLimit,     key: 'plan_time_limit',     min: 5,    max: 300                          },
                { label: 'Gurobi MIP Gap',          val: cfgPlanMipGap,        set: setCfgPlanMipGap,        key: 'plan_mip_gap',        min: 0.001,max: 0.5,  step: 0.001, isFloat: true  },
                { label: 'Priority Boost',          val: cfgPriorityBoost,     set: setCfgPriorityBoost,     key: 'priority_boost',      min: 1.0,  max: 100,  step: 0.5,   isFloat: true  },
              ]} />

              {/* SOCP toggle — lives outside CfgSection since it's boolean */}
              <div className="flex items-center justify-between gap-2 text-[11px] mt-1.5">
                <span className="text-slate-400 shrink-0">Capacity Model</span>
                <button
                  onClick={() => {
                    const next = cfgUseSocp === 0 ? 1 : 0
                    setCfgUseSocp(next)
                    postConfig({ use_socp: next })
                  }}
                  className={`px-2 py-0.5 rounded text-[10px] font-bold border transition-colors
                    ${cfgUseSocp === 1
                      ? 'border-purple-600 text-purple-300 bg-purple-950'
                      : 'border-slate-600 text-slate-400 hover:border-slate-500'}`}
                >
                  {cfgUseSocp === 1 ? 'SOCP' : 'MILP'}
                </button>
              </div>
              {cfgUseSocp === 1 && (
                <CfgSection title="SOCP Parameters" rows={[
                  { label: 'Demand Sigma Frac',     val: cfgSigmaFrac,         set: setCfgSigmaFrac,         key: 'sigma_frac',          min: 0.0,  max: 1.0,  step: 0.05,  isFloat: true  },
                  { label: 'Cantelli ε',            val: cfgCantelliEpsilon,   set: setCfgCantelliEpsilon,   key: 'cantelli_epsilon',    min: 0.01, max: 0.5,  step: 0.01,  isFloat: true  },
                ]} />
              )}

              {/* Frontend-only thresholds */}
              <div className="mt-3 pt-2 border-t border-slate-700">
                <div className="text-[10px] text-slate-500 mb-1.5">Color Thresholds (live)</div>
                <div className="space-y-1.5 text-[11px]">
                  {([
                    { label: 'Act green ≤', val: actGreenThreshold, set: setActGreenThreshold, min: 50, max: 100 },
                    { label: 'Eff green ≤', val: effGreenThreshold, set: setEffGreenThreshold, min: 50, max: 100 },
                    { label: 'Cap green ≤', val: capGreenThreshold,  set: setCapGreenThreshold, min: 50, max: 100 },
                  ] as const).map(({ label, val, set, min, max }) => (
                    <div key={label} className="flex items-center justify-between gap-2">
                      <span className="text-slate-400">{label}</span>
                      <div className="flex items-center gap-1">
                        <input
                          type="number" min={min} max={max} value={val}
                          onChange={(e) => { const v = parseInt(e.target.value); if (!isNaN(v)) (set as (n: number) => void)(v) }}
                          className="w-14 bg-slate-700 border border-slate-600 rounded px-1.5 py-0.5 text-xs text-white tabular-nums text-right focus:outline-none"
                        />
                        <span className="text-slate-600">%</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="text-[11px] text-slate-600 tabular-nums ml-1">
          #<span className="text-slate-400">{state.interval}</span>
        </div>

        {error && <span className="text-red-400 text-xs">{error}</span>}

        <button
          onClick={handleReset}
          className="ml-auto flex items-center gap-1 px-2 py-1.5 rounded text-xs text-slate-600 hover:text-red-400 hover:bg-slate-800 transition-colors"
        >
          <RotateCcw size={11} /> Reset
        </button>
      </div>

      <AnimatePresence>
        {showPlanAhead && planAheadData && (
          <PlanAheadOverlay
            result={planAheadData}
            numNodes={state.nodes.length}
            onClose={() => setShowPlanAhead(false)}
          />
        )}
      </AnimatePresence>
    </div>
  )
}
