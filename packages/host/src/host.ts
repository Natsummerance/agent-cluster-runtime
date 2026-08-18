import { Context, type Fiber } from '@deepseek-ai/cordis'
import type { PluginManifest, PluginPermission } from '@doai/protocol'
import Ajv, { type ErrorObject } from 'ajv'
import addFormats from 'ajv-formats'
import semverSatisfies from 'semver/functions/satisfies.js'
import semverValid from 'semver/functions/valid.js'
import semverValidRange from 'semver/ranges/valid.js'

import type { PluginSelection } from './composition.ts'
import { HostDiagnosticError, type HostDiagnostic } from './diagnostics.ts'
import { EventHub, type EventListener, type OnionInterceptor } from './events.ts'

export type CapabilityPolicy = 'exactly_one' | 'many' | 'optional'
export type Effect = void | (() => void | Promise<void>) | Promise<void | (() => void | Promise<void>)>

export interface HostPluginContext {
  readonly events: EventHub
  readonly scopeName: string
  resolve<Value>(capability: string): Value
  provide<Value>(capability: string, provider: Value): () => Promise<void>
  on(name: string, listener: EventListener): () => void
  intercept<Payload, Result>(name: string, interceptor: OnionInterceptor<Payload, Result>): () => Promise<void>
  effect(start: () => Effect, label?: string): () => Promise<void>
  scope(overrides: Record<string, unknown>): HostPluginContext
}

export interface DoAIPlugin<Config = Record<string, unknown>> {
  manifest: PluginManifest
  apply(context: HostPluginContext, config: Config): void | (() => void | Promise<void>) | Promise<void | (() => void | Promise<void>)>
}

export interface DoAIHostOptions { capabilityPolicies: Record<string, CapabilityPolicy> }
export interface PermissionGrant { readonly plugin: string; readonly kind: PluginPermission['kind']; readonly resource: string }
export type PermissionGrantSet = readonly PermissionGrant[]
export type CredentialProbe = (resource: string) => boolean | Promise<boolean>
export interface HealthCheckView { readonly scope: string; readonly plugins: readonly string[]; resolve<Value>(capability: string): Value }
export type HealthCheck = (view: HealthCheckView) => boolean | Promise<boolean>
export interface ActivationOptions {
  readonly permissionGrants?: PermissionGrantSet
  readonly credentialProbe?: CredentialProbe
  readonly healthChecks?: readonly HealthCheck[]
}
export interface PluginEpoch { readonly plugin: string; readonly epoch: number }
export interface DependencyEpochObservation {
  readonly consumer: string
  readonly kind: 'manifest' | 'provider'
  readonly dependency: string
  readonly provider: string
  readonly providerEpoch: number
}
export interface ActivationEpochReport {
  readonly hostEpoch: number
  readonly scope: string
  readonly pluginEpochs: readonly PluginEpoch[]
  readonly dependencyEpochs: readonly DependencyEpochObservation[]
}
export interface HostInspection {
  readonly active: boolean
  readonly epoch: number
  readonly providers: number
  readonly effects: number
  readonly scope?: string
  readonly epochReport?: ActivationEpochReport
}

interface ProviderEntry { plugin: string; value: unknown }
interface PreparedPlugin { readonly plugin: DoAIPlugin; readonly manifest: PluginManifest; readonly config: Record<string, unknown> }
interface CleanupFailure { readonly plugin: string; readonly order: number }

const PLUGIN_NAME = /^[a-z][a-z0-9-]*$/
const CAPABILITY_NAME = /^[a-z][a-z0-9.-]*$/
const PERMISSION_KINDS = new Set<PluginPermission['kind']>(['network', 'filesystem', 'process', 'credential'])

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (!isRecord(value)) return false
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function deepFreeze<Value>(value: Value, seen = new WeakSet<object>()): Value {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    if (seen.has(value)) return value
    seen.add(value)
    for (const child of Object.values(value)) deepFreeze(child, seen)
    Object.freeze(value)
  }
  return value
}

