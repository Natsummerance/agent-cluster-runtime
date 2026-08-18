import { Context, type Fiber } from '@deepseek-ai/cordis'
import type { PluginManifest } from '@doai/protocol'
import Ajv, { type ErrorObject, type ValidateFunction } from 'ajv'
import addFormats from 'ajv-formats'
import semverSatisfies from 'semver/functions/satisfies.js'

import type { PluginSelection } from './composition.ts'
import { HostDiagnosticError } from './diagnostics.ts'
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

export interface DoAIHostOptions {
  capabilityPolicies: Record<string, CapabilityPolicy>
}

interface ProviderEntry {
  plugin: string
  value: unknown
}

class ScopeProviders {
  readonly #values = new Map<string, ProviderEntry[]>()

  constructor(readonly parent?: ScopeProviders) {}

  scope(): ScopeProviders {
    return new ScopeProviders(this)
  }

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

  resolve<Value>(capability: string, policy: CapabilityPolicy | undefined): Value {
    const entries = policy === 'many' ? this.#chain(capability) : this.#nearest(capability)
    if (entries.length === 0) {
      throw new HostDiagnosticError({
        code: 'CAPABILITY_MISSING',
        message: `capability has no provider: ${capability}`,
        pointer: `/capabilities/${capability}`,
        hint: 'enable a plugin that provides this capability',
      })
    }
    if (policy === 'many') return entries.map((entry) => entry.value) as Value
    if (entries.length !== 1) {
      throw new HostDiagnosticError({
        code: 'PROVIDER_CONFLICT',
        message: `capability requires exactly one provider: ${capability}`,
        pointer: `/capabilities/${capability}`,
        details: entries.map((entry) => entry.plugin),
      })
    }
    return entries[0]!.value as Value
  }

  count(): number {
    let result = 0
    for (const entries of this.#values.values()) result += entries.length
    return result
  }

  countFor(capability: string): number {
    return this.#values.get(capability)?.length ?? 0
  }

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
    readonly metadata: Record<string, unknown> = {},
  ) {}

  resolve<Value>(capability: string): Value {
    return this.providers.resolve<Value>(capability, this.policies[capability])
  }

  provide<Value>(capability: string, provider: Value): () => Promise<void> {
    if (!this.plugin.manifest.provides.includes(capability)) {
      throw new HostDiagnosticError({
        code: 'PROVIDER_UNDECLARED',
        message: `${this.plugin.manifest.name} attempted to provide undeclared capability: ${capability}`,
        plugin: this.plugin.manifest.name,
        scope: this.scopeName,
        pointer: `/plugins/${this.plugin.manifest.name}/provides`,
      })
    }
    return this.cordis.effect(() => this.providers.add(capability, {
      plugin: this.plugin.manifest.name,
      value: provider,
    }), `doai.provide(${JSON.stringify(capability)})`)
  }

  on(name: string, listener: EventListener): () => void {
    return this.events.on(name, listener)
  }

  intercept<Payload, Result>(name: string, interceptor: OnionInterceptor<Payload, Result>): () => Promise<void> {
    return this.events.intercept(name, interceptor)
  }

  effect(start: () => Effect, label?: string): () => Promise<void> {
    return this.cordis.effect(start as never, label)
  }

  scope(overrides: Record<string, unknown>): HostPluginContext {
    const cordis = this.cordis.extend(overrides)
    return new PluginContext(
      cordis,
      this.scopeName,
      this.plugin,
      this.providers.scope(),
      this.policies,
      this.events.scope(cordis),
      { ...this.metadata, ...overrides },
    )
  }
}

interface ActiveScope {
  name: string
  fiber: Fiber
  providers: ScopeProviders
}

function configPointer(error: ErrorObject): string {
  return error.instancePath === '' ? '/config' : `/config${error.instancePath}`
}

