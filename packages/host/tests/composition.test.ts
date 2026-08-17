import { describe, expect, it } from 'vitest'

import { compose, HostDiagnosticError } from '../src/index.ts'

describe('profile/bundle/patch composition', () => {
  it('merges config in declaration order and applies explicit patches', () => {
    const result = compose(
      { name: 'headless', plugins: [{ plugin: 'storage', config: { path: 'a', nested: { left: 1 } } }] },
      [{ name: 'company', plugins: [
        { plugin: 'storage', config: { nested: { right: 2 } } },
        { plugin: 'organization' },
      ] }],
      [
        { op: 'replace', plugin: 'storage', config: { path: 'b' } },
        { op: 'remove', plugin: 'organization' },
      ],
    )

    expect(result).toEqual([
      { plugin: 'storage', config: { path: 'b', nested: { left: 1, right: 2 } } },
    ])
  })

  it('fails loudly when a patch targets an unknown plugin', () => {
    expect(() => compose({ name: 'minimal', plugins: [] }, [], [
      { op: 'replace', plugin: 'missing', config: {} },
    ])).toThrowError(HostDiagnosticError)
  })
})