function manifestFailure(plugin: string, pointer: string): never {
  throw new HostDiagnosticError({
    code: 'MANIFEST_INVALID', message: 'plugin manifest is invalid', plugin, pointer,
    hint: 'fix the plugin manifest before activation',
  })
}

function snapshotOwnData(
  value: unknown,
  plugin: string,
  pointer: string,
  active = new WeakSet<object>(),
  snapshots = new WeakMap<object, unknown>(),
): unknown {
  if (value === null || typeof value !== 'object') return value
  const previous = snapshots.get(value)
  if (previous !== undefined) return previous
  if (active.has(value)) manifestFailure(plugin, pointer)
  active.add(value)
  let descriptors: PropertyDescriptorMap
  try {
    descriptors = Object.getOwnPropertyDescriptors(value)
  } catch {
    manifestFailure(plugin, pointer)
  }
  const array = Array.isArray(value)
  const result: unknown[] | Record<string, unknown> = array ? [] : {}
  for (const key of Reflect.ownKeys(descriptors)) {
    if (key === 'length' && array) continue
    const descriptor = descriptors[key as keyof typeof descriptors]!
    const childPointer = typeof key === 'symbol' ? pointer : `${pointer}/${key}`
    if ('get' in descriptor || 'set' in descriptor || typeof key === 'symbol') {
      manifestFailure(plugin, childPointer)
    }
    if (!descriptor.enumerable) continue
    const child = snapshotOwnData(descriptor.value, plugin, childPointer, active, snapshots)
    if (array && /^\d+$/.test(key)) (result as unknown[])[Number(key)] = child
    else (result as Record<string, unknown>)[key] = child
  }
  active.delete(value)
  snapshots.set(value, result)
  return result
}

function validateStringArray(value: unknown, plugin: string, pointer: string): string[] {
  if (!Array.isArray(value)) manifestFailure(plugin, pointer)
  const seen = new Set<string>()
  for (let index = 0; index < value.length; index += 1) {
    const item = value[index]
    if (typeof item !== 'string' || !CAPABILITY_NAME.test(item)) manifestFailure(plugin, `${pointer}/${index}`)
    if (seen.has(item)) manifestFailure(plugin, pointer)
    seen.add(item)
  }
  return value as string[]
}

function validateManifest(raw: unknown, basePointer = '/manifest'): PluginManifest {
  if (!isRecord(raw)) manifestFailure('unknown', basePointer)
  if (typeof raw.name !== 'string') manifestFailure('unknown', `${basePointer}/name`)
  const plugin = raw.name
  const pointer = basePointer === '/manifest' ? basePointer : `/plugins/${plugin}`
  if (!PLUGIN_NAME.test(plugin)) manifestFailure(plugin, `${pointer}/name`)
  if (typeof raw.version !== 'string' || semverValid(raw.version) === null) manifestFailure(plugin, `${pointer}/version`)
  if (raw.api_version !== '1') {
    throw new HostDiagnosticError({
      code: 'API_VERSION_UNSUPPORTED', message: `unsupported plugin API version for ${plugin}`,
      plugin, pointer: `${pointer}/api_version`, hint: 'use plugin API version 1',
    })
  }
  if (!isPlainRecord(raw.dependencies)) manifestFailure(plugin, `${pointer}/dependencies`)
  for (const [dependency, range] of Object.entries(raw.dependencies)) {
    if (!PLUGIN_NAME.test(dependency) || typeof range !== 'string' || semverValidRange(range) === null) {
      manifestFailure(plugin, `${pointer}/dependencies/${dependency}`)
    }
  }
  validateStringArray(raw.requires, plugin, `${pointer}/requires`)
  validateStringArray(raw.provides, plugin, `${pointer}/provides`)
  if (!isPlainRecord(raw.config_schema)) manifestFailure(plugin, `${pointer}/config_schema`)
  if (!Array.isArray(raw.permissions)) manifestFailure(plugin, `${pointer}/permissions`)
  const permissionTuples = new Set<string>()
  for (let permissionIndex = 0; permissionIndex < raw.permissions.length; permissionIndex += 1) {
    const permission = raw.permissions[permissionIndex]
    const permissionPointer = `${pointer}/permissions/${permissionIndex}`
    if (!isRecord(permission)) manifestFailure(plugin, permissionPointer)
    if (typeof permission.kind !== 'string' || !PERMISSION_KINDS.has(permission.kind as PluginPermission['kind'])) {
      manifestFailure(plugin, `${permissionPointer}/kind`)
    }
    if (!Array.isArray(permission.resources) || permission.resources.length === 0) {
      manifestFailure(plugin, `${permissionPointer}/resources`)
    }
    for (let resourceIndex = 0; resourceIndex < permission.resources.length; resourceIndex += 1) {
      const resource = permission.resources[resourceIndex]
      const resourcePointer = `${permissionPointer}/resources/${resourceIndex}`
      if (typeof resource !== 'string' || resource.length === 0) manifestFailure(plugin, resourcePointer)
      const key = `${permission.kind}\u0000${resource}`
      if (permissionTuples.has(key)) manifestFailure(plugin, resourcePointer)
      permissionTuples.add(key)
    }
    if (permission.reason !== undefined && permission.reason !== null && typeof permission.reason !== 'string') {
      manifestFailure(plugin, `${permissionPointer}/reason`)
    }
  }
  return raw as unknown as PluginManifest
}

