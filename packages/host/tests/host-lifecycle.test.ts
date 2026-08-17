import { describe, expect, it, vi } from 'vitest'

import {
  DoAIHost,
  HostDiagnosticError,
  type CapabilityPolicy,
  type DoAIPlugin,
} from '../src/index.ts'

const policies: Record<string, CapabilityPolicy> = {
  storage: 'exactly_one',
  cards: 'many',
}

function plugin(
  name: string,
  options: {
    dependencies?: Record<string, string>
    requires?: string[]
    provides?: string[]
    apply: DoAIPlugin['apply']
  },
): DoAIPlugin {
  return {
    manifest: {
      name,
      version: '1.0.0',
      api_version: '1',
      dependencies: options.dependencies ?? {},
      requires: options.requires ?? [],
      provides: options.provides ?? [],
      config_schema: { type: 'object', additionalProperties: false },
      permissions: [],
    },
    apply: options.apply,
  }
}

describe('DoAIHost lifecycle', () => {
  it('loads dependencies first and resolves the active provider', async () => {
    const order: string[] = []
    const storage = plugin('storage-plugin', {
      provides: ['storage'],
      apply(ctx) { order.push('storage'); ctx.provide('storage', { kind: 'memory' }) },
    })
    const consumer = plugin('consumer', {
      dependencies: { 'storage-plugin': '^1.0.0' },
      requires: ['storage'],
      apply(ctx) { order.push(`consumer:${ctx.resolve<{ kind: string }>('storage').kind}`) },
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(storage)
    host.register(consumer)

    await host.activate([{ plugin: 'consumer' }, { plugin: 'storage-plugin' }])

    expect(order).toEqual(['storage', 'consumer:memory'])
    expect(host.resolve<{ kind: string }>('storage')).toEqual({ kind: 'memory' })
    await host.dispose()
  })

  it('keeps the old scope active when shadow activation fails', async () => {
    const disposeOld = vi.fn()
    const stable = plugin('stable', {
      provides: ['storage'],
      apply(ctx) {
        ctx.provide('storage', 'old')
        ctx.effect(() => disposeOld, 'stable resource')
      },
    })
    const broken = plugin('broken', {
      provides: ['storage'],
      apply() { throw new Error('boom') },
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(stable)
    host.register(broken)
    await host.activate([{ plugin: 'stable' }])
    expect(host.inspect().epoch).toBe(1)

    await expect(host.activate([{ plugin: 'broken' }])).rejects.toThrow('boom')

    expect(host.resolve('storage')).toBe('old')
    expect(host.inspect().epoch).toBe(1)
    expect(disposeOld).not.toHaveBeenCalled()
    await host.dispose()
    expect(disposeOld).toHaveBeenCalledOnce()
  })

  it('diagnoses missing capabilities, duplicate providers, and bad config', async () => {
    const missing = plugin('missing-consumer', { requires: ['storage'], apply() {} })
    const first = plugin('first', { provides: ['storage'], apply(ctx) { ctx.provide('storage', 1) } })
    const second = plugin('second', { provides: ['storage'], apply(ctx) { ctx.provide('storage', 2) } })
    const configured: DoAIPlugin = {
      manifest: {
        name: 'configured', version: '1.0.0', api_version: '1', dependencies: {}, requires: [], provides: [], permissions: [],
        config_schema: { type: 'object', additionalProperties: false, required: ['enabled'], properties: { enabled: { type: 'boolean' } } },
      },
      apply() {},
    }
    const host = new DoAIHost({ capabilityPolicies: policies })
    for (const item of [missing, first, second, configured]) host.register(item)

    await expect(host.activate([{ plugin: 'missing-consumer' }])).rejects.toMatchObject({ code: 'CAPABILITY_MISSING' })
    await expect(host.activate([{ plugin: 'first' }, { plugin: 'second' }])).rejects.toMatchObject({ code: 'PROVIDER_CONFLICT' })
    await expect(host.activate([{ plugin: 'configured', config: { enabled: 'yes' } }])).rejects.toMatchObject({ code: 'CONFIG_INVALID' })
    await host.dispose()
  })

  it('returns all owned resources to baseline after 100 load/unload cycles', async () => {
    let live = 0
    const disposable = plugin('disposable', {
      provides: ['storage'],
      apply(ctx) {
        live += 1
        ctx.provide('storage', {})
        ctx.effect(() => () => { live -= 1 }, 'counter')
      },
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(disposable)

    for (let index = 0; index < 100; index += 1) {
      await host.activate([{ plugin: 'disposable' }])
      await host.deactivate()
      expect(host.inspect()).toMatchObject({ active: false, providers: 0, effects: 0 })
    }

    expect(live).toBe(0)
    await host.dispose()
  })

  it('rejects an incompatible plugin dependency version', async () => {
    const base = plugin('base', { apply() {} })
    const dependent = plugin('dependent', {
      dependencies: { base: '^2.0.0' },
      apply() {},
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(base)
    host.register(dependent)

    await expect(host.activate([{ plugin: 'dependent' }, { plugin: 'base' }]))
      .rejects.toBeInstanceOf(HostDiagnosticError)
    await host.dispose()
  })

  it('reports a missing plugin dependency before starting any fiber', async () => {
    const apply = vi.fn()
    const dependent = plugin('dependent', {
      dependencies: { absent: '^1.0.0' },
      apply,
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(dependent)

    await expect(host.activate([{ plugin: 'dependent' }])).rejects.toMatchObject({
      code: 'DEPENDENCY_MISSING',
      plugin: 'dependent',
    })
    expect(apply).not.toHaveBeenCalled()
    await host.dispose()
  })

  it('rejects capabilities outside the host catalog at registration', () => {
    const host = new DoAIHost({ capabilityPolicies: policies })
    const unknown = plugin('unknown', { provides: ['mystery'], apply() {} })

    expect(() => host.register(unknown)).toThrow(expect.objectContaining({
      code: 'CAPABILITY_UNKNOWN',
      plugin: 'unknown',
    }))
  })
})
