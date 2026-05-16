import { useState, useEffect, useRef, useCallback } from 'react'
import { AnimatePresence } from 'framer-motion'
import type { SimState } from './types'
import { api } from './api'
import { HUD } from './components/HUD'
import { JobQueue } from './components/JobQueue'
import { NodeGrid } from './components/NodeGrid'
import { MemoryWave } from './components/MemoryWave'
import { PlacedHistoryChart } from './components/PlacedHistoryChart'
import { PlanAheadOverlay } from './components/PlanAheadOverlay'
import { Play, Pause, RotateCcw, Zap, ChevronUp } from 'lucide-react'

// ── Retry helpers ─────────────────────────────────────────────────────────────
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

// ── App ───────────────────────────────────────────────────────────────────────
export default function App() {
  const [state,         setState]         = useState<SimState | null>(null)
  const [isRunning,     setIsRunning]     = useState(false)
  const [baseSeconds,   setBaseSeconds]   = useState(1)
  const [multiplier,    setMultiplier]    = useState(1)
  const [useEffective,  setUseEffective]  = useState(false)
  const [showPlanAhead, setShowPlanAhead] = useState(false)
  const [planAheadData, setPlanAheadData] = useState<SimState['plan_ahead']>(null)
  const [showMore,      setShowMore]      = useState(false)
  const [error,         setError]         = useState<string | null>(null)
  const [recentNodeIds, setRecentNodeIds] = useState<Set<number>>(new Set())
  const [recentJobIds,  setRecentJobIds]  = useState<Set<string>>(new Set())
  const [loading,       setLoading]       = useState(true)

  const timerRef   = useRef<ReturnType<typeof setInterval> | null>(null)
  const paTimer    = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isStepping = useRef(false)
  const moreRef    = useRef<HTMLDivElement>(null)

  const stepMs = Math.max(50, (baseSeconds * 1000) / multiplier)

  // ── Initial load with retry ─────────────────────────────────────────────
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

  // ── Click-outside to close More popup ────────────────────────────────────
  useEffect(() => {
    if (!showMore) return
    const handler = (e: MouseEvent) => {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) {
        setShowMore(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showMore])

  // ── Single step ──────────────────────────────────────────────────────────
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
        setTimeout(() => {
          setRecentNodeIds(new Set())
          setRecentJobIds(new Set())
        }, 700)
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

  // ── Auto-run loop ────────────────────────────────────────────────────────
  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current)
    if (!isRunning) return
    timerRef.current = setInterval(advance, stepMs)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [isRunning, stepMs, advance])

  // ── Controls ─────────────────────────────────────────────────────────────
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

  const handleStep = () => {
    setIsRunning(false)
    advance()
  }

  // ── Loading / error screens ───────────────────────────────────────────────
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
        >
          Retry
        </button>
      </div>
    )
  }

  // ── Main layout ──────────────────────────────────────────────────────────
  return (
    <div className="h-screen flex flex-col bg-slate-950 overflow-hidden select-none">

      {/* ── Top charts bar ────────────────────────────────────────────── */}
      <div className="shrink-0 h-32 border-b border-slate-800 flex">
        <div className="flex-1 min-w-0 border-r border-slate-800">
          <MemoryWave history={state.mem_history} />
        </div>
        <div className="w-64 shrink-0">
          <PlacedHistoryChart history={state.placed_history} />
        </div>
      </div>

      {/* ── Main content: queue + nodes + HUD ───────────────────────── */}
      <div className="flex flex-1 min-h-0">

        {/* Job queue */}
        <div className="w-52 shrink-0 border-r border-slate-800 flex flex-col min-h-0">
          <JobQueue queue={state.queue} recentPlacements={recentJobIds} />
        </div>

        {/* Cluster nodes */}
        <div className="flex-1 min-h-0 flex flex-col">
          <div className="shrink-0 flex items-center justify-between px-3 py-1.5 border-b border-slate-800">
            <span className="text-[11px] font-bold text-slate-300 uppercase tracking-widest">
              Cluster Nodes
            </span>
            <span className="text-[11px] text-slate-500">
              {state.nodes.filter((n) => n.running_jobs.length > 0).length}/{state.nodes.length} active
            </span>
          </div>
          <div className="flex-1 min-h-0">
            <NodeGrid
              nodes={state.nodes}
              newJobNodeIds={recentNodeIds}
              useEffective={useEffective}
            />
          </div>
        </div>

        {/* HUD — docked right of cluster nodes */}
        <div className="border-l border-slate-800 shrink-0">
          <HUD hud={state.hud} interval={state.interval} />
        </div>
      </div>

      {/* ── Controls bar ─────────────────────────────────────────────── */}
      <div className="shrink-0 border-t border-slate-800 bg-slate-900 px-3 py-2 flex items-center gap-2">

        {/* Play / Pause */}
        <button
          onClick={() => setIsRunning((r) => !r)}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-bold transition-colors
            ${isRunning
              ? 'bg-slate-700 hover:bg-slate-600 text-white'
              : 'bg-emerald-600 hover:bg-emerald-500 text-white'}`}
        >
          {isRunning ? <><Pause size={11} /> Pause</> : <><Play size={11} /> Play</>}
        </button>

        {/* Step once */}
        <button
          onClick={handleStep}
          className="flex items-center gap-1 px-2 py-1.5 rounded text-xs text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
        >
          <Zap size={11} /> Step
        </button>

        <div className="w-px h-4 bg-slate-700" />

        {/* Base-seconds text input + multiplier buttons */}
        <div className="flex items-center gap-1">
          <input
            type="number"
            min={0.1}
            step={0.5}
            value={baseSeconds}
            onChange={(e) => {
              const v = parseFloat(e.target.value)
              if (!isNaN(v) && v > 0) setBaseSeconds(v)
            }}
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

        {/* Cap Util / Eff Util toggle */}
        <button
          onClick={() => setUseEffective((e) => !e)}
          className={`px-2 py-1.5 rounded text-xs border transition-colors
            ${useEffective
              ? 'border-sky-600 text-sky-400 bg-sky-950'
              : 'border-slate-700 text-slate-500 hover:text-white'}`}
          title="Toggle between capacity utilization (used/RAM) and effective utilization (used/schedulable)"
        >
          {useEffective ? 'Eff Util' : 'Cap Util'}
        </button>

        {/* Plan Ahead button */}
        <button
          onClick={() => { if (planAheadData) setShowPlanAhead(true) }}
          disabled={!planAheadData}
          className={`px-2 py-1.5 rounded text-xs border transition-colors
            ${planAheadData
              ? 'border-amber-700 text-amber-400 hover:bg-amber-950'
              : 'border-slate-800 text-slate-700 cursor-not-allowed'}`}
        >
          Plan Ahead
        </button>

        {/* More button with popup */}
        <div className="relative" ref={moreRef}>
          <button
            onClick={() => setShowMore((v) => !v)}
            className={`flex items-center gap-1 px-2 py-1.5 rounded text-xs border transition-colors
              ${showMore
                ? 'border-slate-500 text-white bg-slate-800'
                : 'border-slate-700 text-slate-500 hover:text-white'}`}
          >
            More <ChevronUp size={10} className={`transition-transform ${showMore ? '' : 'rotate-180'}`} />
          </button>

          {showMore && (
            <div className="absolute bottom-full mb-2 left-0 w-52 bg-slate-800 border border-slate-700 rounded-lg shadow-xl p-3 z-50">
              <div className="text-[11px] font-bold text-slate-300 mb-2 uppercase tracking-widest">
                Sim Settings
              </div>
              <div className="space-y-2 text-[11px] text-slate-500">
                <div className="flex justify-between items-center">
                  <span>Nodes</span>
                  <span className="text-slate-400 tabular-nums">{state.nodes.length}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span>Tenants</span>
                  <span className="text-slate-400 tabular-nums">{state.hud.total_tenants}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span>Plan-Ahead Interval</span>
                  <span className="text-slate-400 tabular-nums">{state.plan_ahead_interval}i</span>
                </div>
                <div className="border-t border-slate-700 pt-2 text-slate-600 text-[10px]">
                  Advanced config coming soon
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Interval counter */}
        <div className="text-[11px] text-slate-600 tabular-nums ml-1">
          #<span className="text-slate-400">{state.interval}</span>
        </div>

        {/* Error message */}
        {error && <span className="text-red-400 text-xs">{error}</span>}

        {/* Reset */}
        <button
          onClick={handleReset}
          className="ml-auto flex items-center gap-1 px-2 py-1.5 rounded text-xs text-slate-600 hover:text-red-400 hover:bg-slate-800 transition-colors"
        >
          <RotateCcw size={11} /> Reset
        </button>
      </div>

      {/* ── Plan-Ahead overlay ────────────────────────────────────────── */}
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