class CleanupRecorder {
  readonly #failures: CleanupFailure[] = []
  record(plugin: string, order: number): void { this.#failures.push({ plugin, order }) }
  diagnostics(scope: string): HostDiagnostic[] {
    return this.#failures.slice()
      .sort((left, right) => left.plugin.localeCompare(right.plugin) || left.order - right.order)
      .map(({ plugin }, index) => deepFreeze({
        code: 'ROLLBACK_FAILED' as const,
        message: `plugin cleanup failed during rollback: ${plugin}`,
        plugin, scope, pointer: `/rollback/${index}`,
        hint: 'inspect plugin cleanup without exposing raw failure',
      }))
  }
}

class ScopeProviders {
  readonly #values = new Map<string, ProviderEntry[]>()
  constructor(readonly parent?: ScopeProviders) {}
  scope(): ScopeProviders { return new ScopeProviders(this) }
  add(capability: string, entry: ProviderEntry): () => void {
    const entries = this.#values.get(capability) ?? []
    entries.push(entry)
    this.#values.set(capability, entries)
    return () => {
      const index = entries.indexOf(entry)
      if (index >= 0) entries.splice(index, 1)
      if (entries.length === 0) this.#values.delete(capability)
    }
  }
  entries(capability: string, policy: CapabilityPolicy | undefined): readonly ProviderEntry[] {
    return policy === 'many' ? this.#chain(capability) : this.#nearest(capability)
  }
  resolve<Value>(capability: string, policy: CapabilityPolicy | undefined): Value {
    const entries = this.entries(capability, policy)
    if (entries.length === 0) {
      throw new HostDiagnosticError({
        code: 'CAPABILITY_MISSING', message: `capability has no provider: ${capability}`,
        pointer: `/capabilities/${capability}`, hint: 'enable a plugin that provides this capability',
      })
    }
    if (policy === 'many') return entries.map((entry) => entry.value) as Value
    if (entries.length !== 1) {
      throw new HostDiagnosticError({
        code: 'PROVIDER_CONFLICT', message: `capability requires exactly one provider: ${capability}`,
        pointer: `/capabilities/${capability}`, details: entries.map((entry) => entry.plugin),
      })
    }
    return entries[0]!.value as Value
  }
  count(): number {
    let result = 0
    for (const entries of this.#values.values()) result += entries.length
    return result
  }
  countFor(capability: string): number { return this.#values.get(capability)?.length ?? 0 }
  #nearest(capability: string): ProviderEntry[] {
    const entries = this.#values.get(capability)
    if (entries !== undefined && entries.length > 0) return entries
    return this.parent === undefined ? [] : this.parent.#nearest(capability)
  }
  #chain(capability: string): ProviderEntry[] {
    return [
      ...(this.parent === undefined ? [] : this.parent.#chain(capability)),
      ...(this.#values.get(capability) ?? []),
    ]
  }
}

class PluginContext implements HostPluginContext {
  constructor(
    readonly cordis: Context,
    readonly scopeName: string,
    readonly plugin: DoAIPlugin,
    readonly providers: ScopeProviders,
    readonly policies: Record<string, CapabilityPolicy>,
    readonly events: EventHub,
    readonly cleanupRecorder: CleanupRecorder,
    readonly effectCounter: { value: number },
    readonly metadata: Record<string, unknown> = {},
  ) {}
  resolve<Value>(capability: string): Value { return this.providers.resolve<Value>(capability, this.policies[capability]) }
  provide<Value>(capability: string, provider: Value): () => Promise<void> {
    if (!this.plugin.manifest.provides.includes(capability)) {
      throw new HostDiagnosticError({
        code: 'PROVIDER_UNDECLARED', message: `${this.plugin.manifest.name} attempted to provide undeclared capability: ${capability}`,
        plugin: this.plugin.manifest.name, scope: this.scopeName,
        pointer: `/plugins/${this.plugin.manifest.name}/provides`,
      })
    }
    return this.cordis.effect(() => this.providers.add(capability, {
      plugin: this.plugin.manifest.name, value: provider,
    }), `doai.provide(${JSON.stringify(capability)})`)
  }
  on(name: string, listener: EventListener): () => void { return this.events.on(name, listener) }
  intercept<Payload, Result>(name: string, interceptor: OnionInterceptor<Payload, Result>): () => Promise<void> {
    return this.events.intercept(name, interceptor)
  }
  effect(start: () => Effect, label?: string): () => Promise<void> {
    const order = this.effectCounter.value++
    const plugin = this.plugin.manifest.name
    return this.cordis.effect((async () => {
      let dispose
      try { dispose = await start() } catch {
        throw new HostDiagnosticError({
          code: 'PLUGIN_START_FAILED', message: `plugin effect failed to start: ${plugin}`,
          plugin, scope: this.scopeName, pointer: `/plugins/${plugin}/apply`,
          hint: 'inspect the plugin effect start path without exposing raw failure',
        })
      }
      if (dispose === undefined) return
      return async () => {
        try { await dispose() } catch { this.cleanupRecorder.record(plugin, order) }
      }
    }) as never, label)
  }
  scope(overrides: Record<string, unknown>): HostPluginContext {
    const cordis = this.cordis.extend(overrides)
    return new PluginContext(
      cordis, this.scopeName, this.plugin, this.providers.scope(), this.policies,
      this.events.scope(cordis), this.cleanupRecorder, this.effectCounter,
      { ...this.metadata, ...overrides },
    )
  }
}

interface ActiveScope { name: string; fiber: Fiber; providers: ScopeProviders }
function configPointer(error: ErrorObject): string { return error.instancePath === '' ? '/config' : `/config${error.instancePath}` }

export class DoAIHost {
  readonly #root = new Context()
  readonly #plugins = new Map<string, DoAIPlugin>()
  readonly #ajv: Ajv
  readonly #policies: Record<string, CapabilityPolicy>
  #active: ActiveScope | undefined
  #epoch = 0
  #epochReport: ActivationEpochReport | undefined

  constructor(options: DoAIHostOptions) {
    this.#policies = { ...options.capabilityPolicies }
    this.#ajv = new Ajv({ allErrors: true, strict: false })
    addFormats(this.#ajv)
  }

  register(plugin: DoAIPlugin): void {
    const raw = (plugin as { manifest?: unknown }).manifest
    if (!isRecord(raw)) manifestFailure('unknown', '/manifest')
    if (typeof raw.name !== 'string') manifestFailure('unknown', '/manifest/name')
    const name = raw.name
    if (this.#plugins.has(name)) {
      throw new HostDiagnosticError({ code: 'PLUGIN_DUPLICATE', message: `plugin is already registered: ${name}`, plugin: name })
    }
    try {
      const valid = validateManifest(raw, `/plugins/${name}`)
      for (const capability of [...valid.requires, ...valid.provides]) {
        if (this.#policies[capability] === undefined) {
          throw new HostDiagnosticError({
            code: 'CAPABILITY_UNKNOWN', message: `${name} declares unknown capability: ${capability}`,
            plugin: name, pointer: `/plugins/${name}/capabilities/${capability}`,
            hint: 'add the capability to the host catalog before registering the plugin',
          })
        }
      }
    } catch (cause) {
      if (cause instanceof HostDiagnosticError && cause.code === 'CAPABILITY_UNKNOWN') throw cause
    }
    this.#plugins.set(name, plugin)
  }

  async activate(selection: PluginSelection[], options: ActivationOptions = {}): Promise<ActivationEpochReport> {
    const ordered = this.#prepare(selection)
    await this.#preflightPermissions(ordered, options)
    const providers = new ScopeProviders()
    const name = `shadow-${this.#epoch + 1}`
    const cleanupRecorder = new CleanupRecorder()
    let scopeContext: Context | undefined
    const fiber = this.#root.plugin({
      name,
      apply: async (cordis) => {
        scopeContext = cordis.extend({ doaiScope: name })
        const events = new EventHub(scopeContext).scope(scopeContext)
        for (const item of ordered) {
          const preparedPlugin: DoAIPlugin = { manifest: item.manifest, apply: item.plugin.apply.bind(item.plugin) }
          await scopeContext.plugin({
            name: item.manifest.name,
            apply: async (pluginCordis) => {
              const context = new PluginContext(
                pluginCordis, name, preparedPlugin, providers, this.#policies,
                events.bind(pluginCordis), cleanupRecorder, { value: 0 },
              )
              try {
                const cleanup = await preparedPlugin.apply(context, item.config)
                if (cleanup !== undefined) context.effect(() => cleanup, 'plugin return cleanup')
              } catch {
                throw new HostDiagnosticError({
                  code: 'PLUGIN_START_FAILED', message: `plugin failed to start: ${item.manifest.name}`,
                  plugin: item.manifest.name, scope: name,
                  pointer: `/plugins/${item.manifest.name}/apply`,
                  hint: 'inspect the plugin start path without exposing raw failure',
                })
              }
            },
          })
        }
        this.#verifyProviders(ordered, providers, name)
      },
    })
    try {
      await fiber
      if (scopeContext === undefined) {
        throw new HostDiagnosticError({
          code: 'PLUGIN_START_FAILED', message: 'shadow scope did not start', scope: name,
          pointer: '/shadow', hint: 'inspect the host shadow activation lifecycle',
        })
      }
      await this.#runHealthChecks(options.healthChecks ?? [], name, ordered, providers)
    } catch (cause) {
      try { await fiber.dispose() } catch { cleanupRecorder.record('host', Number.MAX_SAFE_INTEGER) }
      throw this.#rollbackDiagnostic(cause, name, cleanupRecorder)
    }
    const nextEpoch = this.#epoch + 1
    const report = this.#createEpochReport(nextEpoch, name, ordered, providers)
    const old = this.#active
    this.#active = { name, fiber, providers }
    this.#epoch = nextEpoch
    this.#epochReport = report
    if (old !== undefined) await old.fiber.dispose()
    return report
  }

  resolve<Value>(capability: string): Value {
    if (this.#active === undefined) {
      throw new HostDiagnosticError({
        code: 'CAPABILITY_MISSING', message: `host has no active scope while resolving: ${capability}`,
        pointer: `/capabilities/${capability}`,
      })
    }
    return this.#active.providers.resolve<Value>(capability, this.#policies[capability])
  }
  async deactivate(): Promise<void> {
    const active = this.#active
    this.#active = undefined
    if (active !== undefined) await active.fiber.dispose()
  }
  async dispose(): Promise<void> { await this.deactivate(); await this.#root.fiber.dispose() }
  inspect(): HostInspection {
    const active = this.#active
    return {
      active: active !== undefined, epoch: this.#epoch,
      providers: active?.providers.count() ?? 0, effects: active?.fiber.getEffects().length ?? 0,
      ...(active === undefined ? {} : { scope: active.name }),
      ...(this.#epochReport === undefined ? {} : { epochReport: this.#epochReport }),
    }
  }

  #prepare(selection: PluginSelection[]): PreparedPlugin[] {
    const selected = new Map<string, PreparedPlugin>()
    for (const item of selection) {
      if (selected.has(item.plugin)) {
        throw new HostDiagnosticError({ code: 'PLUGIN_DUPLICATE', message: `plugin selected twice: ${item.plugin}`, plugin: item.plugin })
      }
      const plugin = this.#plugins.get(item.plugin)
      if (plugin === undefined) {
        throw new HostDiagnosticError({ code: 'PLUGIN_NOT_FOUND', message: `plugin is not registered: ${item.plugin}`, plugin: item.plugin })
      }
      let manifestValue: unknown
      try { manifestValue = plugin.manifest } catch {
        manifestFailure(item.plugin, `/plugins/${item.plugin}`)
      }
      const snapshot = snapshotOwnData(manifestValue, item.plugin, `/plugins/${item.plugin}`)
      const effectiveManifest = validateManifest(snapshot, `/plugins/${item.plugin}`)
      if (effectiveManifest.name !== item.plugin) manifestFailure(item.plugin, `/plugins/${item.plugin}/name`)
      const manifest = deepFreeze(effectiveManifest)
      for (const capability of [...manifest.requires, ...manifest.provides]) {
        if (this.#policies[capability] === undefined) {
          throw new HostDiagnosticError({
            code: 'CAPABILITY_UNKNOWN', message: `${item.plugin} declares unknown capability: ${capability}`,
            plugin: item.plugin, pointer: `/plugins/${item.plugin}/capabilities/${capability}`,
            hint: 'add the capability to the host catalog before activation',
          })
        }
      }
      let validate
      try { validate = this.#ajv.compile(manifest.config_schema) } catch {
        manifestFailure(item.plugin, `/plugins/${item.plugin}/config_schema`)
      }
      let config: Record<string, unknown>
      try { config = deepFreeze(structuredClone(item.config ?? {})) } catch {
        throw new HostDiagnosticError({
          code: 'CONFIG_INVALID', message: `configuration cannot be snapshotted for ${item.plugin}`,
          plugin: item.plugin, pointer: '/config', hint: 'use cloneable configuration values',
        })
      }
      if (!validate(config)) {
        const first = validate.errors?.[0]
        throw new HostDiagnosticError({
          code: 'CONFIG_INVALID', message: `invalid configuration for ${item.plugin}`,
          plugin: item.plugin, pointer: first === undefined ? '/config' : configPointer(first),
          hint: 'make the configuration satisfy the plugin schema', details: deepFreeze(structuredClone(validate.errors)),
        })
      }
      selected.set(item.plugin, { plugin, manifest, config })
    }
    for (const [name, item] of selected) {
      for (const [dependency, range] of Object.entries(item.manifest.dependencies)) {
        const target = selected.get(dependency)
        if (target === undefined) {
          throw new HostDiagnosticError({
            code: 'DEPENDENCY_MISSING', message: `${name} requires plugin ${dependency}`, plugin: name,
            pointer: `/plugins/${name}/dependencies/${dependency}`,
          })
        }
        if (!semverSatisfies(target.manifest.version, range)) {
          throw new HostDiagnosticError({
            code: 'DEPENDENCY_VERSION', message: `${name} requires ${dependency}@${range}, found ${target.manifest.version}`,
            plugin: name, pointer: `/plugins/${name}/dependencies/${dependency}`,
          })
        }
      }
    }
    const providerNames = new Map<string, string[]>()
    for (const [name, item] of selected) {
      for (const capability of item.manifest.provides) {
        const names = providerNames.get(capability) ?? []
        names.push(name)
        providerNames.set(capability, names)
      }
    }
    for (const [capability, names] of providerNames) {
      if (this.#policies[capability] !== 'many' && names.length > 1) {
        throw new HostDiagnosticError({
          code: 'PROVIDER_CONFLICT', message: `capability requires one provider: ${capability}`,
          pointer: `/capabilities/${capability}`, details: names,
        })
      }
    }
    const dependencies = new Map<string, Set<string>>()
    for (const [name, item] of selected) dependencies.set(name, new Set(Object.keys(item.manifest.dependencies)))
    for (const [name, item] of selected) {
      for (const capability of item.manifest.requires) {
        const providerNamesForCapability = providerNames.get(capability) ?? []
        if (providerNamesForCapability.length === 0) {
          throw new HostDiagnosticError({
            code: 'CAPABILITY_MISSING', message: `${name} requires capability ${capability}`, plugin: name,
            pointer: `/plugins/${name}/requires/${capability}`,
          })
        }
        for (const provider of providerNamesForCapability) if (provider !== name) dependencies.get(name)!.add(provider)
      }
    }
    return this.#topological([...selected.values()], dependencies)
  }

  #topological(selection: PreparedPlugin[], dependencies: Map<string, Set<string>>): PreparedPlugin[] {
    const byName = new Map(selection.map((item) => [item.manifest.name, item]))
    const result: PreparedPlugin[] = []
    const visiting = new Set<string>()
    const visited = new Set<string>()
    const visit = (name: string): void => {
      if (visited.has(name)) return
      if (visiting.has(name)) {
        throw new HostDiagnosticError({ code: 'DEPENDENCY_CYCLE', message: `plugin dependency cycle includes: ${name}`, plugin: name })
      }
      visiting.add(name)
      for (const dependency of dependencies.get(name) ?? []) visit(dependency)
      visiting.delete(name)
      visited.add(name)
      result.push(byName.get(name)!)
    }
    for (const item of selection) visit(item.manifest.name)
    return result
  }

  async #preflightPermissions(selection: PreparedPlugin[], options: ActivationOptions): Promise<void> {
    const grants = new Set((options.permissionGrants ?? []).map(({ plugin, kind, resource }) => `${plugin}\u0000${kind}\u0000${resource}`))
    const tuples = selection.flatMap((item) => item.manifest.permissions.flatMap((permission, permissionIndex) =>
      permission.resources.map((resource, resourceIndex) => ({
        plugin: item.manifest.name, kind: permission.kind, resource, permissionIndex, resourceIndex,
      })))).sort((left, right) => left.plugin.localeCompare(right.plugin)
        || left.permissionIndex - right.permissionIndex || left.resourceIndex - right.resourceIndex)
    for (const tuple of tuples) {
      if (!grants.has(`${tuple.plugin}\u0000${tuple.kind}\u0000${tuple.resource}`)) {
        throw new HostDiagnosticError({
          code: 'PERMISSION_DENIED', message: `permission was not explicitly granted for ${tuple.plugin}`,
          plugin: tuple.plugin,
          pointer: `/plugins/${tuple.plugin}/permissions/${tuple.permissionIndex}/resources/${tuple.resourceIndex}`,
          hint: 'grant the exact plugin, permission kind, and resource tuple',
          details: deepFreeze({ kind: tuple.kind, resource: tuple.resource }),
        })
      }
    }
    for (const tuple of tuples) {
      if (tuple.kind !== 'credential') continue
      let available = false
      try { available = options.credentialProbe !== undefined && await options.credentialProbe(tuple.resource) } catch { available = false }
      if (!available) {
        throw new HostDiagnosticError({
          code: 'CREDENTIAL_MISSING', message: `credential is unavailable for ${tuple.plugin}`,
          plugin: tuple.plugin,
          pointer: `/plugins/${tuple.plugin}/permissions/${tuple.permissionIndex}/resources/${tuple.resourceIndex}`,
          hint: 'make the opaque credential resource available to the host',
          details: deepFreeze({ kind: tuple.kind, resource: tuple.resource }),
        })
      }
    }
  }

