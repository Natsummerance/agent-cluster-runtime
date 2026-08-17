export const COMMANDS = ['run', 'web', 'plugin', 'config', 'session', 'doctor', 'migrate'] as const
export type CommandName = typeof COMMANDS[number]

export interface ParsedCommand {
  command: CommandName
  args: string[]
  options: Record<string, string | boolean>
}

export function parseCommand(argv: string[]): ParsedCommand {
  const [rawCommand, ...rest] = argv
  if (!COMMANDS.includes(rawCommand as CommandName)) throw new Error(`unknown command: ${rawCommand ?? ''}`)
  const command = rawCommand as CommandName
  const options: Record<string, string | boolean> = {}
  const args: string[] = []
  const allowed = command === 'migrate'
    ? new Set(['--dry-run', '--apply', '--from', '--to'])
    : new Set<string>()
  for (let index = 0; index < rest.length; index += 1) {
    const value = rest[index]!
    if (!value.startsWith('--')) { args.push(value); continue }
    if (!allowed.has(value)) throw new Error(`unknown ${command} option: ${value}`)
    if (value === '--dry-run' || value === '--apply') options[value.slice(2)] = true
    else {
      const next = rest[index + 1]
      if (next === undefined || next.startsWith('--')) throw new Error(`${value} requires a value`)
      options[value.slice(2)] = next
      index += 1
    }
  }
  if (command === 'migrate' && options.apply && options['dry-run']) {
    throw new Error('migrate accepts exactly one of --apply or --dry-run')
  }
  return { command, args, options }
}
