export type PresetId = 'standard' | 'code' | 'minimal' | 'creator'
export type CodeRuntimeId = 'python' | 'typescript'

export interface AgentPreset {
  id: PresetId
  description: string
  plugins: string[]
}

export const PRESETS: readonly AgentPreset[] = [
  { id: 'standard', description: 'General agent with approved workspace tools', plugins: ['agent-standard', 'tools-local'] },
  { id: 'code', description: 'Standard agent plus an isolated JSON code runtime', plugins: ['agent-standard', 'tools-local'] },
  { id: 'minimal', description: 'Model-only agent without local tools', plugins: ['agent-minimal'] },
  { id: 'creator', description: 'Disposable plugin authoring and conformance scope', plugins: ['agent-creator'] },
]

export function resolvePreset(id: string, options: { runtime?: CodeRuntimeId } = {}): AgentPreset {
  const preset = PRESETS.find((item) => item.id === id)
  if (preset === undefined) throw new Error(`unknown agent preset: ${id}`)
  if (preset.id !== 'code') return { ...preset, plugins: [...preset.plugins] }
  if (options.runtime === undefined) throw new Error('code preset requires runtime: python or typescript')
  return { ...preset, plugins: [...preset.plugins, `runtime-${options.runtime}`, `tools-code-${options.runtime}`] }
}
