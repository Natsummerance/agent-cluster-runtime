import { DoAIHost, type CapabilityPolicy } from '@doai/host'
import { describe, expect, it } from 'vitest'

import { officialEnterprisePlugins, type EnterpriseService } from '../src/index.ts'

const policies: Record<string, CapabilityPolicy> = {
  'project.registry': 'exactly_one', 'policy.authorize': 'exactly_one',
  'tenant.registry': 'exactly_one', 'mcp.oauth': 'exactly_one',
  'audit.sink': 'exactly_one', 'calendar.resources': 'exactly_one',
  'dependency.graph': 'exactly_one', 'evolution.manage': 'exactly_one',
  'ui.card': 'many',
}

describe('official enterprise plugins', () => {
  it('loads as ordinary plugins and isolates records by tenant', async () => {
    const plugins = officialEnterprisePlugins()
    expect(new Set(plugins.map((plugin) => plugin.manifest.name)).size).toBe(9)
    const host = new DoAIHost({ capabilityPolicies: policies })
    plugins.forEach((plugin) => host.register(plugin))
    await host.activate(plugins.map((plugin) => ({ plugin: plugin.manifest.name })), {
      permissionGrants: plugins.flatMap((plugin) => plugin.manifest.permissions.flatMap((permission) =>
        permission.resources.map((resource) => ({ plugin: plugin.manifest.name, kind: permission.kind, resource })))),
      credentialProbe: async () => true,
    })

    const projects = host.resolve<EnterpriseService>('project.registry')
    projects.put('tenant-a', 'same', { name: 'A' })
    projects.put('tenant-b', 'same', { name: 'B' })
    expect(projects.get('tenant-a', 'same')).toEqual({ name: 'A' })
    expect(projects.get('tenant-b', 'same')).toEqual({ name: 'B' })
    expect(host.resolve<EnterpriseService[]>('ui.card')).toHaveLength(1)
    await host.dispose()
  })

  it('declares external permissions for OAuth MCP and audit storage', () => {
    const byName = new Map(officialEnterprisePlugins().map((plugin) => [plugin.manifest.name, plugin]))
    expect(byName.get('mcp-oauth-official')?.manifest.permissions.map((item) => item.kind)).toEqual(['network', 'credential'])
    expect(byName.get('audit-official')?.manifest.permissions.map((item) => item.kind)).toEqual(['filesystem'])
  })
})