  #verifyProviders(selection: PreparedPlugin[], providers: ScopeProviders, scope: string): void {
    const required = new Set<string>()
    for (const item of selection) {
      item.manifest.requires.forEach((capability) => required.add(capability))
      item.manifest.provides.forEach((capability) => {
        if (this.#policies[capability] === 'exactly_one') required.add(capability)
      })
    }
    for (const capability of required) {
      const count = providers.countFor(capability)
      if (count === 0) {
        throw new HostDiagnosticError({
          code: 'CAPABILITY_MISSING', message: `declared capability was not provided: ${capability}`,
          pointer: `/capabilities/${capability}`, scope,
        })
      }
      if (this.#policies[capability] !== 'many' && count !== 1) {
        throw new HostDiagnosticError({
          code: 'PROVIDER_CONFLICT', message: `capability has ${count} live providers: ${capability}`,
          pointer: `/capabilities/${capability}`, scope,
        })
      }
    }
  }

  async #runHealthChecks(checks: readonly HealthCheck[], scope: string, selection: PreparedPlugin[], providers: ScopeProviders): Promise<void> {
    const view = Object.freeze({
      scope,
      plugins: Object.freeze(selection.map((item) => item.manifest.name).sort()),
      resolve: <Value>(capability: string): Value => providers.resolve<Value>(capability, this.#policies[capability]),
    })
    for (let index = 0; index < checks.length; index += 1) {
      let healthy = false
      try { healthy = await checks[index]!(view) } catch { healthy = false }
      if (!healthy) {
        throw new HostDiagnosticError({
          code: 'HEALTH_CHECK_FAILED', message: `shadow health check failed at index ${index}`,
          scope, pointer: `/healthChecks/${index}`, hint: 'inspect the health check without exposing raw failure',
        })
      }
    }
  }

