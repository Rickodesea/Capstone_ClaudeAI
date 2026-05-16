import '@testing-library/jest-dom'
import { vi } from 'vitest'

// Recharts uses ResizeObserver internally — mock it for jsdom
global.ResizeObserver = vi.fn().mockImplementation(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
}))

// SVG methods recharts calls that jsdom doesn't implement
Object.defineProperty(SVGElement.prototype, 'getTotalLength', {
  writable: true,
  value: vi.fn().mockReturnValue(0),
})
