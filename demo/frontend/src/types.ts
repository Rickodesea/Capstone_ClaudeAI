export interface QueuedJob {
  job_id: string
  tenant_id: number
  req_mem_mb: number
  pred_mem_mb: number
  req_cpu: number
  arrival_interval: number
  wait_intervals: number
}

export interface RunningJobInfo {
  job_id: string
  tenant_id: number
  act_mem_mb: number
  is_spike: boolean
}

export interface NodeInfo {
  node_id: number
  capacity_mb: number
  os_tax_mb: number
  cpu_cores: number
  used_mb: number
  m_cap: number
  mem_pct: number
  eff_pct: number
  violation_rate: number
  viols_count: number   // used_mb > m_cap (soft / schedulable ceiling)
  pme_count: number     // used_mb > capacity_mb (physical memory exceeded)
  running_jobs: RunningJobInfo[]
}

export interface PlacedJob {
  job_id: string
  tenant_id: number
  node_id: number
  pred_mem_mb: number
}

export interface PlanAheadResult {
  interval: number
  num_slots: number
  access_period: number
  planning_horizon: number
  slot_labels: string[]
  tenant_schedule: Record<string, Record<string, number[]>>
  current_slot: number
  summary: {
    avg_nodes_per_tenant: number
    isolation_score: number
    week_number: number
  }
}

export interface TenantInfo {
  tenant_id: number
  avg_wait_sec: number
  active_node_ids: number[]
  authorized_nodes: number[]
}

export interface HUDData {
  total_jobs: number
  total_tenants: number
  total_nodes: number
  mem_utilization_pct: number
  longest_wait_intervals: number
  intervals_to_plan_ahead: number
}

export interface SimState {
  interval: number
  plan_ahead_interval: number
  sim_time: string
  queue: QueuedJob[]
  nodes: NodeInfo[]
  recent_placements: PlacedJob[]
  plan_ahead: PlanAheadResult | null
  hud: HUDData
  mem_history: number[]
  eff_history: number[]
  placed_history: number[]
  tenants: TenantInfo[]
}

export const TENANT_COLORS: Record<number, string> = {
  0: '#22c55e',
  1: '#3b82f6',
  2: '#f59e0b',
  3: '#a855f7',
  4: '#ec4899',
}

export const TENANT_NAMES: Record<number, string> = {
  0: 'T-1A',
  1: 'T-1B',
  2: 'T-1C',
  3: 'T-1D',
  4: 'T-1E',
}