  #rollbackDiagnostic(cause: unknown, scope: string, recorder: CleanupRecorder): HostDiagnosticError {
    const primary = cause instanceof HostDiagnosticError ? cause : new HostDiagnosticError({
      code: 'PLUGIN_START_FAILED', message: 'plugin activation failed', scope,
      pointer: '/shadow', hint: 'inspect the shadow activation path without exposing raw failure',
    })
    const rollbackFailures = recorder.diagnostics(scope)
    if (rollbackFailures.length === 0) return primary
    const primaryDetails = isRecord(primary.details)
      ? primary.details
      : primary.details === undefined ? {} : { primaryDetails: deepFreeze(structuredClone(primary.details)) }
    return new HostDiagnosticError({
      ...primary.toJSON(),
      details: deepFreeze({ ...primaryDetails, rollbackFailures: deepFreeze(rollbackFailures) }),
    })
  }

  #createEpochReport(epoch: number, scope: string, selection: PreparedPlugin[], providers: ScopeProviders): ActivationEpochReport {
    const pluginEpochs = selection.map((item) => ({ plugin: item.manifest.name, epoch }))
      .sort((left, right) => left.plugin.localeCompare(right.plugin))
    const dependencyEpochs: DependencyEpochObservation[] = []
    for (const item of selection) {
      const consumer = item.manifest.name
      for (const dependency of Object.keys(item.manifest.dependencies)) {
        dependencyEpochs.push({ consumer, kind: 'manifest', dependency, provider: dependency, providerEpoch: epoch })
      }
      for (const dependency of item.manifest.requires) {
        const owners = new Set(providers.entries(dependency, this.#policies[dependency]).map((entry) => entry.plugin))
        for (const provider of [...owners].sort()) {
          dependencyEpochs.push({ consumer, kind: 'provider', dependency, provider, providerEpoch: epoch })
        }
      }
    }
    dependencyEpochs.sort((left, right) => left.consumer.localeCompare(right.consumer)
      || left.kind.localeCompare(right.kind) || left.dependency.localeCompare(right.dependency)
      || left.provider.localeCompare(right.provider))
    return deepFreeze({ hostEpoch: epoch, scope, pluginEpochs, dependencyEpochs })
  }
}
