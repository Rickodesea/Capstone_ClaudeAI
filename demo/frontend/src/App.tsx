import { useState, useEffect, useRef, useCallback } from 'react'
import { AnimatePresence } from 'framer-motion'
import type { SimState } from './types'
import { TENANT_COLORS, TENANT_NAMES } from './types'
import { api } from './api'
import { HUD } from './components/HUD'
import { JobQueue } from './components/JobQueue'
import { NodeGrid } from './components/NodeGrid'
import { MemoryWave } from './components/MemoryWave'
import { PlanAheadOverlay } from './components/PlanAheadOverlay'
import { Play, Pause, RotateCcw, Zap, ChevronUp, Users } from 'lucide-react'

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

const MULTIPLIERS = [1, 4, 10]

export default function App() {
  const [state,         setState]         = useState<SimState | null>(null)
  const [isRunning,     setIsRunning]     = useState(false)
  const [baseSeconds,   setBaseSeconds]   = useState(1)
  const [multiplier,    setMultiplier]    = useState(1)
  const [useEffective,  setUseEffective]  = useState(false)
  const [showPlanAhead, setShowPlanAhead] = useState(false)
  const [planAheadData, setPlanAheadData] = useState<SimState['plan_ahead']>(null)
  const [showMore,      setShowMore]      = useState(false)
  const [showTenants,   setShowTenants]   = useState(false)
  const [error,         setError]         = useState<string | null>(null)
  const [recentNodeIds, setRecentNodeIds] = useState<Set<number>>(new Set())
  const [recentJobIds,  setRecentJobIds]  = useState<Set<string>>(new Set())
  const [loading,       setLoading]       = useState(true)

  // Backend config (sent to backend via /api/config, applied on reset)
  const [cfgJobsPerRound,        setCfgJobsPerRound]        = useState(20)
  const [cfgSpikeProb,           setCfgSpikeProb]           = useState(10)
  const [cfgKWindow,             setCfgKWindow]             = useState(10)
  const [cfgPlanAheadInterval,   setCfgPlanAheadInterval]   = useState(50)
  const [cfgAccessPeriod,        setCfgAccessPeriod]        = useState(4)

  // Frontend-only color thresholds
  const [effGreenThreshold, setEffGreenThreshold] = useState(95)
  const [capGreenThreshold, setCapGreenThreshold] = useState(85)

  const timerRef    = useRef<ReturnType<typeof setInterval> | null>(null)
  const paTimer     = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isStepping  = useRef(false)
  const moreRef     = useRef<HTMLDivElement>(null)
  const tenantsRef  = useRef<HTMLDivElement>(null)

  const stepMs = Math.max(50, (baseSeconds * 1000) / multiplier)

  useEffect(() => {
    let cancelled = false
    fetchWithRetry(() => api.getState())
      .then((s) => {
        if (!cancelled) {
          setState(s)
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

  useEffect(() => {
    if (!showMore) return
    const h = (e: MouseEvent) => {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) setShowMore(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [showMore])

  useEffect(() => {
    if (!showTenants) return
    const h = (e: MouseEvent) => {
      if (tenantsRef.current && !tenantsRef.current.contains(e.target as Node)) setShowTenants(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [showTenants])

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

  const postConfig = async (patch: Record<string, number>) => {
    try {
      await fetch('http://localhost:8000/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
    } catch { /* backend may be unreachable */ }
  }

  const handleReset = async () => {
    setIsRunning(false)
    isStepping.current = false
    const fresh = await api.reset()
    setState(fresh)
    if (fresh.plan_ahead) {
      setPlanAheadData(fresh.plan_ahead)
      setShowPlanAhead(true)
      if (paTimer.current) clearTimeout(paTimer.current)
      paTimer.current = setTimeout(() => setShowPlanAhead(false), 8000)
    }
    setError(null)
  }

  const handleStep = () => { setIsRunning(false); advance() }

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
          cd demo/api &amp;&amp; uvicorn main:app --reload --port 8000
        </div>
        <button
          onClick={() => { setLoading(true); setError(null); window.location.reload() }}
          className="text-xs text-slate-500 hover:text-white mt-2"
        >Retry</button>
      </div>
    )
  }

  const activeNodes = state.nodes.filter((n) => n.running_jobs.length > 0).length

  return (
    <div className="h-screen flex flex-col bg-slate-950 overflow-hidden select-none">

      {/* ── Top: Memory wave (full width) ────────────────────────────── */}
      <div className="shrink-0 h-28 border-b border-slate-800">
        <MemoryWave history={state.mem_history} effHistory={state.eff_history} />
      </div>

      {/* ── Shared section-header row ─────────────────────────────────── */}
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

      {/* ── Main content ──────────────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0">

        {/* Job queue */}
        <div className="w-52 shrink-0 border-r border-slate-800 flex flex-col min-h-0">
          <JobQueue queue={state.queue} recentPlacements={recentJobIds} />
        </div>

        {/* Cluster nodes */}
        <div className="flex-1 min-h-0">
          <NodeGrid
            nodes={state.nodes}
            newJobNodeIds={recentNodeIds}
            useEffective={useEffective}
            effGreenThreshold={effGreenThreshold}
            capGreenThreshold={capGreenThreshold}
          />
        </div>

        {/* Summary card */}
        <div className="border-l border-slate-800 shrink-0 flex flex-col">
          <HUD hud={state.hud} interval={state.interval} planAheadInterval={state.plan_ahead_interval} />
        </div>
      </div>

      {/* ── Controls bar ──────────────────────────────────────────────── */}
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
            type="number" min={0.1} step={0.5} value={baseSeconds}
            onChange={(e) => { const v = parseFloat(e.target.value); if (!isNaN(v) && v > 0) setBaseSeconds(v) }}
            className="w-14 bg-slate-800 border border-slate-700 rounded px-1.5 py-1 text-xs text-white tabular-nums text-right focus:outline-none focus:border-slate-500"
          />
          <span className="text-[11px] text-slate-500">s</span>
        </div>

        <div className="flex items-center gap-0 border border-slate-700 rounded overflow-hidden">
          {MULTIPLIERS.map((m) => (
            <button
              key={m}
              onClick={() => setMultiplier(m)}
              className={`px-2 py-1 text-xs transition-colors border-r border-slate-700 last:border-0
                ${multiplier === m ? 'bg-slate-700 text-white font-bold' : 'text-slate-500 hover:text-white'}`}
            >
              ×{m}
            </button>
          ))}
        </div>

        <div className="w-px h-4 bg-slate-700" />

        <button
          onClick={() => setUseEffective((e) => !e)}
          className={`px-2 py-1.5 rounded text-xs border transition-colors
            ${useEffective ? 'border-sky-600 text-sky-400 bg-sky-950' : 'border-slate-700 text-slate-500 hover:text-white'}`}
        >
          {useEffective ? 'Eff Util' : 'Cap Util'}
        </button>

        <button
          onClick={() => { if (planAheadData) setShowPlanAhead(true) }}
          disabled={!planAheadData}
          className={`px-2 py-1.5 rounded text-xs border transition-colors
            ${planAheadData ? 'border-amber-700 text-amber-400 hover:bg-amber-950' : 'border-slate-800 text-slate-700 cursor-not-allowed'}`}
        >
          Plan Ahead
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
              <div className="text-[11px] font-bold text-slate-300 mb-2 uppercase tracking-widest">Tenants — Current Period</div>
              <div className="space-y-2">
                {state.tenants.map((t) => {
                  const color = TENANT_COLORS[t.tenant_id] ?? '#94a3b8'
                  const name  = TENANT_NAMES[t.tenant_id] ?? `T${t.tenant_id}`
                  return (
                    <div key={t.tenant_id} className="bg-slate-900 rounded p-2 text-[11px]">
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-bold" style={{ color }}>{name}</span>
                        <span className="text-slate-500">wait: {t.avg_wait_sec.toFixed(1)}s</span>
                      </div>
                      <div className="text-slate-600">
                        Auth. nodes (plan):&nbsp;
                        <span className="text-slate-400">
                          {t.authorized_nodes.length > 0 ? t.authorized_nodes.map((n) => `N${n}`).join(', ') : '—'}
                        </span>
                      </div>
                      <div className="text-slate-600">
                        Active nodes (now):&nbsp;
                        <span className="text-slate-400">
                          {t.active_node_ids.length > 0 ? t.active_node_ids.map((n) => `N${n}`).join(', ') : '—'}
                        </span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>

        {/* More button */}
        <div className="relative" ref={moreRef}>
          <button
            onClick={() => setShowMore((v) => !v)}
            className={`flex items-center gap-1 px-2 py-1.5 rounded text-xs border transition-colors
              ${showMore ? 'border-slate-500 text-white bg-slate-800' : 'border-slate-700 text-slate-500 hover:text-white'}`}
          >
            More <ChevronUp size={10} className={`transition-transform ${showMore ? '' : 'rotate-180'}`} />
          </button>

          {showMore && (
            <div className="absolute bottom-full mb-2 right-0 w-72 bg-slate-800 border border-slate-700 rounded-lg shadow-xl p-3 z-50">
              {/* Backend sim config */}
              <div className="text-[11px] font-bold text-slate-300 mb-1 uppercase tracking-widest">Sim Config</div>
              <div className="text-[10px] text-slate-600 mb-2">Changes apply after Reset</div>
              <div className="space-y-1.5 text-[11px]">
                {([
                  { label: 'Jobs / Round',         val: cfgJobsPerRound,      set: setCfgJobsPerRound,      key: 'jobs_per_round',      min: 1,   max: 200  },
                  { label: 'Spike Prob %',          val: cfgSpikeProb,         set: setCfgSpikeProb,         key: 'spike_prob_pct',      min: 0,   max: 100  },
                  { label: 'K Window',              val: cfgKWindow,           set: setCfgKWindow,           key: 'k_window',            min: 1,   max: 50   },
                  { label: 'Plan-Ahead Horizon',    val: cfgPlanAheadInterval, set: setCfgPlanAheadInterval, key: 'plan_ahead_interval', min: 5,   max: 500  },
                  { label: 'Access Period (i/slot)', val: cfgAccessPeriod,     set: setCfgAccessPeriod,      key: 'access_period',       min: 1,   max: 50   },
                ] as const).map(({ label, val, set, key, min, max }) => (
                  <div key={key} className="flex items-center justify-between gap-2">
                    <span className="text-slate-400 shrink-0">{label}</span>
                    <input
                      type="number" min={min} max={max} value={val}
                      onChange={(e) => {
                        const v = parseInt(e.target.value)
                        if (!isNaN(v)) { (set as (n: number) => void)(v); postConfig({ [key]: v }) }
                      }}
                      className="w-16 bg-slate-700 border border-slate-600 rounded px-1.5 py-0.5 text-xs text-white tabular-nums text-right focus:outline-none"
                    />
                  </div>
                ))}
              </div>

              {/* Frontend-only color thresholds */}
              <div className="mt-3 pt-2 border-t border-slate-700">
                <div className="text-[10px] text-slate-600 mb-1.5">Color Thresholds (live)</div>
                <div className="space-y-1.5 text-[11px]">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-slate-400">Eff green ≤</span>
                    <div className="flex items-center gap-1">
                      <input
                        type="number" min={50} max={100} value={effGreenThreshold}
                        onChange={(e) => { const v = parseInt(e.target.value); if (!isNaN(v)) setEffGreenThreshold(v) }}
                        className="w-14 bg-slate-700 border border-slate-600 rounded px-1.5 py-0.5 text-xs text-white tabular-nums text-right focus:outline-none"
                      />
                      <span className="text-slate-600">%</span>
                    </div>
                  </div>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-slate-400">Cap green ≤</span>
                    <div className="flex items-center gap-1">
                      <input
                        type="number" min={50} max={100} value={capGreenThreshold}
                        onChange={(e) => { const v = parseInt(e.target.value); if (!isNaN(v)) setCapGreenThreshold(v) }}
                        className="w-14 bg-slate-700 border border-slate-600 rounded px-1.5 py-0.5 text-xs text-white tabular-nums text-right focus:outline-none"
                      />
                      <span className="text-slate-600">%</span>
                    </div>
                  </div>
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
