/**
 * Frontend component smoke tests.
 *
 * Run with:
 *   cd demo/frontend
 *   npm test
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { HUD } from '../components/HUD'
import { MemoryWave } from '../components/MemoryWave'
import { JobQueue } from '../components/JobQueue'
import { NodeGrid } from '../components/NodeGrid'
import { PlacedHistoryChart } from '../components/PlacedHistoryChart'
import type { HUDData, QueuedJob, NodeInfo } from '../types'

// ── Fixtures ────────────────────────────────────────────────────────────────

const mockHUD: HUDData = {
  total_jobs:              25,
  total_tenants:           3,
  total_nodes:             5,
  mem_utilization_pct:     42.5,
  longest_wait_intervals:  3,
  intervals_to_plan_ahead: 47,
}

const mockQueue: QueuedJob[] = [
  { job_id: 'r1_j0', tenant_id: 0, req_mem_mb: 800, pred_mem_mb: 720, req_cpu: 1.5, arrival_interval: 0, wait_intervals: 4 },
  { job_id: 'r1_j1', tenant_id: 1, req_mem_mb: 512, pred_mem_mb: 490, req_cpu: 2.0, arrival_interval: 1, wait_intervals: 2 },
]

const mockNodes: NodeInfo[] = [
  {
    node_id: 0, capacity_mb: 16384, os_tax_mb: 1024, cpu_cores: 8,
    used_mb: 8000, m_cap: 14000, mem_pct: 48.8, eff_pct: 57.1,
    violation_rate: 0.0, viols_count: 0, pme_count: 0, running_jobs: [
      { job_id: 'r0_j0', tenant_id: 0, act_mem_mb: 700, is_spike: false },
    ],
  },
  {
    node_id: 1, capacity_mb: 32768, os_tax_mb: 2048, cpu_cores: 16,
    used_mb: 0, m_cap: 28000, mem_pct: 0, eff_pct: 0,
    violation_rate: 0.0, viols_count: 0, pme_count: 0, running_jobs: [],
  },
]

// ── HUD tests ────────────────────────────────────────────────────────────────

describe('HUD', () => {
  it('renders without crashing', () => {
    const { container } = render(<HUD hud={mockHUD} interval={5} planAheadInterval={50} />)
    expect(container.firstChild).toBeTruthy()
  })

  it('displays total job count', () => {
    render(<HUD hud={mockHUD} interval={5} planAheadInterval={50} />)
    expect(screen.getByText('25')).toBeInTheDocument()
  })

  it('displays memory percentage', () => {
    render(<HUD hud={mockHUD} interval={5} planAheadInterval={50} />)
    expect(screen.getByText('42.5')).toBeInTheDocument()
  })

  it('displays tenant count', () => {
    render(<HUD hud={mockHUD} interval={5} planAheadInterval={50} />)
    expect(screen.getByText('3')).toBeInTheDocument()
  })

  it('displays node count', () => {
    render(<HUD hud={mockHUD} interval={5} planAheadInterval={50} />)
    expect(screen.getByText('5')).toBeInTheDocument()
  })

  it('shows interval counter', () => {
    render(<HUD hud={mockHUD} interval={42} planAheadInterval={50} />)
    expect(screen.getByText('#42')).toBeInTheDocument()
  })
})

// ── MemoryWave tests ──────────────────────────────────────────────────────────

describe('MemoryWave', () => {
  it('renders with empty history', () => {
    const { container } = render(<MemoryWave history={[]} effHistory={[]} />)
    expect(container.firstChild).toBeTruthy()
  })

  it('renders with history data', () => {
    const { container } = render(<MemoryWave history={[10, 20, 30, 40, 50]} effHistory={[12, 22, 32, 42, 52]} />)
    expect(container.firstChild).toBeTruthy()
  })

  it('renders correctly at 100% utilization', () => {
    const { container } = render(<MemoryWave history={[100, 100, 100]} effHistory={[95, 95, 95]} />)
    expect(container.firstChild).toBeTruthy()
  })
})

// ── JobQueue tests ────────────────────────────────────────────────────────────

describe('JobQueue', () => {
  it('renders without crashing', () => {
    const { container } = render(
      <JobQueue queue={mockQueue} recentPlacements={new Set()} />
    )
    expect(container.firstChild).toBeTruthy()
  })

  it('shows job IDs', () => {
    render(<JobQueue queue={mockQueue} recentPlacements={new Set()} />)
    expect(screen.getByText('r1_j0')).toBeInTheDocument()
    expect(screen.getByText('r1_j1')).toBeInTheDocument()
  })

  it('shows empty state when queue is empty', () => {
    render(<JobQueue queue={[]} recentPlacements={new Set()} />)
    expect(screen.getByText('queue empty')).toBeInTheDocument()
  })

  it('shows longest wait label on oldest job', () => {
    render(<JobQueue queue={mockQueue} recentPlacements={new Set()} />)
    expect(screen.getByText('● LONGEST')).toBeInTheDocument()
  })
})

// ── NodeGrid tests ────────────────────────────────────────────────────────────

describe('NodeGrid', () => {
  it('renders without crashing', () => {
    const { container } = render(
      <NodeGrid nodes={mockNodes} newJobNodeIds={new Set()} useEffective={false} />
    )
    expect(container.firstChild).toBeTruthy()
  })

  it('renders all nodes', () => {
    render(
      <NodeGrid nodes={mockNodes} newJobNodeIds={new Set()} useEffective={false} />
    )
    expect(screen.getByText('Node 0')).toBeInTheDocument()
    expect(screen.getByText('Node 1')).toBeInTheDocument()
  })

  it('shows job count on node with jobs', () => {
    render(
      <NodeGrid nodes={mockNodes} newJobNodeIds={new Set()} useEffective={false} />
    )
    // Node 0 has 1 running job
    expect(screen.getByText('1')).toBeInTheDocument()
  })

  it('shows CPU cores', () => {
    render(
      <NodeGrid nodes={mockNodes} newJobNodeIds={new Set()} useEffective={false} />
    )
    expect(screen.getByText('8 cores')).toBeInTheDocument()
  })

  it('shows Memory Utilization label when useEffective=false', () => {
    render(
      <NodeGrid nodes={mockNodes} newJobNodeIds={new Set()} useEffective={false} />
    )
    const labels = screen.getAllByText('Memory Utilization')
    expect(labels.length).toBeGreaterThan(0)
  })

  it('shows Memory Utilization label when useEffective=true', () => {
    render(
      <NodeGrid nodes={mockNodes} newJobNodeIds={new Set()} useEffective={true} />
    )
    const labels = screen.getAllByText('Memory Utilization')
    expect(labels.length).toBeGreaterThan(0)
  })
})

// ── PlacedHistoryChart tests ──────────────────────────────────────────────────

describe('PlacedHistoryChart', () => {
  it('renders without crashing on empty history', () => {
    const { container } = render(<PlacedHistoryChart history={[]} />)
    expect(container.firstChild).toBeTruthy()
  })

  it('renders with history data', () => {
    const { container } = render(<PlacedHistoryChart history={[5, 10, 15, 20]} />)
    expect(container.firstChild).toBeTruthy()
  })

  it('shows title label', () => {
    render(<PlacedHistoryChart history={[5, 10]} />)
    expect(screen.getByText('Placed Jobs / Step')).toBeInTheDocument()
  })
})

// ── TENANT_COLORS / TENANT_NAMES exports ─────────────────────────────────────

describe('types exports', () => {
  it('TENANT_COLORS has entries for tenants 0-2', async () => {
    const { TENANT_COLORS } = await import('../types')
    expect(TENANT_COLORS[0]).toBeDefined()
    expect(TENANT_COLORS[1]).toBeDefined()
    expect(TENANT_COLORS[2]).toBeDefined()
  })

  it('TENANT_NAMES has entries for tenants 0-2', async () => {
    const { TENANT_NAMES } = await import('../types')
    expect(TENANT_NAMES[0]).toBeDefined()
    expect(typeof TENANT_NAMES[0]).toBe('string')
  })
})
