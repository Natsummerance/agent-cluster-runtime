import { createHash } from 'node:crypto'

import { describe, expect, it } from 'vitest'

import type { DoAIPlugin } from '@doai/host'

import { CreatorConformanceKit, PermissionAuditor, PluginCatalog, scaffoldPlugin } from '../src/index.ts'

function candidate(cleanup: () => void): DoAIPlugin {
  return {
    manifest: {
      name: 'candidate', version: '1.0.0', api_version: '1', dependencies: {}, requires: [],
      provides: ['candidate.service'], config_schema: { type: 'object', additionalProperties: false },
      permissions: [{ kind: 'network', resources: ['https://example.invalid'], reason: 'test' }],
    },
    apply(ctx) {
      ctx.provide('candidate.service', { ok: true })
      ctx.effect(() => cleanup, 'candidate cleanup')
    },
  }
}

describe('Creator conformance and permission audit', () => {
  it('trial-installs only in a disposable shadow Host', async () => {
    let cleaned = 0
    const report = await new CreatorConformanceKit({ 'candidate.service': 'exactly_one' })
      .verify(candidate(() => { cleaned += 1 }), {})

    expect(report).toMatchObject({ valid: true, providersAfterDispose: 0, effectsAfterDispose: 0 })
    expect(cleaned).toBe(1)
  })

  it('requires explicit grants before formal installation', () => {
    const plugin = candidate(() => {})
    expect(() => new PermissionAuditor([]).assertGranted(plugin.manifest)).toThrow('network')
    expect(() => new PermissionAuditor([
      { kind: 'network', resource: 'https://example.invalid' },
    ]).assertGranted(plugin.manifest)).not.toThrow()
  })

  it('checks source integrity for install and upgrade', () => {
    const plugin = candidate(() => {})
    const source = new TextEncoder().encode('export default {}')
    const sha256 = createHash('sha256').update(source).digest('hex')
    const catalog = new PluginCatalog(new PermissionAuditor([
      { kind: 'network', resource: 'https://example.invalid' },
    ]))

    catalog.install({ plugin, source, sha256 })
    expect(catalog.inspect('candidate')).toEqual({ version: '1.0.0', sha256 })
    expect(() => catalog.upgrade({ plugin, source, sha256: '0'.repeat(64) })).toThrow('version')
    const upgraded = { ...plugin, manifest: { ...plugin.manifest, version: '1.1.0' } }
    expect(() => catalog.upgrade({ plugin: upgraded, source, sha256: '0'.repeat(64) })).toThrow('hash mismatch')
    catalog.upgrade({ plugin: upgraded, source, sha256 })
    expect(catalog.inspect('candidate')?.version).toBe('1.1.0')
  })

  it('generates a valid starter manifest', () => {
    expect(scaffoldPlugin('my-plugin').manifest).toMatchObject({
      name: 'my-plugin', api_version: '1', permissions: [],
    })
  })
})
