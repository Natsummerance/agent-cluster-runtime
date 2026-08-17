import { Context } from '@deepseek-ai/cordis'
import { describe, expect, it, vi } from 'vitest'

import { EventHub } from '../src/events.ts'

declare module '@deepseek-ai/cordis' {
  interface Events {
    fixture(): string | undefined
  }
}

describe('EventHub', () => {
  it('matches Cordis serial and first-win behavior', async () => {
    const reference = new Context()
    const candidateContext = new Context()
    const candidate = new EventHub(candidateContext)
    const seenReference: string[] = []
    const seenCandidate: string[] = []

    reference.on('fixture', () => { seenReference.push('a'); return undefined })
    reference.on('fixture', () => { seenReference.push('b'); return 'stop' })
    reference.on('fixture', () => { seenReference.push('c'); return 'late' })
    candidate.on('fixture', () => { seenCandidate.push('a'); return undefined })
    candidate.on('fixture', () => { seenCandidate.push('b'); return 'stop' })
    candidate.on('fixture', () => { seenCandidate.push('c'); return 'late' })

    expect(await candidate.serial('fixture')).toBe(await reference.serial('fixture'))
    expect(seenCandidate).toEqual(seenReference)
    seenReference.length = 0
    seenCandidate.length = 0
    expect(candidate.first('fixture')).toBe(reference.bail('fixture'))
    expect(seenCandidate).toEqual(seenReference)
    await candidateContext.fiber.dispose()
    await reference.fiber.dispose()
  })

  it('runs onion interceptors around the terminal operation', async () => {
    const ctx = new Context()
    const registrationHub = new EventHub(ctx)
    const executionHub = new EventHub(ctx.extend({ session: 's-1' }))
    const trace: string[] = []
    registrationHub.intercept('tool.execute', async (_payload, next) => {
      trace.push('outer:before')
      const result = await next()
      trace.push('outer:after')
      return `${result}:outer`
    })
    registrationHub.intercept('tool.execute', async (_payload, next) => {
      trace.push('inner:before')
      const result = await next()
      trace.push('inner:after')
      return `${result}:inner`
    })

    const result = await executionHub.onion('tool.execute', {}, async () => {
      trace.push('terminal')
      return 'ok'
    })

    expect(result).toBe('ok:inner:outer')
    expect(trace).toEqual([
      'outer:before', 'inner:before', 'terminal', 'inner:after', 'outer:after',
    ])
    await ctx.fiber.dispose()
  })

  it('releases listeners and interceptors with their Cordis fiber', async () => {
    const ctx = new Context()
    const hub = new EventHub(ctx)
    const listener = vi.fn()
    const interceptor = vi.fn((_payload: unknown, next: () => Promise<unknown>) => next())
    hub.on('event', listener)
    hub.intercept('operation', interceptor)

    await ctx.fiber.dispose()
    hub.broadcast('event')
    await hub.onion('operation', null, async () => 'done')

    expect(listener).not.toHaveBeenCalled()
    expect(interceptor).not.toHaveBeenCalled()
  })
})
