import type { Context } from '@deepseek-ai/cordis'

export type EventListener = (...args: unknown[]) => unknown
export type OnionNext<Result> = () => Promise<Result>
export type OnionInterceptor<Payload, Result> = (
  payload: Payload,
  next: OnionNext<Result>,
) => Result | Promise<Result>

interface ListenerEntry {
  callback: EventListener
}

interface InterceptorEntry {
  callback: OnionInterceptor<unknown, unknown>
}

class EventScope {
  readonly listeners = new Map<string, ListenerEntry[]>()
  readonly interceptors = new Map<string, InterceptorEntry[]>()

  constructor(readonly parent?: EventScope) {}

  listenerChain(name: string): ListenerEntry[] {
    return [
      ...(this.parent?.listenerChain(name) ?? []),
      ...(this.listeners.get(name) ?? []),
    ]
  }

  interceptorChain(name: string): InterceptorEntry[] {
    return [
      ...(this.parent?.interceptorChain(name) ?? []),
      ...(this.interceptors.get(name) ?? []),
    ]
  }
}

const eventScopes = new WeakMap<Context, EventScope>()

function isBailed(value: unknown): boolean {
  return value !== null && value !== false && value !== undefined
}

export class EventHub {
  readonly #scope: EventScope

  constructor(readonly context: Context) {
    const root = context.root
    const scoped = eventScopes.get(context)
    const rootScope = eventScopes.get(root)
    this.#scope = scoped ?? rootScope ?? new EventScope()
    if (rootScope === undefined) eventScopes.set(root, this.#scope)
  }

  bind(context: Context): EventHub {
    eventScopes.set(context, this.#scope)
    return new EventHub(context)
  }

  scope(context: Context): EventHub {
    eventScopes.set(context, new EventScope(this.#scope))
    return new EventHub(context)
  }

  on(name: string, listener: EventListener): () => void {
    const entry = { callback: this.context.reflect.bind(listener) }
    return this.context.effect(() => {
      const entries = this.#scope.listeners.get(name) ?? []
      entries.push(entry)
      this.#scope.listeners.set(name, entries)
      return () => {
        const index = entries.indexOf(entry)
        if (index >= 0) entries.splice(index, 1)
        if (entries.length === 0) this.#scope.listeners.delete(name)
      }
    }, `doai.on(${JSON.stringify(name)})`)
  }

  broadcast(name: string, ...args: unknown[]): void {
    for (const { callback } of this.#scope.listenerChain(name)) callback(...args)
  }

  async parallel(name: string, ...args: unknown[]): Promise<void> {
    const results = await Promise.allSettled(
      this.#scope.listenerChain(name).map(async ({ callback }) => await callback(...args)),
    )
    const errors = results
      .filter((result): result is PromiseRejectedResult => result.status === 'rejected')
      .map((result) => result.reason)
    if (errors.length > 0) throw new AggregateError(errors)
  }

  async serial<Result>(name: string, ...args: unknown[]): Promise<Result | undefined> {
    for (const { callback } of this.#scope.listenerChain(name)) {
      const result = await callback(...args) as Result
      if (isBailed(result)) return result
    }
    return undefined
  }

  first<Result>(name: string, ...args: unknown[]): Result | undefined {
    for (const { callback } of this.#scope.listenerChain(name)) {
      const result = callback(...args) as Result
      if (isBailed(result)) return result
    }
    return undefined
  }

  intercept<Payload, Result>(name: string, interceptor: OnionInterceptor<Payload, Result>): () => Promise<void> {
    const entry = { callback: interceptor as OnionInterceptor<unknown, unknown> }
    return this.context.effect(() => {
      const entries = this.#scope.interceptors.get(name) ?? []
      entries.push(entry)
      this.#scope.interceptors.set(name, entries)
      return () => {
        const index = entries.indexOf(entry)
        if (index >= 0) entries.splice(index, 1)
        if (entries.length === 0) this.#scope.interceptors.delete(name)
      }
    }, `doai.intercept(${JSON.stringify(name)})`)
  }

  async onion<Payload, Result>(
    name: string,
    payload: Payload,
    terminal: () => Result | Promise<Result>,
  ): Promise<Result> {
    const entries = this.#scope.interceptorChain(name)
    const dispatch = async (index: number): Promise<Result> => {
      const entry = entries[index]
      if (entry === undefined) return await terminal()
      let called = false
      return await (entry.callback as OnionInterceptor<Payload, Result>)(payload, async () => {
        if (called) throw new Error(`onion interceptor called next() twice: ${name}`)
        called = true
        return await dispatch(index + 1)
      })
    }
    return await dispatch(0)
  }
}
