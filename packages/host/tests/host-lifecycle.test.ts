import { describe, expect, it, vi } from 'vitest'

import {
  DoAIHost,
  HostDiagnosticError,
  type CapabilityPolicy,
  type DoAIPlugin,
  type HostPluginContext,
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

  it('keeps shadow registries invisible and rolls them back when activation fails', async () => {
    const activeListener = vi.fn()
    const shadowListener = vi.fn()
    const activeInterceptor = vi.fn(async (_payload: unknown, next: () => Promise<string>) => `active:${await next()}`)
    const shadowInterceptor = vi.fn(async (_payload: unknown, next: () => Promise<string>) => `shadow:${await next()}`)
    let activeContext!: HostPluginContext
    let shadowContext!: HostPluginContext
    let providerSeenBeforeCommit: unknown
    let onionSeenBeforeCommit: string | undefined

    const active = plugin('active', {
      provides: ['storage'],
      apply(ctx) {
        activeContext = ctx
        ctx.provide('storage', 'active')
        ctx.on('same-event', activeListener)
        ctx.intercept('same-operation', activeInterceptor)
      },
    })
    const failingShadow = plugin('failing-shadow', {
      provides: ['storage'],
      async apply(ctx) {
        shadowContext = ctx
        ctx.provide('storage', 'shadow')
        ctx.on('same-event', shadowListener)
        ctx.intercept('same-operation', shadowInterceptor)
        providerSeenBeforeCommit = host.resolve('storage')
        activeContext.events.broadcast('same-event')
        onionSeenBeforeCommit = await activeContext.events.onion('same-operation', null, async () => 'terminal')
        throw new Error('shadow validation failed')
      },
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(active)
    host.register(failingShadow)
    await host.activate([{ plugin: 'active' }])

    await expect(host.activate([{ plugin: 'failing-shadow' }])).rejects.toThrow('shadow validation failed')

    expect(providerSeenBeforeCommit).toBe('active')
    expect(onionSeenBeforeCommit).toBe('active:terminal')
    expect(activeListener).toHaveBeenCalledOnce()
    expect(shadowListener).not.toHaveBeenCalled()
    expect(shadowInterceptor).not.toHaveBeenCalled()
    expect(host.resolve('storage')).toBe('active')
    expect(host.inspect()).toMatchObject({ active: true, epoch: 1, providers: 1, scope: 'shadow-1' })

    expect(() => shadowContext.resolve('storage')).toThrow(expect.objectContaining({ code: 'CAPABILITY_MISSING' }))
    shadowContext.events.broadcast('same-event')
    expect(await shadowContext.events.onion('same-operation', null, async () => 'shadow-terminal')).toBe('shadow-terminal')
    expect(shadowListener).not.toHaveBeenCalled()
    expect(shadowInterceptor).not.toHaveBeenCalled()

    activeListener.mockClear()
    activeInterceptor.mockClear()
    activeContext.events.broadcast('same-event')
    expect(await activeContext.events.onion('same-operation', null, async () => 'terminal')).toBe('active:terminal')
    expect(activeListener).toHaveBeenCalledOnce()
    expect(shadowListener).not.toHaveBeenCalled()
    expect(activeInterceptor).toHaveBeenCalledOnce()
    expect(shadowInterceptor).not.toHaveBeenCalled()
    await host.dispose()
  })

  it('publishes a successful shadow atomically before draining the previous active scope', async () => {
    const oldListener = vi.fn()
    const nextListener = vi.fn()
    const disposeOld = vi.fn()
    let oldContext!: HostPluginContext
    let nextContext!: HostPluginContext
    let providerBeforeCommit: unknown
    let eventBeforeCommit: string | undefined
    const old = plugin('old-active', {
      provides: ['storage'],
      apply(ctx) {
        oldContext = ctx
        ctx.provide('storage', 'old')
        ctx.on('replacement-event', oldListener)
        ctx.intercept('replacement-operation', async (_payload, next) => `old:${await next()}`)
        ctx.effect(() => disposeOld, 'old cleanup')
      },
    })
    const replacement = plugin('replacement', {
      provides: ['storage'],
      async apply(ctx) {
        nextContext = ctx
        ctx.provide('storage', 'next')
        ctx.on('replacement-event', nextListener)
        ctx.intercept('replacement-operation', async (_payload, next) => `next:${await next()}`)
        providerBeforeCommit = host.resolve('storage')
        oldContext.events.broadcast('replacement-event')
        eventBeforeCommit = await oldContext.events.onion('replacement-operation', null, async () => 'terminal')
      },
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(old)
    host.register(replacement)
    await host.activate([{ plugin: 'old-active' }])

    await host.activate([{ plugin: 'replacement' }])

    expect(providerBeforeCommit).toBe('old')
    expect(eventBeforeCommit).toBe('old:terminal')
    expect(oldListener).toHaveBeenCalledOnce()
    expect(nextListener).not.toHaveBeenCalled()
    expect(disposeOld).toHaveBeenCalledOnce()
    expect(host.resolve('storage')).toBe('next')
    expect(host.inspect()).toMatchObject({ active: true, epoch: 2, scope: 'shadow-2' })

    nextContext.events.broadcast('replacement-event')
    expect(await nextContext.events.onion('replacement-operation', null, async () => 'terminal')).toBe('next:terminal')
    expect(oldListener).toHaveBeenCalledOnce()
    expect(nextListener).toHaveBeenCalledOnce()
    await host.dispose()
    expect(disposeOld).toHaveBeenCalledOnce()
  })

  it('resolves child provider overrides through the scope chain without leaking to parent or sibling', async () => {
    const resolved: Record<string, unknown> = {}
    let firstChild!: HostPluginContext
    let sibling!: HostPluginContext
    let disposeOverride!: () => Promise<void>
    const scoped = plugin('scoped', {
      provides: ['storage'],
      apply(ctx) {
        ctx.provide('storage', 'parent')
        firstChild = ctx.scope({ tenant: 'first' })
        sibling = ctx.scope({ tenant: 'sibling' })
        disposeOverride = firstChild.provide('storage', 'first-child')

        resolved.parent = ctx.resolve('storage')
        resolved.firstChild = firstChild.resolve('storage')
        resolved.sibling = sibling.resolve('storage')
      },
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(scoped)

    await host.activate([{ plugin: 'scoped' }])

    expect(resolved).toEqual({
      parent: 'parent',
      firstChild: 'first-child',
      sibling: 'parent',
    })
    expect(host.resolve('storage')).toBe('parent')

    await disposeOverride()
    expect(firstChild.resolve('storage')).toBe('parent')
    expect(sibling.resolve('storage')).toBe('parent')
    await host.dispose()
  })

  it('inherits listeners and onion interceptors from parent scopes without leaking child registrations', async () => {
    let parent!: HostPluginContext
    let child!: HostPluginContext
    let sibling!: HostPluginContext
    const trace: string[] = []
    const pluginWithScopes = plugin('event-scopes', {
      apply(ctx) {
        parent = ctx
        child = ctx.scope({ tenant: 'child' })
        sibling = ctx.scope({ tenant: 'sibling' })
        parent.on('scoped-event', () => { trace.push('parent') })
        child.on('scoped-event', () => { trace.push('child') })
        parent.intercept('scoped-operation', async (_payload, next) => {
          trace.push('parent:before')
          const result = await next()
          trace.push('parent:after')
          return `parent:${result}`
        })
        child.intercept('scoped-operation', async (_payload, next) => {
          trace.push('child:before')
          const result = await next()
          trace.push('child:after')
          return `child:${result}`
        })
      },
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(pluginWithScopes)
    await host.activate([{ plugin: 'event-scopes' }])

    parent.events.broadcast('scoped-event')
    expect(trace).toEqual(['parent'])
    trace.length = 0
    child.events.broadcast('scoped-event')
    expect(trace).toEqual(['parent', 'child'])
    trace.length = 0
    sibling.events.broadcast('scoped-event')
    expect(trace).toEqual(['parent'])

    trace.length = 0
    expect(await child.events.onion('scoped-operation', null, async () => 'terminal')).toBe('parent:child:terminal')
    expect(trace).toEqual(['parent:before', 'child:before', 'child:after', 'parent:after'])
    trace.length = 0
    expect(await parent.events.onion('scoped-operation', null, async () => 'terminal')).toBe('parent:terminal')
    expect(trace).toEqual(['parent:before', 'parent:after'])
    trace.length = 0
    expect(await sibling.events.onion('scoped-operation', null, async () => 'terminal')).toBe('parent:terminal')
    expect(trace).toEqual(['parent:before', 'parent:after'])
    await host.dispose()
  })

  it('keeps many-provider order stable from parent to child scope', async () => {
    const resolved: Record<string, unknown> = {}
    const manyScoped = plugin('many-scoped', {
      provides: ['cards'],
      apply(ctx) {
        ctx.provide('cards', 'parent-a')
        ctx.provide('cards', 'parent-b')
        const child = ctx.scope({ tenant: 'child' })
        child.provide('cards', 'child')
        resolved.parent = ctx.resolve('cards')
        resolved.child = child.resolve('cards')
      },
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(manyScoped)
    await host.activate([{ plugin: 'many-scoped' }])

    expect(resolved).toEqual({
      parent: ['parent-a', 'parent-b'],
      child: ['parent-a', 'parent-b', 'child'],
    })
    await host.dispose()
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
