import { HostDiagnosticError } from './diagnostics.ts'

export interface PluginSelection {
  plugin: string
  config?: Record<string, unknown>
}

export interface CompositionLayer {
  name: string
  plugins: PluginSelection[]
}

export type CompositionPatch =
  | { op: 'add'; plugin: string; config?: Record<string, unknown> }
  | { op: 'replace'; plugin: string; config: Record<string, unknown> }
  | { op: 'remove'; plugin: string }

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function merge(left: Record<string, unknown>, right: Record<string, unknown>): Record<string, unknown> {
  const result = { ...left }
  for (const [key, value] of Object.entries(right)) {
    const previous = result[key]
    result[key] = isRecord(previous) && isRecord(value) ? merge(previous, value) : value
  }
  return result
}

export function compose(
  profile: CompositionLayer,
  bundles: CompositionLayer[],
  patches: CompositionPatch[],
): PluginSelection[] {
  const selected = new Map<string, PluginSelection>()
  for (const layer of [profile, ...bundles]) {
    for (const item of layer.plugins) {
      const previous = selected.get(item.plugin)
      selected.set(item.plugin, {
        plugin: item.plugin,
        config: merge(previous?.config ?? {}, item.config ?? {}),
      })
    }
  }
  for (const patch of patches) {
    const previous = selected.get(patch.plugin)
    if (patch.op === 'add') {
      if (previous !== undefined) {
        throw new HostDiagnosticError({
          code: 'PLUGIN_DUPLICATE',
          message: `patch cannot add already selected plugin: ${patch.plugin}`,
          pointer: `/patches/${patch.plugin}`,
          plugin: patch.plugin,
          hint: 'use replace to override its configuration',
        })
      }
      selected.set(patch.plugin, { plugin: patch.plugin, config: patch.config ?? {} })
      continue
    }
    if (previous === undefined) {
      throw new HostDiagnosticError({
        code: 'PLUGIN_NOT_FOUND',
        message: `patch targets an unselected plugin: ${patch.plugin}`,
        pointer: `/patches/${patch.plugin}`,
        plugin: patch.plugin,
      })
    }
    if (patch.op === 'remove') selected.delete(patch.plugin)
    else selected.set(patch.plugin, {
      plugin: patch.plugin,
      config: merge(previous.config ?? {}, patch.config),
    })
  }
  return [...selected.values()].map((item) => (
    Object.keys(item.config ?? {}).length === 0 ? { plugin: item.plugin } : item
  ))
}
