import { describe, expect, it, vi } from 'vitest'

import { ToolRuntime, type ToolDefinition } from '../src/index.ts'

function tool(name: string): ToolDefinition {
  return {
    name,
    description: name,
    risk: 'read',
    input_schema: { type: 'object', additionalProperties: false },
    execute: vi.fn(async () => name),
  }
}

describe('ToolRuntime registration ownership', () => {
  it('returns an idempotent disposer without removing a later same-name registration', async () => {
    const runtime = new ToolRuntime()
    const disposeFirst = runtime.register(tool('repeatable'))
    expect(() => runtime.register(tool('repeatable'))).toThrow('tool already registered: repeatable')

    disposeFirst()
    disposeFirst()
    const second = tool('repeatable')
    const disposeSecond = runtime.register(second)
    disposeFirst()

    expect(runtime.get('repeatable')).toBe(second)
    expect(runtime.list()).toHaveLength(1)
    disposeSecond()
    expect(() => runtime.get('repeatable')).toThrow('unknown tool: repeatable')
    await expect(runtime.execute('repeatable', {})).rejects.toThrow('unknown tool: repeatable')
  })
})
