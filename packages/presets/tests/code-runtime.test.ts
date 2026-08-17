import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import { PythonCodeRuntime, TypeScriptCodeRuntime } from '../src/index.ts'

const root = resolve(import.meta.dirname, '../../..')
const python = process.platform === 'win32'
  ? resolve(root, '.venv/Scripts/python.exe')
  : resolve(root, '.venv/bin/python')

describe('shared JSON code runtime contract', () => {
  it('executes Python in an isolated process', async () => {
    const runtime = new PythonCodeRuntime(python)
    expect(await runtime.evaluate({
      code: "result = {'sum': bindings['a'] + bindings['b']}",
      bindings: { a: 2, b: 3 },
    })).toEqual({ sum: 5 })
    await expect(runtime.evaluate({ code: "result = open('secret')", bindings: {} }))
      .rejects.toThrow('open')
  })

  it('executes TypeScript without exposing process or require', async () => {
    const runtime = new TypeScriptCodeRuntime()
    expect(await runtime.evaluate({
      code: "(bindings: any) => ({ sum: bindings.a + bindings.b })",
      bindings: { a: 4, b: 6 },
    })).toEqual({ sum: 10 })
    await expect(runtime.evaluate({ code: "() => process.env", bindings: {} }))
      .rejects.toThrow()
  })
})
