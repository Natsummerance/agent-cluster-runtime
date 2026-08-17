import { createHash, verify as verifySignature } from 'node:crypto'

import { DoAIHost, type CapabilityPolicy, type DoAIPlugin } from '@doai/host'
import type { PluginManifest } from '@doai/protocol'

export interface ConformanceReport {
  valid: boolean
  providersAfterDispose: number
  effectsAfterDispose: number
  diagnostic?: string
}

export class CreatorConformanceKit {
  constructor(readonly policies: Record<string, CapabilityPolicy>) {}

  async verify(plugin: DoAIPlugin, config: Record<string, unknown>): Promise<ConformanceReport> {
    const host = new DoAIHost({ capabilityPolicies: this.policies })
    try {
      host.register(plugin)
      await host.activate([{ plugin: plugin.manifest.name, config }])
      await host.deactivate()
      const inspection = host.inspect()
      return {
        valid: inspection.providers === 0 && inspection.effects === 0,
        providersAfterDispose: inspection.providers,
        effectsAfterDispose: inspection.effects,
      }
    } catch (cause) {
      await host.deactivate()
      const inspection = host.inspect()
      return {
        valid: false,
        providersAfterDispose: inspection.providers,
        effectsAfterDispose: inspection.effects,
        diagnostic: cause instanceof Error ? cause.message : String(cause),
      }
    } finally {
      await host.dispose()
    }
  }

  exportBundle(plugin: DoAIPlugin, config: Record<string, unknown>): string {
    return JSON.stringify({ schema_version: 1, plugin: plugin.manifest, config }, null, 2) + '\n'
  }
}

export interface PermissionGrant {
  kind: 'network' | 'filesystem' | 'process' | 'credential'
  resource: string
}

export class PermissionAuditor {
  readonly #grants: Set<string>

  constructor(grants: PermissionGrant[]) {
    this.#grants = new Set(grants.map((grant) => `${grant.kind}:${grant.resource}`))
  }

  assertGranted(manifest: PluginManifest): void {
    for (const permission of manifest.permissions) {
      for (const resource of permission.resources) {
        if (!this.#grants.has(`${permission.kind}:${resource}`)) {
          throw new Error(`plugin permission is not granted: ${permission.kind}:${resource}`)
        }
      }
    }
  }
}

export function verifyPluginSource(
  source: Uint8Array,
  expectedSha256: string,
  signature?: Uint8Array,
  publicKey?: string,
): void {
  const actual = createHash('sha256').update(source).digest('hex')
  if (actual !== expectedSha256.toLowerCase()) throw new Error(`plugin source hash mismatch: expected ${expectedSha256}, got ${actual}`)
  if ((signature === undefined) !== (publicKey === undefined)) throw new Error('signature and public key must be supplied together')
  if (signature !== undefined && publicKey !== undefined && !verifySignature(null, source, publicKey, signature)) {
    throw new Error('plugin source signature is invalid')
  }
}

export function scaffoldPlugin(name: string): { manifest: PluginManifest; source: string } {
  if (!/^[a-z][a-z0-9-]*$/.test(name)) throw new Error(`invalid plugin name: ${name}`)
  return {
    manifest: {
      name, version: '0.1.0', api_version: '1', dependencies: {}, requires: [], provides: [],
      config_schema: { type: 'object', additionalProperties: false }, permissions: [],
    },
    source: `export default {\n  manifest: ${JSON.stringify({ name, version: '0.1.0' }, null, 2)},\n  apply(ctx, config) {\n    // Register reversible effects here.\n  },\n}\n`,
  }
}

export interface PluginSourcePackage {
  plugin: DoAIPlugin
  source: Uint8Array
  sha256: string
  signature?: Uint8Array
  publicKey?: string
}

export class PluginCatalog {
  readonly #installed = new Map<string, { version: string; sha256: string }>()

  constructor(readonly permissions: PermissionAuditor) {}

  install(sourcePackage: PluginSourcePackage): void {
    const { plugin, sha256 } = sourcePackage
    if (this.#installed.has(plugin.manifest.name)) throw new Error(`plugin is already installed: ${plugin.manifest.name}`)
    this.#verify(sourcePackage)
    this.#installed.set(plugin.manifest.name, { version: plugin.manifest.version, sha256 })
  }

  upgrade(sourcePackage: PluginSourcePackage): void {
    const current = this.#installed.get(sourcePackage.plugin.manifest.name)
    if (current === undefined) throw new Error(`plugin is not installed: ${sourcePackage.plugin.manifest.name}`)
    if (current.version === sourcePackage.plugin.manifest.version) throw new Error('plugin upgrade must change the version')
    this.#verify(sourcePackage)
    this.#installed.set(sourcePackage.plugin.manifest.name, {
      version: sourcePackage.plugin.manifest.version,
      sha256: sourcePackage.sha256,
    })
  }

  inspect(name: string): { version: string; sha256: string } | undefined {
    const value = this.#installed.get(name)
    return value === undefined ? undefined : { ...value }
  }

  #verify(sourcePackage: PluginSourcePackage): void {
    this.permissions.assertGranted(sourcePackage.plugin.manifest)
    verifyPluginSource(
      sourcePackage.source,
      sourcePackage.sha256,
      sourcePackage.signature,
      sourcePackage.publicKey,
    )
  }
}
