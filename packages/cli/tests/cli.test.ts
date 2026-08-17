import { describe, expect, it } from 'vitest'

import { parseCommand } from '../src/cli.ts'

describe('doai CLI surface', () => {
  it('recognizes the v1 command families', () => {
    for (const command of ['run', 'web', 'plugin', 'config', 'session', 'doctor', 'migrate']) {
      expect(parseCommand([command]).command).toBe(command)
    }
  })

  it('fails loudly for unknown commands and options', () => {
    expect(() => parseCommand(['unknown'])).toThrow('unknown command')
    expect(() => parseCommand(['migrate', '--mystery'])).toThrow('unknown migrate option')
    expect(() => parseCommand(['migrate', '--apply', '--dry-run'])).toThrow('exactly one')
  })
})
