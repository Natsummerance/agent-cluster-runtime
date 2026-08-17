#!/usr/bin/env node
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

import { scaffoldPlugin } from '@doai/presets'

import { parseCommand } from './cli.ts'
import { migrateLegacySessions } from './migrate.ts'

async function main(): Promise<void> {
  const parsed = parseCommand(process.argv.slice(2))
  if (parsed.command === 'migrate') {
    const mode = parsed.options.apply ? 'apply' : 'dry-run'
    const source = parsed.options.from
    const target = parsed.options.to
    if (typeof source !== 'string' || typeof target !== 'string') throw new Error('migrate requires --from and --to')
    process.stdout.write(JSON.stringify(await migrateLegacySessions({ source, target, mode }), null, 2) + '\n')
    return
  }
  if (parsed.command === 'doctor') {
    process.stdout.write(JSON.stringify({
      ok: true,
      node: process.version,
      protocol_schema: existsSync(resolve('protocol/schema/doai-v1.schema.json')),
      platform: process.platform,
    }, null, 2) + '\n')
    return
  }
  if (parsed.command === 'plugin' && parsed.args[0] === 'scaffold') {
    process.stdout.write(JSON.stringify(scaffoldPlugin(parsed.args[1] ?? ''), null, 2) + '\n')
    return
  }
  throw new Error(`${parsed.command} requires a configured v1 profile; no implicit fallback is allowed`)
}

main().catch((cause) => {
  process.stderr.write(JSON.stringify({
    code: 'CLI_FAILED',
    message: cause instanceof Error ? cause.message : String(cause),
    hint: 'run doai doctor and inspect the active profile',
  }) + '\n')
  process.exitCode = 2
})
