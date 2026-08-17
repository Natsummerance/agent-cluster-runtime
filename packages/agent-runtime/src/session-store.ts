import { mkdir, open, readFile, rename, rm } from 'node:fs/promises'
import { dirname, join, resolve, sep } from 'node:path'

import type { EventScope, JsonValue, MutationMeta, SessionEvent } from '@doai/protocol'

export interface SessionEventDraft {
  type: string
  scope: EventScope
  payload: { [key: string]: JsonValue }
  ignorable: boolean
}

export interface SessionEventStore {
  append(sessionId: string, expectedRevision: number, draft: SessionEventDraft): Promise<SessionEvent>
  read(sessionId: string, throughSeq?: number): Promise<SessionEvent[]>
  revision(sessionId: string): Promise<number>
  fork(sourceSessionId: string, throughSeq: number, targetSessionId: string): Promise<void>
}

export interface IdempotentSessionEventStore extends SessionEventStore {
  appendIdempotent(sessionId: string, mutation: MutationMeta, draft: SessionEventDraft): Promise<SessionEvent>
  findIdempotency(sessionId: string, idempotencyKey: string): Promise<SessionEvent | undefined>
}

export class RevisionConflictError extends Error {
  constructor(
    readonly sessionId: string,
    readonly expected: number,
    readonly actual: number,
  ) {
    super(`session revision conflict for ${sessionId}: expected ${expected}, actual ${actual}`)
    this.name = 'RevisionConflictError'
  }
}

const SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/

export class JsonlSessionEventStore implements SessionEventStore {
  readonly #root: string
  readonly #now: () => Date
  readonly #locks = new Map<string, Promise<void>>()
  #temporaryCounter = 0

  constructor(root: string, now: () => Date = () => new Date()) {
    this.#root = resolve(root)
    this.#now = now
  }

