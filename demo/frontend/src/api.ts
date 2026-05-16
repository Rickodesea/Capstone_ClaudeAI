import type { SimState } from './types'

const BASE = '/api'

async function request<T>(path: string, method = 'GET'): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
  })
  if (!res.ok) throw new Error(`API ${method} ${path} → ${res.status}`)
  return res.json() as Promise<T>
}

export const api = {
  getState: () => request<SimState>('/state'),
  step:     () => request<SimState>('/step', 'POST'),
  reset:    () => request<SimState>('/reset', 'POST'),
}
