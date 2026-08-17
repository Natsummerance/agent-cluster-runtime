import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { createInterface } from 'node:readline'

import type { JsonValue, MutationMeta } from '@doai/protocol'

export type HostRequestHandler = (
  method: string,
  params: { [key: string]: JsonValue },
  mutation?: MutationMeta,
) => Promise<JsonValue>

export interface OrganizationSupervisorOptions {
  command: string
  args?: string[]
  cwd: string
  env?: Record<string, string>
  hostRequest: HostRequestHandler
  requestTimeoutMs?: number
  heartbeatMs?: number
}

interface PendingRequest {
  resolve(value: JsonValue): void
  reject(error: Error): void
  timeout: NodeJS.Timeout
}

export class RpcPeerError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly retryable: boolean,
    readonly details?: JsonValue,
  ) {
    super(message)
    this.name = 'RpcPeerError'
  }
}

export class OrganizationSupervisor {
  readonly #pending = new Map<string, PendingRequest>()
  #child: ChildProcessWithoutNullStreams | undefined
  #starting: Promise<void> | undefined
  #nextId = 0
  #heartbeat: NodeJS.Timeout | undefined
  #stopping = false
  readonly #stderr: string[] = []

  constructor(readonly options: OrganizationSupervisorOptions) {}

  async start(): Promise<void> {
    if (this.#child !== undefined) return
    if (this.#starting !== undefined) return await this.#starting
    this.#starting = this.#startOnce()
    try {
      await this.#starting
    } finally {
      this.#starting = undefined
    }
  }

  async call<Result extends JsonValue = JsonValue>(
    method: string,
    params: { [key: string]: JsonValue },
    mutation?: MutationMeta,
  ): Promise<Result> {
    await this.start()
    return await this.#callRaw(method, params, mutation) as Result
  }

  async cancel(sessionId: string): Promise<void> {
    await this.call('organization.cancel', { session_id: sessionId })
  }

  async stop(): Promise<void> {
    this.#stopping = true
    if (this.#heartbeat !== undefined) clearInterval(this.#heartbeat)
    this.#heartbeat = undefined
    const child = this.#child
    this.#child = undefined
    if (child === undefined) return
    const exited = new Promise<void>((resolveExit) => child.once('exit', () => resolveExit()))
    child.kill()
    await exited
    this.#stopping = false
  }

  inspect(): { running: boolean; pid?: number; pending: number; stderr: string[] } {
    return {
      running: this.#child !== undefined,
      ...(this.#child?.pid === undefined ? {} : { pid: this.#child.pid }),
      pending: this.#pending.size,
      stderr: [...this.#stderr],
    }
  }

  async #startOnce(): Promise<void> {
    this.#stopping = false
    const child = spawn(this.options.command, this.options.args ?? [], {
      cwd: this.options.cwd,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'],
      env: {
        PATH: process.env.PATH,
        PATHEXT: process.env.PATHEXT,
        SystemRoot: process.env.SystemRoot,
        TEMP: process.env.TEMP,
        TMP: process.env.TMP,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1',
        ...this.options.env,
      },
    })
    this.#child = child
    createInterface({ input: child.stdout }).on('line', (line) => { void this.#onLine(line) })
    createInterface({ input: child.stderr }).on('line', (line) => {
      this.#stderr.push(line)
      if (this.#stderr.length > 20) this.#stderr.shift()
    })
    child.once('exit', (code, signal) => {
      if (this.#child === child) this.#child = undefined
      const error = new RpcPeerError(
        'ORGANIZATION_PEER_EXITED',
        `organization plane exited (code=${String(code)}, signal=${String(signal)})`,
        !this.#stopping,
        { stderr: this.#stderr.join('\n') },
      )
      for (const pending of this.#pending.values()) {
        clearTimeout(pending.timeout)
        pending.reject(error)
      }
      this.#pending.clear()
    })
    child.once('error', (error) => {
      if (this.#child === child) this.#child = undefined
      for (const pending of this.#pending.values()) pending.reject(error)
      this.#pending.clear()
    })

    await this.#callRaw('protocol.hello', {
      protocol_version: '1.0',
      event_schema_version: '1.0',
      capabilities: ['agent.invoke', 'approval.request', 'session.append', 'session.idempotency.get'],
    })
    const heartbeatMs = this.options.heartbeatMs ?? 10_000
    if (heartbeatMs > 0) {
      this.#heartbeat = setInterval(() => {
        if (this.#pending.size === 0 && this.#child !== undefined) {
          void this.#callRaw('health', {}).catch(() => {})
        }
      }, heartbeatMs)
      this.#heartbeat.unref()
    }
  }

  #callRaw(
    method: string,
    params: { [key: string]: JsonValue },
    mutation?: MutationMeta,
  ): Promise<JsonValue> {
    const child = this.#child
    if (child === undefined) throw new RpcPeerError('ORGANIZATION_PEER_NOT_RUNNING', 'organization plane is not running', true)
    const id = `host-${this.#nextId += 1}`
    return new Promise<JsonValue>((resolveRequest, reject) => {
      const timeout = setTimeout(() => {
        this.#pending.delete(id)
        reject(new RpcPeerError('ORGANIZATION_RPC_TIMEOUT', `organization RPC timed out: ${method}`, true))
      }, this.options.requestTimeoutMs ?? 30_000)
      this.#pending.set(id, { resolve: resolveRequest, reject, timeout })
      child.stdin.write(JSON.stringify({
        jsonrpc: '2.0', id, method, params,
        ...(mutation === undefined ? {} : { mutation }),
      }) + '\n')
    })
  }

  async #onLine(line: string): Promise<void> {
    let message: Record<string, unknown>
    try {
      message = JSON.parse(line) as Record<string, unknown>
    } catch {
      this.#failAll(new RpcPeerError('ORGANIZATION_PROTOCOL_ERROR', 'organization plane emitted invalid JSON', false))
      return
    }
    if (typeof message.method === 'string') {
      await this.#handleHostRequest(message)
      return
    }
    const id = String(message.id)
    const pending = this.#pending.get(id)
    if (pending === undefined) return
    this.#pending.delete(id)
    clearTimeout(pending.timeout)
    if (message.error !== undefined) {
      const error = message.error as Record<string, unknown>
      pending.reject(new RpcPeerError(
        String(error.code ?? 'ORGANIZATION_RPC_FAILED'),
        String(error.message ?? 'organization RPC failed'),
        Boolean(error.retryable),
        error.details as JsonValue | undefined,
      ))
    } else {
      pending.resolve(message.result as JsonValue)
    }
  }

  async #handleHostRequest(message: Record<string, unknown>): Promise<void> {
    const child = this.#child
    if (child === undefined) return
    const id = String(message.id)
    try {
      const result = await this.options.hostRequest(
        String(message.method),
        message.params as { [key: string]: JsonValue },
        message.mutation as MutationMeta | undefined,
      )
      child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, result }) + '\n')
    } catch (cause) {
      const error = cause instanceof RpcPeerError
        ? cause
        : new RpcPeerError('HOST_CAPABILITY_FAILED', cause instanceof Error ? cause.message : String(cause), false)
      child.stdin.write(JSON.stringify({
        jsonrpc: '2.0', id,
        error: { code: error.code, message: error.message, retryable: error.retryable, details: error.details },
      }) + '\n')
    }
  }

  #failAll(error: Error): void {
    for (const pending of this.#pending.values()) {
      clearTimeout(pending.timeout)
      pending.reject(error)
    }
    this.#pending.clear()
  }
}