  async append(sessionId: string, expectedRevision: number, draft: SessionEventDraft): Promise<SessionEvent> {
    return await this.#locked(sessionId, async () => {
      const events = await this.#readUnlocked(sessionId)
      const actual = events.length
      if (actual !== expectedRevision) throw new RevisionConflictError(sessionId, expectedRevision, actual)
      const event: SessionEvent = {
        schema_version: '1.0',
        session_id: sessionId,
        seq: actual + 1,
        type: draft.type,
        ts: this.#now().toISOString(),
        scope: draft.scope,
        payload: draft.payload,
        ignorable: draft.ignorable,
      }
      await this.#writeAtomic(sessionId, [...events, event])
      return event
    })
  }

  async appendIdempotent(sessionId: string, mutation: MutationMeta, draft: SessionEventDraft): Promise<SessionEvent> {
    return await this.#locked(sessionId, async () => {
      const events = await this.#readUnlocked(sessionId)
      const existing = events.find((event) => {
        const metadata = event.payload._mutation
        return metadata !== null && typeof metadata === 'object' && !Array.isArray(metadata)
          && metadata.idempotency_key === mutation.idempotency_key
      })
      if (existing !== undefined) {
        if (existing.type !== draft.type) {
          throw new Error(`idempotency key reused for a different event: ${mutation.idempotency_key}`)
        }
        return existing
      }
      if (events.length !== mutation.session_revision) {
        throw new RevisionConflictError(sessionId, mutation.session_revision, events.length)
      }
      const event: SessionEvent = {
        schema_version: '1.0', session_id: sessionId, seq: events.length + 1,
        type: draft.type, ts: this.#now().toISOString(), scope: draft.scope,
        payload: {
          ...draft.payload,
          _mutation: {
            request_id: mutation.request_id,
            idempotency_key: mutation.idempotency_key,
          },
        },
        ignorable: draft.ignorable,
      }
      await this.#writeAtomic(sessionId, [...events, event])
      return event
    })
  }

  async findIdempotency(sessionId: string, idempotencyKey: string): Promise<SessionEvent | undefined> {
    return (await this.#readUnlocked(sessionId)).find((event) => {
      const metadata = event.payload._mutation
      return metadata !== null && typeof metadata === 'object' && !Array.isArray(metadata)
        && metadata.idempotency_key === idempotencyKey
    })
  }

  async read(sessionId: string, throughSeq?: number): Promise<SessionEvent[]> {
    const events = await this.#readUnlocked(sessionId)
    return throughSeq === undefined ? events : events.filter((event) => event.seq <= throughSeq)
  }

  async revision(sessionId: string): Promise<number> {
    return (await this.#readUnlocked(sessionId)).length
  }

  async fork(sourceSessionId: string, throughSeq: number, targetSessionId: string): Promise<void> {
    this.#path(sourceSessionId)
    await this.#locked(targetSessionId, async () => {
      if ((await this.#readUnlocked(targetSessionId)).length !== 0) {
        throw new RevisionConflictError(targetSessionId, 0, await this.revision(targetSessionId))
      }
      const source = await this.read(sourceSessionId, throughSeq)
      if (throughSeq < 1 || source.length !== throughSeq) {
        throw new RevisionConflictError(sourceSessionId, throughSeq, source.length)
      }
      const copied = source.map((event, index): SessionEvent => ({
        ...event,
        session_id: targetSessionId,
        seq: index + 1,
      }))
      const parent = source.at(-1)!
      copied.push({
        schema_version: '1.0',
        session_id: targetSessionId,
        seq: copied.length + 1,
        type: 'session.forked',
        ts: this.#now().toISOString(),
        scope: parent.scope,
        payload: { source_session_id: sourceSessionId, source_seq: throughSeq },
        ignorable: false,
      })
      await this.#writeAtomic(targetSessionId, copied)
    })
  }

  #path(sessionId: string): string {
    if (!SESSION_ID.test(sessionId)) throw new Error(`invalid session id: ${sessionId}`)
    const path = resolve(this.#root, `${sessionId}.jsonl`)
    if (!path.startsWith(`${this.#root}${sep}`)) throw new Error(`invalid session id: ${sessionId}`)
    return path
  }

  async #readUnlocked(sessionId: string): Promise<SessionEvent[]> {
    const path = this.#path(sessionId)
    let text: string
    try {
      text = await readFile(path, 'utf8')
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return []
      throw error
    }
    if (text === '') return []
    return text.trimEnd().split('\n').map((line, index) => {
      const event = JSON.parse(line) as SessionEvent
      if (event.session_id !== sessionId || event.seq !== index + 1 || event.schema_version !== '1.0') {
        throw new Error(`corrupt session log ${sessionId} at line ${index + 1}`)
      }
      return event
    })
  }

  async #writeAtomic(sessionId: string, events: SessionEvent[]): Promise<void> {
    const path = this.#path(sessionId)
    await mkdir(dirname(path), { recursive: true })
    const temporary = join(dirname(path), `.${sessionId}.${process.pid}.${this.#temporaryCounter += 1}.tmp`)
    const handle = await open(temporary, 'wx')
    try {
      await handle.writeFile(events.map((event) => JSON.stringify(event)).join('\n') + '\n', 'utf8')
      await handle.sync()
    } finally {
      await handle.close()
    }
    try {
      await rename(temporary, path)
    } catch (error) {
      await rm(temporary, { force: true })
      throw error
    }
  }

  async #locked<Result>(sessionId: string, operation: () => Promise<Result>): Promise<Result> {
    const previous = this.#locks.get(sessionId) ?? Promise.resolve()
    let release!: () => void
    const current = new Promise<void>((resolveLock) => { release = resolveLock })
    const queued = previous.then(() => current)
    this.#locks.set(sessionId, queued)
    await previous
    try {
      return await operation()
    } finally {
      release()
      if (this.#locks.get(sessionId) === queued) this.#locks.delete(sessionId)
    }
  }
}
