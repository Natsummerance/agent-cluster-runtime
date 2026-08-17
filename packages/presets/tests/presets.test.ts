import { describe, expect, it } from 'vitest'

import { PRESETS, resolvePreset } from '../src/index.ts'

describe('agent presets', () => {
  it('publishes the four v1 presets with both Code runtimes', () => {
    expect(PRESETS.map((preset) => preset.id)).toEqual(['standard', 'code', 'minimal', 'creator'])
    expect(resolvePreset('code', { runtime: 'python' }).plugins).toContain('runtime-python')
    expect(resolvePreset('code', { runtime: 'typescript' }).plugins).toContain('runtime-typescript')
    expect(resolvePreset('minimal').plugins).not.toContain('tools-local')
  })

  it('fails loudly for an unknown preset or missing Code runtime', () => {
    expect(() => resolvePreset('unknown')).toThrow('unknown agent preset')
    expect(() => resolvePreset('code')).toThrow('requires runtime')
  })
})
