import type { PluginManifest, PluginPermission } from '@doai/protocol'
import { describe, expect, it, vi } from 'vitest'

import {
  DoAIHost,
  type ActivationOptions,
  type CapabilityPolicy,
  type DoAIPlugin,
  type HealthCheck,
  type PermissionGrantSet,
} from '../src/index.ts'

const policies: Record<string, CapabilityPolicy> = {
  storage: 'exactly_one',
  cards: 'many',
}

function manifest(name: string): PluginManifest {
  return {
    name,
    version: '1.0.0',
    api_version: '1',
    dependencies: {},
    requires: [],
    provides: [],
    config_schema: { type: 'object', additionalProperties: false },
    permissions: [],
  }
}

function plugin(
  name: string,
  options: {
    dependencies?: Record<string, string>
    requires?: string[]
    provides?: string[]
    permissions?: PluginPermission[]
    configSchema?: Record<string, unknown>
    apply?: DoAIPlugin['apply']
  } = {},
): DoAIPlugin {
  return {
    manifest: {
      ...manifest(name),
      dependencies: options.dependencies ?? {},
      requires: options.requires ?? [],
      provides: options.provides ?? [],
      permissions: options.permissions ?? [],
      config_schema: options.configSchema ?? { type: 'object', additionalProperties: false },
    } as PluginManifest,
    apply: options.apply ?? (() => {}),
  }
}

function observableDiagnosticText(value: unknown): string {
  const seen = new Set<unknown>()
  const parts: string[] = []
  const visit = (candidate: unknown): void => {
    if (candidate === null || candidate === undefined || seen.has(candidate)) return
    if (typeof candidate === 'string') { parts.push(candidate); return }
    if (typeof candidate !== 'object') { parts.push(String(candidate)); return }
    seen.add(candidate)
    if (candidate instanceof Error) {
      parts.push(candidate.name, candidate.message, candidate.stack ?? '')
      visit(candidate.cause)
    }
    for (const item of Object.values(candidate)) visit(item)
    const serializable = candidate as { toJSON?: () => unknown }
    if (typeof serializable.toJSON === 'function') visit(serializable.toJSON())
  }
  visit(value)
  try { parts.push(JSON.stringify(value)) } catch { /* diagnostics must still be inspectable */ }
  return parts.join('\n')
}

function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void
  const promise = new Promise<void>((settle) => { resolve = settle })
  return { promise, resolve }
}

async function expectPending(promise: Promise<unknown>): Promise<void> {
  let settled = false
  void promise.then(
    () => { settled = true },
    () => { settled = true },
  )
  await Promise.resolve()
  await Promise.resolve()
  expect(settled).toBe(false)
}

