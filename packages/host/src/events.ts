import type { Context } from '@deepseek-ai/cordis'

export type EventListener = (...args: unknown[]) => unknown
export type OnionNext<Result> = () => Promise<Result>
export type OnionInterceptor<Payload, Result> = (
  payload: Payload,
  next: OnionNext<Result>,
) => Result | Promise<Result>

type InterceptorStore = Map<string, OnionInterceptor<unknown, unknown>[]>
const interceptorStores = new WeakMap<Context, InterceptorStore>()

export class EventHub {
  readonly #interceptors: InterceptorStore

  constructor(readonly context: Context) {
    const root = context.root
    const existing = interceptorStores.get(root)
    if (existing !== undefined) this.#interceptors = existing
    else {
      this.#interceptors = new Map()
      interceptorStores.set(root, this.#interceptors)
    }
  }

  on(name: string, listener: EventListener): () => void {
    return this.context.on(name as never, listener as never)
  }

  broadcast(name: string, ...args: unknown[]): void {
    const emit = this.context.emit as unknown as (...values: unknown[]) => void
    emit(name, ...args)
  }

  async parallel(name: string, ...args: unknown[]): Promise<void> {
    const parallel = this.context.parallel as unknown as (...values: unknown[]) => Promise<void>
    await parallel(name, ...args)
  }

  async serial<Result>(name: string, ...args: unknown[]): Promise<Result | undefined> {
    const serial = this.context.serial as unknown as (...values: unknown[]) => Promise<Result | undefined>
    return await serial(name, ...args)
  }

  first<Result>(name: string, ...args: unknown[]): Result | undefined {
    const bail = this.context.bail as unknown as (...values: unknown[]) => Result | undefined
    return bail(name, ...args)
  }

  intercept<Payload, Result>(name: string, interceptor: OnionInterceptor<Payload, Result>): () => Promise<void> {
    return this.context.effect(() => {
      const entries = this.#interceptors.get(name) ?? []
      entries.push(interceptor as OnionInterceptor<unknown, unknown>)
      this.#interceptors.set(name, entries)
      return () => {
        const index = entries.indexOf(interceptor as OnionInterceptor<unknown, unknown>)
        if (index >= 0) entries.splice(index, 1)
        if (entries.length === 0) this.#interceptors.delete(name)
      }
    }, `doai.intercept(${JSON.stringify(name)})`)
  }

  async onion<Payload, Result>(
    name: string,
    payload: Payload,
    terminal: () => Result | Promise<Result>,
  ): Promise<Result> {
    const entries = [...(this.#interceptors.get(name) ?? [])] as OnionInterceptor<Payload, Result>[]
    const dispatch = async (index: number): Promise<Result> => {
      const interceptor = entries[index]
      if (interceptor === undefined) return await terminal()
      let called = false
      return await interceptor(payload, async () => {
        if (called) throw new Error(`onion interceptor called next() twice: ${name}`)
        called = true
        return await dispatch(index + 1)
      })
    }
    return await dispatch(0)
  }
}
