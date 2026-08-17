import type { DoAIPlugin } from '@doai/host'

export interface EnterpriseService {
  readonly capability: string
  readonly records: Map<string, unknown>
  put(tenantId: string, id: string, value: unknown): void
  get(tenantId: string, id: string): unknown
}

function createTenantService(capability: string): EnterpriseService {
  const records = new Map<string, unknown>()
  const key = (tenantId: string, id: string) => `${tenantId}\u0000${id}`
  return {
    capability,
    records,
    put(tenantId, id, value) {
      if (!tenantId || !id) throw new Error(`${capability} requires tenant and record ids`)
      records.set(key(tenantId, id), value)
    },
    get(tenantId, id) { return records.get(key(tenantId, id)) },
  }
}

function officialPlugin(
  name: string,
  capability: string,
  permissions: DoAIPlugin['manifest']['permissions'] = [],
): DoAIPlugin {
  return {
    manifest: {
      name, version: '1.0.0', api_version: '1', dependencies: {}, requires: [],
      provides: [capability], permissions,
      config_schema: { type: 'object', additionalProperties: false },
    },
    apply(ctx) { ctx.provide(capability, createTenantService(capability)) },
  }
}

export function officialEnterprisePlugins(): DoAIPlugin[] {
  return [
    officialPlugin('projects-official', 'project.registry'),
    officialPlugin('rbac-official', 'policy.authorize'),
    officialPlugin('tenants-official', 'tenant.registry'),
    officialPlugin('mcp-oauth-official', 'mcp.oauth', [
      { kind: 'network', resources: ['configured-mcp-origins'], reason: 'OAuth MCP connections' },
      { kind: 'credential', resources: ['mcp-oauth'], reason: 'OAuth tokens' },
    ]),
    officialPlugin('audit-official', 'audit.sink', [
      { kind: 'filesystem', resources: ['audit-store'], reason: 'durable audit records' },
    ]),
    officialPlugin('calendar-official', 'calendar.resources'),
    officialPlugin('dependency-graph-official', 'dependency.graph'),
    officialPlugin('evolution-official', 'evolution.manage'),
    officialPlugin('ui-cards-official', 'ui.card'),
  ]
}