describe('Host activation policy transaction', () => {
  it.each([
    {
      label: 'unsupported API',
      mutate: (value: Record<string, unknown>) => { value.api_version = '2' },
      code: 'API_VERSION_UNSUPPORTED', pointer: '/plugins/invalid/api_version',
    },
    {
      label: 'invalid plugin version',
      mutate: (value: Record<string, unknown>) => { value.version = 'not-semver' },
      code: 'MANIFEST_INVALID', pointer: '/plugins/invalid/version',
    },
    {
      label: 'invalid dependency range',
      mutate: (value: Record<string, unknown>) => { value.dependencies = { base: 'not a range' } },
      code: 'MANIFEST_INVALID', pointer: '/plugins/invalid/dependencies/base',
    },
    {
      label: 'invalid plugin name',
      mutate: (value: Record<string, unknown>) => { value.name = 'Invalid_Name' },
      code: 'MANIFEST_INVALID', pointer: '/plugins/Invalid_Name/name',
    },
    {
      label: 'malformed capability collection',
      mutate: (value: Record<string, unknown>) => { value.requires = 'storage' },
      code: 'MANIFEST_INVALID', pointer: '/plugins/invalid/requires',
    },
    {
      label: 'malformed provides collection',
      mutate: (value: Record<string, unknown>) => { value.provides = null },
      code: 'MANIFEST_INVALID', pointer: '/plugins/invalid/provides',
    },
    {
      label: 'malformed dependencies collection',
      mutate: (value: Record<string, unknown>) => { value.dependencies = [] },
      code: 'MANIFEST_INVALID', pointer: '/plugins/invalid/dependencies',
    },
    {
      label: 'malformed config schema',
      mutate: (value: Record<string, unknown>) => { value.config_schema = null },
      code: 'MANIFEST_INVALID', pointer: '/plugins/invalid/config_schema',
    },
    {
      label: 'malformed permissions collection',
      mutate: (value: Record<string, unknown>) => { value.permissions = null },
      code: 'MANIFEST_INVALID', pointer: '/plugins/invalid/permissions',
    },
    {
      label: 'missing required field',
      mutate: (value: Record<string, unknown>) => { delete value.permissions },
      code: 'MANIFEST_INVALID', pointer: '/plugins/invalid/permissions',
    },
    {
      label: 'invalid capability entry',
      mutate: (value: Record<string, unknown>) => { value.requires = ['bad capability'] },
      code: 'MANIFEST_INVALID', pointer: '/plugins/invalid/requires/0',
    },
    {
      label: 'duplicate capability declaration',
      mutate: (value: Record<string, unknown>) => { value.requires = ['storage', 'storage'] },
      code: 'MANIFEST_INVALID', pointer: '/plugins/invalid/requires',
    },
    {
      label: 'duplicate provided capability',
      mutate: (value: Record<string, unknown>) => { value.provides = ['storage', 'storage'] },
      code: 'MANIFEST_INVALID', pointer: '/plugins/invalid/provides',
    },
    {
      label: 'invalid permission kind',
      mutate: (value: Record<string, unknown>) => {
        value.permissions = [{ kind: 'database', resources: ['resource'] }]
      },
      code: 'MANIFEST_INVALID', pointer: '/plugins/invalid/permissions/0/kind',
    },
    {
      label: 'invalid permission resource',
      mutate: (value: Record<string, unknown>) => {
        value.permissions = [{ kind: 'network', resources: [''] }]
      },
      code: 'MANIFEST_INVALID', pointer: '/plugins/invalid/permissions/0/resources/0',
    },
    {
      label: 'empty permission resources',
      mutate: (value: Record<string, unknown>) => {
        value.permissions = [{ kind: 'network', resources: [] }]
      },
      code: 'MANIFEST_INVALID', pointer: '/plugins/invalid/permissions/0/resources',
    },
    {
      label: 'duplicate permission declaration',
      mutate: (value: Record<string, unknown>) => {
        value.permissions = [
          { kind: 'network', resources: ['https://service.invalid'] },
          { kind: 'network', resources: ['https://service.invalid'] },
        ]
      },
      code: 'MANIFEST_INVALID', pointer: '/plugins/invalid/permissions/1/resources/0',
    },
  ])('rejects $label before starting a fiber', async ({ mutate, code, pointer }) => {
    const apply = vi.fn()
    const raw = manifest('invalid') as unknown as Record<string, unknown>
    mutate(raw)
    const invalid = { manifest: raw, apply } as unknown as DoAIPlugin
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(invalid)
    const hasBase = raw.dependencies !== null && typeof raw.dependencies === 'object'
      && !Array.isArray(raw.dependencies) && Object.hasOwn(raw.dependencies, 'base')
    if (hasBase) {
      host.register(plugin('base'))
    }

    await expect(host.activate([
      { plugin: String(raw.name) },
      ...(hasBase ? [{ plugin: 'base' }] : []),
    ])).rejects.toMatchObject({ code, plugin: String(raw.name), pointer, hint: expect.any(String) })
    expect(apply).not.toHaveBeenCalled()
    await host.dispose()
  })

  it.each([
    { label: 'null manifest', raw: null, pointer: '/manifest' },
    { label: 'non-string name', raw: { ...manifest('invalid'), name: 42 }, pointer: '/manifest/name' },
  ])('fails loudly when registering a plugin with a $label', ({ raw, pointer }) => {
    const host = new DoAIHost({ capabilityPolicies: policies })
    expect(() => host.register({ manifest: raw, apply: vi.fn() } as unknown as DoAIPlugin))
      .toThrow(expect.objectContaining({ code: 'MANIFEST_INVALID', pointer, hint: expect.any(String) }))
  })

  it('revalidates the effective mutable manifest and config schema on every activation', async () => {
    const apply = vi.fn()
    const mutable = plugin('mutable', { apply })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(mutable)
    mutable.manifest.version = 'mutated-invalid-version'

    await expect(host.activate([{ plugin: 'mutable' }])).rejects.toMatchObject({
      code: 'MANIFEST_INVALID', pointer: '/plugins/mutable/version',
    })
    expect(apply).not.toHaveBeenCalled()

    mutable.manifest.version = '1.0.0'
    await host.activate([{ plugin: 'mutable' }])
    await host.deactivate()
    mutable.manifest.config_schema = {
      type: 'object', additionalProperties: false, required: ['enabled'],
      properties: { enabled: { type: 'boolean' } },
    }
    await expect(host.activate([{ plugin: 'mutable' }])).rejects.toMatchObject({
      code: 'CONFIG_INVALID', plugin: 'mutable', pointer: '/config',
    })
    expect(apply).toHaveBeenCalledOnce()
    await host.dispose()
  })

  it('rejects mutation of the registered plugin identity before starting a fiber', async () => {
    const apply = vi.fn()
    const mutable = plugin('registered-name', { apply })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(mutable)
    mutable.manifest.name = 'different-name'

    await expect(host.activate([{ plugin: 'registered-name' }])).rejects.toMatchObject({
      code: 'MANIFEST_INVALID', plugin: 'registered-name', pointer: '/plugins/registered-name/name',
    })
    expect(apply).not.toHaveBeenCalled()
    await host.dispose()
  })

  it('cannot validate one accessor value and activate a different manifest snapshot', async () => {
    const apply = vi.fn()
    const mutable = plugin('snapshot-race', { apply })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(mutable)
    let reads = 0
    Object.defineProperty(mutable.manifest, 'version', {
      configurable: true,
      enumerable: true,
      get() {
        reads += 1
        return reads <= 2 ? '1.0.0' : 'invalid-after-validation'
      },
    })

    await expect(host.activate([{ plugin: 'snapshot-race' }])).rejects.toMatchObject({
      code: 'MANIFEST_INVALID', plugin: 'snapshot-race', pointer: '/plugins/snapshot-race/version',
      hint: expect.any(String),
    })
    expect(apply).not.toHaveBeenCalled()
    expect(host.inspect()).toEqual({ active: false, epoch: 0, providers: 0, effects: 0 })
    await host.dispose()
  })

  it('cannot acquire missing manifest fields through snapshot prototype mutation', async () => {
    const apply = vi.fn()
    const raw = manifest('prototype-race') as unknown as Record<string, unknown>
    delete raw.permissions
    Object.defineProperty(raw, '__proto__', {
      configurable: true,
      enumerable: true,
      value: { permissions: [] },
    })
    const candidate = { manifest: raw, apply } as unknown as DoAIPlugin
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(candidate)

    await expect(host.activate([{ plugin: 'prototype-race' }])).rejects.toMatchObject({
      code: 'MANIFEST_INVALID', plugin: 'prototype-race', pointer: '/plugins/prototype-race/permissions',
      hint: expect.any(String),
    })
    expect(apply).not.toHaveBeenCalled()
    expect(host.inspect()).toEqual({ active: false, epoch: 0, providers: 0, effects: 0 })
    await host.dispose()
  })

  it('validates every manifest and grant before any apply or credential probe', async () => {
    const firstApply = vi.fn()
    const secondApply = vi.fn()
    const probe = vi.fn(async () => true)
    const credentialed = plugin('credentialed', {
      permissions: [{ kind: 'credential', resources: ['handle-a'] }],
      apply: firstApply,
    })
    const denied = plugin('denied', {
      permissions: [{ kind: 'process', resources: ['worker'] }],
      apply: secondApply,
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(credentialed)
    host.register(denied)

    await expect(host.activate([
      { plugin: 'credentialed' }, { plugin: 'denied' },
    ], {
      permissionGrants: [{ plugin: 'credentialed', kind: 'credential', resource: 'handle-a' }],
      credentialProbe: probe,
    })).rejects.toMatchObject({ code: 'PERMISSION_DENIED', plugin: 'denied' })
    expect(probe).not.toHaveBeenCalled()
    expect(firstApply).not.toHaveBeenCalled()
    expect(secondApply).not.toHaveBeenCalled()

    denied.manifest.permissions = []
    denied.manifest.version = 'invalid-version'
    await expect(host.activate([
      { plugin: 'credentialed' }, { plugin: 'denied' },
    ], {
      permissionGrants: [{ plugin: 'credentialed', kind: 'credential', resource: 'handle-a' }],
      credentialProbe: probe,
    })).rejects.toMatchObject({ code: 'MANIFEST_INVALID', plugin: 'denied' })
    expect(probe).not.toHaveBeenCalled()
    expect(firstApply).not.toHaveBeenCalled()
    expect(secondApply).not.toHaveBeenCalled()
    await host.dispose()
  })

  it.each([
    {
      label: 'no grants',
      grants: [] satisfies PermissionGrantSet,
      pointer: '/plugins/permissioned/permissions/0/resources/0',
    },
    {
      label: 'another plugin grant',
      grants: [{ plugin: 'other', kind: 'network', resource: 'net-service' }] satisfies PermissionGrantSet,
      pointer: '/plugins/permissioned/permissions/0/resources/0',
    },
    {
      label: 'wrong kind grant',
      grants: [{ plugin: 'permissioned', kind: 'filesystem', resource: 'net-service' }] satisfies PermissionGrantSet,
      pointer: '/plugins/permissioned/permissions/0/resources/0',
    },
    {
      label: 'one resource missing',
      grants: [{ plugin: 'permissioned', kind: 'network', resource: 'net-service' }] satisfies PermissionGrantSet,
      pointer: '/plugins/permissioned/permissions/1/resources/0',
    },
  ])('denies $label before apply', async ({ grants, pointer }) => {
    const apply = vi.fn()
    const permissioned = plugin('permissioned', {
      permissions: [
        { kind: 'network', resources: ['net-service'] },
        { kind: 'filesystem', resources: ['workspace'] },
      ],
      apply,
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(permissioned)

    await expect(host.activate([{ plugin: 'permissioned' }], { permissionGrants: grants }))
      .rejects.toMatchObject({
        code: 'PERMISSION_DENIED',
        plugin: 'permissioned',
        pointer,
        details: expect.objectContaining({ resource: expect.any(String) }),
      })
    expect(apply).not.toHaveBeenCalled()
    await host.dispose()
  })

  it.each(['network', 'filesystem', 'process', 'credential'] as const)(
    'requires an exact explicit grant for permission kind %s',
    async (kind) => {
      const apply = vi.fn()
      const resource = `${kind}-resource`
      const permissioned = plugin(`kind-${kind}`, {
        permissions: [{ kind, resources: [resource] }],
        apply,
      })
      const host = new DoAIHost({ capabilityPolicies: policies })
      host.register(permissioned)

      await expect(host.activate([{ plugin: `kind-${kind}` }]))
        .rejects.toMatchObject({ code: 'PERMISSION_DENIED', plugin: `kind-${kind}` })
      expect(apply).not.toHaveBeenCalled()

      await host.activate([{ plugin: `kind-${kind}` }], {
        permissionGrants: [{ plugin: `kind-${kind}`, kind, resource }],
        ...(kind === 'credential' ? { credentialProbe: async () => true } : {}),
      })
      expect(apply).toHaveBeenCalledOnce()
      await host.dispose()
    },
  )

  it('accepts only exact grants and probes each credential by opaque resource', async () => {
    const apply = vi.fn()
    const probe = vi.fn(async (resource: string) => resource === 'opaque-handle')
    const secured = plugin('secured', {
      permissions: [
        { kind: 'network', resources: ['net-service'] },
        { kind: 'credential', resources: ['opaque-handle'] },
      ],
      apply,
    })
    const grants: PermissionGrantSet = [
      { plugin: 'secured', kind: 'network', resource: 'net-service' },
      { plugin: 'secured', kind: 'credential', resource: 'opaque-handle' },
      { plugin: 'secured', kind: 'process', resource: 'undeclared-extra' },
    ]
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(secured)

    const report = await host.activate([{ plugin: 'secured' }], {
      permissionGrants: grants,
      credentialProbe: probe,
    })

    expect(report.hostEpoch).toBe(1)
    expect(probe).toHaveBeenCalledOnce()
    expect(probe).toHaveBeenCalledWith('opaque-handle')
    expect(apply).toHaveBeenCalledOnce()
    await host.dispose()
  })

  it('probes every authorized credential handle without probing non-credential resources', async () => {
    const probe = vi.fn(async () => true)
    const first = plugin('first-secured', {
      permissions: [
        { kind: 'credential', resources: ['handle-a', 'handle-b'] },
        { kind: 'network', resources: ['network-not-probed'] },
      ],
    })
    const second = plugin('second-secured', {
      permissions: [{ kind: 'credential', resources: ['handle-c'] }],
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(first)
    host.register(second)
    await host.activate([{ plugin: 'second-secured' }, { plugin: 'first-secured' }], {
      permissionGrants: [
        { plugin: 'first-secured', kind: 'credential', resource: 'handle-a' },
        { plugin: 'first-secured', kind: 'credential', resource: 'handle-b' },
        { plugin: 'first-secured', kind: 'network', resource: 'network-not-probed' },
        { plugin: 'second-secured', kind: 'credential', resource: 'handle-c' },
      ],
      credentialProbe: probe,
    })

    expect(probe.mock.calls).toEqual([['handle-a'], ['handle-b'], ['handle-c']])
    await host.dispose()
  })

  it.each([
    { label: 'missing probe', probe: undefined },
    { label: 'false probe', probe: async () => false },
    {
      label: 'throwing probe',
      probe: async () => {
        throw new Error('outer probe failure', { cause: new Error('SECRET_PROBE_DETAIL') })
      },
    },
  ])('fails closed for a $label without starting shadow plugins', async ({ probe }) => {
    const apply = vi.fn()
    const secured = plugin('secured', {
      permissions: [{ kind: 'credential', resources: ['opaque-handle'] }],
      apply,
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(secured)
    const options: ActivationOptions = {
      permissionGrants: [{ plugin: 'secured', kind: 'credential', resource: 'opaque-handle' }],
      ...(probe === undefined ? {} : { credentialProbe: probe }),
    }

    const error = await host.activate([{ plugin: 'secured' }], options).catch((cause: unknown) => cause)

    expect(error).toMatchObject({
      code: 'CREDENTIAL_MISSING',
      plugin: 'secured',
      pointer: '/plugins/secured/permissions/0/resources/0',
      details: { kind: 'credential', resource: 'opaque-handle' },
    })
    expect(observableDiagnosticText(error)).not.toContain('SECRET_PROBE_DETAIL')
    expect((error as { cause?: unknown }).cause).toBeUndefined()
    expect(apply).not.toHaveBeenCalled()
    await host.dispose()
  })

  it('runs health checks against shadow providers and rolls back before rejecting', async () => {
    const stableDispose = vi.fn()
    const shadowDispose = vi.fn()
    const stableInterceptor = vi.fn(async (_payload: unknown, next: () => Promise<string>) => `old:${await next()}`)
    const shadowInterceptor = vi.fn(async (_payload: unknown, next: () => Promise<string>) => `new:${await next()}`)
    let stableContext!: Parameters<DoAIPlugin['apply']>[0]
    let shadowContext!: Parameters<DoAIPlugin['apply']>[0]
    const stable = plugin('stable', {
      provides: ['storage'],
      apply(ctx) {
        stableContext = ctx
        ctx.provide('storage', 'old')
        ctx.intercept('health-operation', stableInterceptor)
        ctx.effect(() => stableDispose, 'stable cleanup')
      },
    })
    const shadow = plugin('shadow-health', {
      provides: ['storage'],
      apply(ctx) {
        shadowContext = ctx
        ctx.provide('storage', 'new')
        ctx.intercept('health-operation', shadowInterceptor)
        ctx.effect(() => shadowDispose, 'shadow cleanup')
      },
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(stable)
    host.register(shadow)
    const oldReport = await host.activate([{ plugin: 'stable' }])
    const oldInspection = host.inspect()
    const check: HealthCheck = async (view) => {
      expect(view.scope).toBe('shadow-2')
      expect(view.plugins).toEqual(['shadow-health'])
      expect(view.resolve('storage')).toBe('new')
      expect(host.resolve('storage')).toBe('old')
      expect(Object.keys(view).sort()).toEqual(['plugins', 'resolve', 'scope'])
      expect(Object.isFrozen(view)).toBe(true)
      expect(Object.isFrozen(view.plugins)).toBe(true)
      expect(() => (view.plugins as unknown as string[]).push('mutated')).toThrow()
      expect(await stableContext.events.onion('health-operation', null, async () => 'terminal')).toBe('old:terminal')
      expect(shadowInterceptor).not.toHaveBeenCalled()
      return false
    }

    await expect(host.activate([{ plugin: 'shadow-health' }], { healthChecks: [check] }))
      .rejects.toMatchObject({ code: 'HEALTH_CHECK_FAILED', scope: 'shadow-2', pointer: '/healthChecks/0' })

    expect(shadowDispose).toHaveBeenCalledOnce()
    expect(stableDispose).not.toHaveBeenCalled()
    expect(host.resolve('storage')).toBe('old')
    expect(host.inspect()).toEqual(oldInspection)
    expect(host.inspect().epochReport).toBe(oldReport)
    expect(await shadowContext.events.onion('health-operation', null, async () => 'terminal')).toBe('terminal')
    expect(shadowInterceptor).not.toHaveBeenCalled()
    await host.dispose()
    expect(stableDispose).toHaveBeenCalledOnce()
  })

  it('sanitizes a rejected health check and completes shadow cleanup', async () => {
    const cleanup = vi.fn()
    const candidate = plugin('candidate', {
      apply(ctx) { ctx.effect(() => cleanup, 'candidate cleanup') },
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(candidate)

    const error = await host.activate([{ plugin: 'candidate' }], {
      healthChecks: [async () => { throw new Error('private health detail') }],
    }).catch((cause: unknown) => cause)

    expect(error).toMatchObject({ code: 'HEALTH_CHECK_FAILED', pointer: '/healthChecks/0' })
    expect(cleanup).toHaveBeenCalledOnce()
    expect(host.inspect()).toMatchObject({ active: false, epoch: 0 })
    await host.dispose()
  })

  it('waits for every disposer, preserves a sanitized start failure, and creates no epoch gap', async () => {
    const cleanupEntered = deferred()
    const releaseCleanup = deferred()
    const cleanupAttempts: string[] = []
    const oldDispose = vi.fn()
    const seenValues: string[] = []
    let oldContext!: Parameters<DoAIPlugin['apply']>[0]
    let candidateContext!: Parameters<DoAIPlugin['apply']>[0]
    const oldListener = vi.fn()
    const candidateListener = vi.fn()
    const stableProvider = plugin('dual-provider', {
      provides: ['storage'],
      configSchema: {
        type: 'object', additionalProperties: false, required: ['value'],
        properties: { value: { type: 'string' } },
      },
      apply(ctx, config) { ctx.provide('storage', (config as { value: string }).value) },
    })
    const stableConsumer = plugin('dual-consumer', {
      dependencies: { 'dual-provider': '^1.0.0' },
      requires: ['storage'],
      apply(ctx) {
        if (seenValues.length === 0) oldContext = ctx
        seenValues.push(ctx.resolve('storage'))
        ctx.on('dual-event', oldListener)
        ctx.intercept('dual-operation', async (_payload, next) => `old:${await next()}`)
        ctx.effect(() => oldDispose, 'old consumer cleanup')
      },
    })
    const failing = plugin('dual-candidate', {
      provides: ['storage'],
      apply(ctx) {
        candidateContext = ctx
        ctx.provide('storage', 'candidate')
        ctx.on('dual-event', candidateListener)
        ctx.intercept('dual-operation', async (_payload, next) => `candidate:${await next()}`)
        ctx.effect(() => async () => {
          cleanupAttempts.push('first-failure')
          cleanupEntered.resolve()
          await releaseCleanup.promise
          throw new AggregateError([
            new Error('ROLLBACK_ONE_ERRORS_SECRET'),
          ], 'ROLLBACK_ONE_MESSAGE_SECRET', {
            cause: new Error('ROLLBACK_ONE_CAUSE_SECRET'),
          })
        }, 'ROLLBACK_ONE_LABEL_SECRET')
        ctx.effect(() => async () => {
          cleanupAttempts.push('second-failure')
          await releaseCleanup.promise
          const aborted = new Error('ROLLBACK_ABORT_MESSAGE_SECRET', {
            cause: new Error('ROLLBACK_ABORT_CAUSE_SECRET'),
          })
          aborted.name = 'AbortError'
          throw aborted
        }, 'ROLLBACK_ABORT_LABEL_SECRET')
        ctx.effect(() => () => { cleanupAttempts.push('successful-cleanup') }, 'safe cleanup')
        throw new AggregateError([
          new Error('PRIMARY_ERRORS_SECRET'),
        ], 'PRIMARY_MESSAGE_SECRET', {
          cause: new Error('PRIMARY_CAUSE_SECRET'),
        })
      },
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(stableProvider)
    host.register(stableConsumer)
    host.register(failing)
    const oldReport = await host.activate([
      { plugin: 'dual-provider', config: { value: 'old' } },
      { plugin: 'dual-consumer' },
    ])
    const oldInspection = host.inspect()

    const activation = host.activate([{ plugin: 'dual-candidate' }])
    await cleanupEntered.promise
    await expectPending(activation)
    expect(new Set(cleanupAttempts)).toEqual(new Set([
      'first-failure', 'second-failure', 'successful-cleanup',
    ]))
    releaseCleanup.resolve()
    const error = await activation.catch((cause: unknown) => cause)

    expect(error).toMatchObject({
      code: 'PLUGIN_START_FAILED',
      scope: 'shadow-2',
      pointer: '/plugins/dual-candidate/apply',
      hint: expect.any(String),
      details: {
        rollbackFailures: [
          {
            code: 'ROLLBACK_FAILED', plugin: 'dual-candidate', scope: 'shadow-2',
            pointer: '/rollback/0', hint: expect.any(String),
          },
          {
            code: 'ROLLBACK_FAILED', plugin: 'dual-candidate', scope: 'shadow-2',
            pointer: '/rollback/1', hint: expect.any(String),
          },
        ],
      },
    })
    expect((error as { code: string }).code).not.toBe('CANCELLED')
    expect(observableDiagnosticText(error)).not.toMatch(/PRIMARY_.*_SECRET|ROLLBACK_.*_SECRET/)
    expect((error as { cause?: unknown }).cause).toBeUndefined()
    expect(host.inspect()).toEqual(oldInspection)
    expect(host.inspect().epochReport).toBe(oldReport)
    expect(host.inspect().epochReport).toEqual(oldReport)
    expect(host.resolve('storage')).toBe('old')
    expect(oldDispose).not.toHaveBeenCalled()
    oldContext.events.broadcast('dual-event')
    expect(oldListener).toHaveBeenCalledOnce()
    expect(candidateListener).not.toHaveBeenCalled()
    expect(await oldContext.events.onion('dual-operation', null, async () => 'terminal')).toBe('old:terminal')
    expect(() => candidateContext.resolve('storage')).toThrow(expect.objectContaining({ code: 'CAPABILITY_MISSING' }))
    candidateContext.events.broadcast('dual-event')
    expect(candidateListener).not.toHaveBeenCalled()
    expect(await candidateContext.events.onion('dual-operation', null, async () => 'terminal')).toBe('terminal')

    const nextReport = await host.activate([
      { plugin: 'dual-provider', config: { value: 'next' } },
      { plugin: 'dual-consumer' },
    ])
    expect(nextReport).toEqual({
      hostEpoch: 2,
      scope: 'shadow-2',
      pluginEpochs: [
        { plugin: 'dual-consumer', epoch: 2 },
        { plugin: 'dual-provider', epoch: 2 },
      ],
      dependencyEpochs: [
        {
          consumer: 'dual-consumer', kind: 'manifest', dependency: 'dual-provider',
          provider: 'dual-provider', providerEpoch: 2,
        },
        {
          consumer: 'dual-consumer', kind: 'provider', dependency: 'storage',
          provider: 'dual-provider', providerEpoch: 2,
        },
      ],
    })
    expect(seenValues).toEqual(['old', 'next'])
    expect(oldDispose).toHaveBeenCalledOnce()
    await host.dispose()
  })

  it('preserves provider verification failure when a disposer also fails', async () => {
    const cleanupAttempts: string[] = []
    const incomplete = plugin('verify-candidate', {
      provides: ['storage'],
      apply(ctx) {
        ctx.effect(() => () => { cleanupAttempts.push('successful-cleanup') }, 'successful cleanup')
        ctx.effect(() => () => {
          cleanupAttempts.push('failed-cleanup')
          throw new Error('VERIFY_ROLLBACK_SECRET')
        }, 'VERIFY_LABEL_SECRET')
      },
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(incomplete)

    const error = await host.activate([{ plugin: 'verify-candidate' }])
      .catch((cause: unknown) => cause)

    expect(error).toMatchObject({
      code: 'CAPABILITY_MISSING',
      scope: 'shadow-1',
      pointer: '/capabilities/storage',
      details: {
        rollbackFailures: [{
          code: 'ROLLBACK_FAILED', plugin: 'verify-candidate', scope: 'shadow-1',
          pointer: '/rollback/0', hint: expect.any(String),
        }],
      },
    })
    expect(cleanupAttempts).toEqual(expect.arrayContaining(['successful-cleanup', 'failed-cleanup']))
    expect(observableDiagnosticText(error)).not.toContain('VERIFY_ROLLBACK_SECRET')
    expect(observableDiagnosticText(error)).not.toContain('VERIFY_LABEL_SECRET')
    expect(host.inspect()).toEqual({ active: false, epoch: 0, providers: 0, effects: 0 })
    await host.dispose()
  })

  it.each([
    { label: 'false', check: (() => false) as HealthCheck },
    {
      label: 'throw',
      check: (() => {
        throw new Error('HEALTH_PRIMARY_MESSAGE_SECRET', {
          cause: new Error('HEALTH_PRIMARY_CAUSE_SECRET'),
        })
      }) as HealthCheck,
    },
    {
      label: 'reject',
      check: (() => Promise.reject(new AggregateError([
        new Error('HEALTH_PRIMARY_ERRORS_SECRET'),
      ], 'HEALTH_PRIMARY_REJECT_SECRET'))) as HealthCheck,
    },
  ])('preserves health $label and cancellation-like rollback failure', async ({ check }) => {
    const oldDispose = vi.fn()
    const cleanupAttempt = vi.fn()
    const stable = plugin('health-stable', {
      provides: ['storage'],
      apply(ctx) {
        ctx.provide('storage', 'old')
        ctx.effect(() => oldDispose, 'old cleanup')
      },
    })
    const candidate = plugin('health-candidate', {
      provides: ['storage'],
      apply(ctx) {
        ctx.provide('storage', 'new')
        ctx.effect(() => () => {
          cleanupAttempt()
          const aborted = new Error('HEALTH_ROLLBACK_ABORT_SECRET')
          aborted.name = 'AbortError'
          throw aborted
        }, 'HEALTH_ROLLBACK_LABEL_SECRET')
      },
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(stable)
    host.register(candidate)
    const oldReport = await host.activate([{ plugin: 'health-stable' }])
    const oldInspection = host.inspect()

    const error = await host.activate([{ plugin: 'health-candidate' }], { healthChecks: [check] })
      .catch((cause: unknown) => cause)

    expect(error).toMatchObject({
      code: 'HEALTH_CHECK_FAILED', scope: 'shadow-2', pointer: '/healthChecks/0', hint: expect.any(String),
      details: {
        rollbackFailures: [{
          code: 'ROLLBACK_FAILED', plugin: 'health-candidate', scope: 'shadow-2',
          pointer: '/rollback/0', hint: expect.any(String),
        }],
      },
    })
    expect((error as { code: string }).code).not.toBe('CANCELLED')
    expect(cleanupAttempt).toHaveBeenCalledOnce()
    expect(observableDiagnosticText(error)).not.toContain('HEALTH_PRIMARY_')
    expect(observableDiagnosticText(error)).not.toContain('HEALTH_ROLLBACK_')
    expect(host.inspect()).toEqual(oldInspection)
    expect(host.inspect().epochReport).toBe(oldReport)
    expect(host.inspect().epochReport).toEqual(oldReport)
    expect(host.resolve('storage')).toBe('old')
    expect(oldDispose).not.toHaveBeenCalled()
    await host.dispose()
  })

  it.each([
    { label: 'false result', check: (() => false) as HealthCheck },
    { label: 'synchronous throw', check: (() => { throw new Error('sync health failure') }) as HealthCheck },
    { label: 'asynchronous reject', check: (() => Promise.reject(new Error('async health failure'))) as HealthCheck },
  ])('normalizes health check $label', async ({ check }) => {
    const cleanup = vi.fn()
    const candidate = plugin('health-shape', {
      apply(ctx) { ctx.effect(() => cleanup, 'health shape cleanup') },
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(candidate)

    await expect(host.activate([{ plugin: 'health-shape' }], { healthChecks: [check] }))
      .rejects.toMatchObject({
        code: 'HEALTH_CHECK_FAILED', scope: 'shadow-1', pointer: '/healthChecks/0', hint: expect.any(String),
      })
    expect(cleanup).toHaveBeenCalledOnce()
    await host.dispose()
  })

  it('verifies live providers before invoking health checks', async () => {
    const check = vi.fn(async () => true)
    const incomplete = plugin('incomplete', { provides: ['storage'] })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(incomplete)

    await expect(host.activate([{ plugin: 'incomplete' }], { healthChecks: [check] }))
      .rejects.toMatchObject({ code: 'CAPABILITY_MISSING' })
    expect(check).not.toHaveBeenCalled()
    await host.dispose()
  })

  it('advances deterministic dependency epochs only after successful consumer restart', async () => {
    const seenValues: string[] = []
    const drainedRuns: number[] = []
    const drainStateAtApply: number[][] = []
    let consumerRuns = 0
    const provider = plugin('provider', {
      provides: ['storage'],
      configSchema: {
        type: 'object', additionalProperties: false, required: ['value'],
        properties: { value: { type: 'string' } },
      },
      apply(ctx, config) { ctx.provide('storage', (config as { value: string }).value) },
    })
    const consumer = plugin('consumer', {
      dependencies: { provider: '^1.0.0' },
      requires: ['storage'],
      apply(ctx) {
        consumerRuns += 1
        const run = consumerRuns
        drainStateAtApply.push([...drainedRuns])
        seenValues.push(ctx.resolve('storage'))
        ctx.effect(() => () => { drainedRuns.push(run) }, `consumer run ${run}`)
      },
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(provider)
    host.register(consumer)

    const first = await host.activate([
      { plugin: 'consumer' },
      { plugin: 'provider', config: { value: 'one' } },
    ])
    expect(first).toEqual({
      hostEpoch: 1,
      scope: 'shadow-1',
      pluginEpochs: [
        { plugin: 'consumer', epoch: 1 },
        { plugin: 'provider', epoch: 1 },
      ],
      dependencyEpochs: [
        { consumer: 'consumer', kind: 'manifest', dependency: 'provider', provider: 'provider', providerEpoch: 1 },
        { consumer: 'consumer', kind: 'provider', dependency: 'storage', provider: 'provider', providerEpoch: 1 },
      ],
    })
    expect(Object.isFrozen(first)).toBe(true)
    expect(Object.isFrozen(first.pluginEpochs)).toBe(true)
    expect(Object.isFrozen(first.dependencyEpochs[0])).toBe(true)

    const invalidPreflight = plugin('invalid-preflight')
    invalidPreflight.manifest.version = 'invalid'
    const incomplete = plugin('incomplete-epoch', { provides: ['storage'] })
    host.register(invalidPreflight)
    host.register(incomplete)

    await expect(host.activate([{ plugin: 'invalid-preflight' }]))
      .rejects.toMatchObject({ code: 'MANIFEST_INVALID' })
    expect(host.inspect().epochReport).toBe(first)
    await expect(host.activate([{ plugin: 'incomplete-epoch' }]))
      .rejects.toMatchObject({ code: 'CAPABILITY_MISSING' })
    expect(host.inspect().epochReport).toBe(first)
    await expect(host.activate([
      { plugin: 'provider', config: { value: 'failed-health' } },
      { plugin: 'consumer' },
    ], { healthChecks: [async () => false] })).rejects.toMatchObject({ code: 'HEALTH_CHECK_FAILED' })
    expect(host.inspect().epochReport).toBe(first)
    expect(host.resolve('storage')).toBe('one')

    const second = await host.activate([
      { plugin: 'provider', config: { value: 'two' } },
      { plugin: 'consumer' },
    ])

    expect(second).toEqual({
      hostEpoch: 2,
      scope: 'shadow-2',
      pluginEpochs: [
        { plugin: 'consumer', epoch: 2 },
        { plugin: 'provider', epoch: 2 },
      ],
      dependencyEpochs: [
        { consumer: 'consumer', kind: 'manifest', dependency: 'provider', provider: 'provider', providerEpoch: 2 },
        { consumer: 'consumer', kind: 'provider', dependency: 'storage', provider: 'provider', providerEpoch: 2 },
      ],
    })
    expect(seenValues).toEqual(['one', 'failed-health', 'two'])
    expect(consumerRuns).toBe(3)
    expect(drainStateAtApply).toEqual([[], [], [2]])
    expect(drainedRuns).toEqual([2, 1])
    expect(host.inspect().epochReport).toBe(second)
    expect(() => (second.pluginEpochs as unknown as unknown[]).push({})).toThrow()
    expect(() => { (second.pluginEpochs[0] as { epoch: number }).epoch = 99 }).toThrow()
    expect(host.inspect().epochReport).toEqual(second)
    await host.dispose()
    expect(drainedRuns).toEqual([2, 1, 3])
  })

  it('reports every actual provider owner for a many capability in stable order', async () => {
    const makeCard = (name: string, value?: string): DoAIPlugin => plugin(name, {
      provides: ['cards'],
      apply: value === undefined ? () => {} : (ctx) => { ctx.provide('cards', value) },
    })
    const consumer = plugin('card-consumer', {
      requires: ['cards'],
      apply(ctx) { expect(ctx.resolve('cards')).toEqual(['b', 'a']) },
    })
    const host = new DoAIHost({ capabilityPolicies: policies })
    host.register(makeCard('provider-b', 'b'))
    host.register(makeCard('declared-but-not-live'))
    host.register(makeCard('provider-a', 'a'))
    host.register(consumer)

    const report = await host.activate([
      { plugin: 'provider-b' }, { plugin: 'card-consumer' },
      { plugin: 'declared-but-not-live' }, { plugin: 'provider-a' },
    ])

    expect(report.dependencyEpochs.filter((item) => item.consumer === 'card-consumer')).toEqual([
      { consumer: 'card-consumer', kind: 'provider', dependency: 'cards', provider: 'provider-a', providerEpoch: 1 },
      { consumer: 'card-consumer', kind: 'provider', dependency: 'cards', provider: 'provider-b', providerEpoch: 1 },
    ])
    await host.dispose()
  })
})