export class DoAIHost {
  readonly #root = new Context()
  readonly #plugins = new Map<string, DoAIPlugin>()
  readonly #validators = new Map<string, ValidateFunction>()
  readonly #ajv: Ajv
  readonly #policies: Record<string, CapabilityPolicy>
  #active: ActiveScope | undefined
  #epoch = 0

  constructor(options: DoAIHostOptions) {
    this.#policies = { ...options.capabilityPolicies }
    this.#ajv = new Ajv({ allErrors: true, strict: false })
    addFormats(this.#ajv)
  }

  register(plugin: DoAIPlugin): void {
    const name = plugin.manifest.name
    if (this.#plugins.has(name)) {
      throw new HostDiagnosticError({
        code: 'PLUGIN_DUPLICATE',
        message: `plugin is already registered: ${name}`,
        plugin: name,
      })
    }
    for (const capability of [...plugin.manifest.requires, ...plugin.manifest.provides]) {
      if (this.#policies[capability] === undefined) {
        throw new HostDiagnosticError({
          code: 'CAPABILITY_UNKNOWN',
          message: `${name} declares unknown capability: ${capability}`,
          plugin: name,
          pointer: `/plugins/${name}/capabilities/${capability}`,
          hint: 'add the capability to the host catalog before registering the plugin',
        })
      }
    }
    this.#plugins.set(name, plugin)
  }

  async activate(selection: PluginSelection[]): Promise<void> {
    const ordered = this.#prepare(selection)
    const providers = new ScopeProviders()
    const name = `shadow-${this.#epoch + 1}`
    let scopeContext: Context | undefined
    const fiber = this.#root.plugin({
      name,
      apply: async (cordis) => {
        scopeContext = cordis.extend({ doaiScope: name })
        const events = new EventHub(scopeContext).scope(scopeContext)
        for (const item of ordered) {
          const plugin = this.#plugins.get(item.plugin)!
          await scopeContext.plugin({
            name: plugin.manifest.name,
            apply: async (pluginCordis) => {
              const context = new PluginContext(
                pluginCordis,
                name,
                plugin,
                providers,
                this.#policies,
                events.bind(pluginCordis),
              )
              const cleanup = await plugin.apply(context, item.config ?? {})
              if (cleanup !== undefined) context.effect(() => cleanup, 'plugin return cleanup')
            },
          })
        }
        this.#verifyProviders(ordered, providers, name)
      },
    })
    try {
      await fiber
    } catch (cause) {
      await fiber.dispose()
      if (cause instanceof HostDiagnosticError) throw cause
      throw new HostDiagnosticError({
        code: 'PLUGIN_START_FAILED',
        message: cause instanceof Error ? cause.message : 'plugin activation failed',
        scope: name,
      }, { cause })
    }
    if (scopeContext === undefined) {
      await fiber.dispose()
      throw new HostDiagnosticError({ code: 'PLUGIN_START_FAILED', message: 'shadow scope did not start', scope: name })
    }
    const old = this.#active
    this.#active = { name, fiber, providers }
    this.#epoch += 1
    if (old !== undefined) await old.fiber.dispose()
  }

  resolve<Value>(capability: string): Value {
    if (this.#active === undefined) {
      throw new HostDiagnosticError({
        code: 'CAPABILITY_MISSING',
        message: `host has no active scope while resolving: ${capability}`,
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

  async dispose(): Promise<void> {
    await this.deactivate()
    await this.#root.fiber.dispose()
  }

  inspect(): { active: boolean; epoch: number; providers: number; effects: number; scope?: string } {
    const active = this.#active
    return {
      active: active !== undefined,
      epoch: this.#epoch,
      providers: active?.providers.count() ?? 0,
      effects: active?.fiber.getEffects().length ?? 0,
      ...(active === undefined ? {} : { scope: active.name }),
    }
  }

  #prepare(selection: PluginSelection[]): PluginSelection[] {
    const selected = new Map<string, PluginSelection>()
    for (const item of selection) {
      if (selected.has(item.plugin)) {
        throw new HostDiagnosticError({ code: 'PLUGIN_DUPLICATE', message: `plugin selected twice: ${item.plugin}`, plugin: item.plugin })
      }
      const plugin = this.#plugins.get(item.plugin)
      if (plugin === undefined) {
        throw new HostDiagnosticError({ code: 'PLUGIN_NOT_FOUND', message: `plugin is not registered: ${item.plugin}`, plugin: item.plugin })
      }
      let validate = this.#validators.get(item.plugin)
      if (validate === undefined) {
        try {
          validate = this.#ajv.compile(plugin.manifest.config_schema)
        } catch (cause) {
          throw new HostDiagnosticError({
            code: 'CONFIG_INVALID',
            message: `invalid config schema for ${item.plugin}: ${cause instanceof Error ? cause.message : String(cause)}`,
            plugin: item.plugin,
            pointer: `/plugins/${item.plugin}/config_schema`,
          }, { cause })
        }
      }
      this.#validators.set(item.plugin, validate)
      const config = item.config ?? {}
      if (!validate(config)) {
        const first = validate.errors?.[0]
        throw new HostDiagnosticError({
          code: 'CONFIG_INVALID',
          message: `invalid configuration for ${item.plugin}: ${this.#ajv.errorsText(validate.errors)}`,
          plugin: item.plugin,
          pointer: first === undefined ? '/config' : configPointer(first),
          details: validate.errors,
        })
      }
      selected.set(item.plugin, item)
    }

    for (const name of selected.keys()) {
      const plugin = this.#plugins.get(name)!
      for (const [dependency, range] of Object.entries(plugin.manifest.dependencies)) {
        const target = selected.get(dependency)
        if (target === undefined) {
          throw new HostDiagnosticError({
            code: 'DEPENDENCY_MISSING', message: `${name} requires plugin ${dependency}`, plugin: name,
            pointer: `/plugins/${name}/dependencies/${dependency}`,
          })
        }
        const version = this.#plugins.get(target.plugin)!.manifest.version
        if (!semverSatisfies(version, range)) {
          throw new HostDiagnosticError({
            code: 'DEPENDENCY_VERSION', message: `${name} requires ${dependency}@${range}, found ${version}`, plugin: name,
            pointer: `/plugins/${name}/dependencies/${dependency}`,
          })
        }
      }
    }

    const providerNames = new Map<string, string[]>()
    for (const name of selected.keys()) {
      for (const capability of this.#plugins.get(name)!.manifest.provides) {
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
    for (const name of selected.keys()) dependencies.set(name, new Set(Object.keys(this.#plugins.get(name)!.manifest.dependencies)))
    for (const name of selected.keys()) {
      for (const capability of this.#plugins.get(name)!.manifest.requires) {
        const providersForCapability = providerNames.get(capability) ?? []
        if (providersForCapability.length === 0) {
          throw new HostDiagnosticError({
            code: 'CAPABILITY_MISSING', message: `${name} requires capability ${capability}`, plugin: name,
            pointer: `/plugins/${name}/requires/${capability}`,
          })
        }
        for (const provider of providersForCapability) if (provider !== name) dependencies.get(name)!.add(provider)
      }
    }
    return this.#topological(selection, dependencies)
  }

  #topological(selection: PluginSelection[], dependencies: Map<string, Set<string>>): PluginSelection[] {
    const byName = new Map(selection.map((item) => [item.plugin, item]))
    const result: PluginSelection[] = []
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
    for (const item of selection) visit(item.plugin)
    return result
  }

  #verifyProviders(selection: PluginSelection[], providers: ScopeProviders, scope: string): void {
    const required = new Set<string>()
    for (const item of selection) {
      const manifest = this.#plugins.get(item.plugin)!.manifest
      manifest.requires.forEach((name) => required.add(name))
      manifest.provides.forEach((name) => {
        if (this.#policies[name] === 'exactly_one') required.add(name)
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
}
