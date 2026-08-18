import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import type { JsonValue, MutationMeta, SessionEvent } from '@doai/protocol'
import { describe, expect, it, vi } from 'vitest'

import {
  JsonlSessionEventStore,
  StandardAgent,
  ToolRuntime,
  projectModelMessages,
  type ApprovalService,
  type ModelProvider,
  type SessionEventDraft,
  type SessionEventStore,
  type ToolDefinition,
} from '../src/index.ts'

const scope = { tenant_id: 'tenant-model-request', project_id: 'project-model-request' }
const approval: ApprovalService = { request: async () => ({ approved: true, reason: 'test' }) }

type ModelRequest = Parameters<ModelProvider['generate']>[0]
type RecordedRequest = Pick<ModelRequest, 'messages' | 'tools'>

class CountingToolRuntime extends ToolRuntime {
  listCalls = 0
  override list(): ReturnType<ToolRuntime['list']> {
    this.listCalls += 1
    return super.list()
  }
}

function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void
  const promise = new Promise<void>((settle) => { resolve = settle })
  return { promise, resolve }
}

function wrapStore(
  store: JsonlSessionEventStore,
  append: (sessionId: string, expectedRevision: number, draft: SessionEventDraft) => Promise<SessionEvent>,
): SessionEventStore {
  return {
    append,
    read: store.read.bind(store),
    revision: store.revision.bind(store),
    fork: store.fork.bind(store),
  }
}

function expectDeepFrozen(value: unknown, seen = new WeakSet<object>()): void {
  if (value === null || typeof value !== 'object' || seen.has(value)) return
  seen.add(value)
  expect(Object.isFrozen(value)).toBe(true)
  for (const child of Object.values(value)) expectDeepFrozen(child, seen)
}

function observableText(value: unknown): string {
  const parts: string[] = []
  const seen = new Set<unknown>()
  const visit = (candidate: unknown): void => {
    if (candidate === null || candidate === undefined || seen.has(candidate)) return
    if (typeof candidate !== 'object') { parts.push(String(candidate)); return }
    seen.add(candidate)
    if (candidate instanceof Error) {
      parts.push(candidate.name, candidate.message, candidate.stack ?? '')
      visit(candidate.cause)
    }
    for (const child of Object.values(candidate)) visit(child)
  }
  visit(value)
  try { parts.push(JSON.stringify(value)) } catch { /* caller-visible graph may not be serializable */ }
  return parts.join('\n')
}

function recordRequest(request: ModelRequest): RecordedRequest {
  return structuredClone({ messages: request.messages, tools: request.tools })
}

function projectDurableRequests(events: SessionEvent[]): RecordedRequest[] {
  return events
    .filter((event) => event.type === 'model.requested')
    .map((requested) => ({
      messages: projectModelMessages(events.filter((event) => event.seq < requested.seq)),
      tools: requested.payload.tools as unknown as RecordedRequest['tools'],
    }))
}

function definition(name: string, marker: string): ToolDefinition {
  return {
    name,
    description: `Tool surface ${marker}`,
    input_schema: {
      type: 'object',
      additionalProperties: false,
      required: ['payload'],
      properties: {
        payload: {
          type: 'object',
          additionalProperties: false,
          required: ['value'],
          properties: { value: { type: 'string', minLength: 1, marker } },
        },
      },
    },
    risk: 'read',
    async execute() { return marker },
  }
}

async function seedPartialModelRequest(
  store: JsonlSessionEventStore,
  sessionId: string,
  mutation: MutationMeta,
  tools: JsonValue | undefined,
): Promise<void> {
  const drafts: SessionEventDraft[] = [
    { type: 'session.created', scope, payload: {}, ignorable: false },
    { type: 'agent.system-prompt', scope, payload: { content: 'retry system' }, ignorable: false },
    { type: 'input.received', scope, payload: { content: 'retry input' }, ignorable: false },
    { type: 'agent.started', scope, payload: {}, ignorable: false },
    {
      type: 'model.requested',
      scope,
      payload: tools === undefined ? { step: 0 } : { step: 0, tools },
      ignorable: false,
    },
  ]
  for (let index = 0; index < drafts.length; index += 1) {
    await store.appendIdempotent(sessionId, {
      ...mutation,
      idempotency_key: `${mutation.idempotency_key}:event:${index}`,
      session_revision: index,
    }, drafts[index]!)
  }
}

async function runSingleSurface(tool: ToolDefinition): Promise<{
  events: SessionEvent[]
  requests: RecordedRequest[]
}> {
  const root = await mkdtemp(join(tmpdir(), 'doai-model-surface-'))
  const store = new JsonlSessionEventStore(root, () => new Date('2026-08-18T00:00:00Z'))
  const tools = new ToolRuntime()
  tools.register(tool)
  const requests: RecordedRequest[] = []
  const model: ModelProvider = {
    async generate(request) {
      requests.push(recordRequest(request))
      return { content: 'complete', tool_calls: [] }
    },
  }
  const agent = new StandardAgent({ store, model, tools, approval })
  await agent.invoke({
    session_id: 'same-prefix', scope, input: 'same input', system_prompt: 'same system',
  })
  return { events: await store.read('same-prefix'), requests }
}

describe('durable model request tool surface', () => {
  it('makes different adapter tool surfaces distinguishable in matching durable request events', async () => {
    const first = await runSingleSurface(definition('surface.first', 'first'))
    const second = await runSingleSurface(definition('surface.second', 'second'))

    expect(first.requests).toHaveLength(1)
    expect(second.requests).toHaveLength(1)
    expect(first.requests[0]!.messages).toEqual(second.requests[0]!.messages)
    expect(first.requests[0]!.tools).not.toEqual(second.requests[0]!.tools)

    const firstDurable = projectDurableRequests(first.events)
    const secondDurable = projectDurableRequests(second.events)
    expect(firstDurable).toEqual(first.requests)
    expect(secondDurable).toEqual(second.requests)
    expect(firstDurable[0]!.tools).not.toEqual(secondDurable[0]!.tools)
  })

  it('captures tools once per call and isolates durable history from disposal and mutation', async () => {
    const root = await mkdtemp(join(tmpdir(), 'doai-model-lifecycle-'))
    const store = new JsonlSessionEventStore(root, () => new Date('2026-08-18T00:00:00Z'))
    const tools = new CountingToolRuntime()
    const stable = definition('surface.stable', 'before-capture')
    tools.register(stable)
    const sharedRule = Object.create(null) as Record<string, unknown>
    Object.defineProperties(sharedRule, {
      type: { enumerable: true, value: 'string' },
      examples: { enumerable: true, value: ['', false, 0, 1.25, null] },
    })
    Object.defineProperty(stable.input_schema, '__proto__', {
      enumerable: true, value: { const: 'inert-own-data' },
    })
    Object.defineProperties(stable.input_schema, {
      shared_a: { enumerable: true, value: sharedRule },
      shared_b: { enumerable: true, value: sharedRule },
    })
    let disposeOnce!: () => void
    disposeOnce = tools.register({
      ...definition('surface.once', 'once'),
      async execute() {
        disposeOnce()
        return 'disposed'
      },
    })
    const requests: RecordedRequest[] = []
    let mutationFailures = 0
    const model: ModelProvider = {
      async generate(request) {
        requests.push(recordRequest(request))
        expectDeepFrozen(request.tools)
        const capturedSchema = request.tools[0]!.input_schema
        expect(Object.hasOwn(capturedSchema, '__proto__')).toBe(true)
        expect(Object.getPrototypeOf(capturedSchema.shared_a)).toBeNull()
        expect(capturedSchema.shared_a).toEqual(capturedSchema.shared_b)
        const mutationAttempts = [
          () => { (request.tools[0] as { name: string }).name = 'adapter mutation' },
          () => { request.tools[0] = request.tools.at(-1)! },
          () => {
            const properties = request.tools[0]!.input_schema.properties as Record<string, unknown>
            properties.payload = { type: 'number' }
          },
        ]
        for (const mutate of mutationAttempts) {
          try { mutate() } catch { mutationFailures += 1 }
        }
        if (requests.length === 1) {
          const schema = stable.input_schema.properties as Record<string, unknown>
          schema.after_capture = { type: 'boolean' }
          return {
            content: '',
            tool_calls: [{ id: 'dispose-1', name: 'surface.once', arguments: { payload: { value: 'go' } } }],
          }
        }
        return { content: 'done', tool_calls: [] }
      },
    }
    const agent = new StandardAgent({ store, model, tools, approval, maxSteps: 3 })

    await agent.invoke({
      session_id: 'lifecycle', scope, input: 'run lifecycle', system_prompt: 'observe tools',
    })

    const events = await store.read('lifecycle')
    const requested = events.filter((event) => event.type === 'model.requested')
    expect(requests).toHaveLength(requested.length)
    expect(requests).toHaveLength(2)
    expect(tools.listCalls).toBe(requests.length)
    expect(mutationFailures).toBe(requests.length * 3)
    expect(projectDurableRequests(events)).toEqual(requests)
    for (let index = 0; index < requests.length; index += 1) {
      const durableTools = requested[index]!.payload.tools
      expect(JSON.parse(JSON.stringify(durableTools))).toEqual(requests[index]!.tools)
      expect(requested[index]!.payload).not.toHaveProperty('messages')
    }
    expect(requests[0]!.tools.map((tool) => tool.name)).toEqual(['surface.stable', 'surface.once'])
    expect(requests[1]!.tools.map((tool) => tool.name)).toEqual(['surface.stable'])
    expect(requests[0]!.tools[0]!.description).toBe('Tool surface before-capture')
    expect(requests[0]!.tools[0]!.input_schema).not.toHaveProperty('properties.after_capture')
    expect(requests[1]!.tools[0]!.input_schema).toHaveProperty('properties.after_capture', { type: 'boolean' })
    expect(Object.hasOwn(requests[0]!.tools[0]!.input_schema, '__proto__')).toBe(true)
    expect((requests[0]!.tools[0]!.input_schema.__proto__ as Record<string, unknown>).const).toBe('inert-own-data')
    expect(requests[0]!.tools[0]!.input_schema).toHaveProperty('shared_a.examples', ['', false, 0, 1.25, null])
  })

  it('durably commits the matching tools before entering the model adapter', async () => {
    const root = await mkdtemp(join(tmpdir(), 'doai-model-barrier-'))
    const durable = new JsonlSessionEventStore(root, () => new Date('2026-08-18T00:00:00Z'))
    const entered = deferred()
    const release = deferred()
    const store = wrapStore(durable, async (sessionId, revision, draft) => {
      if (draft.type === 'model.requested') {
        entered.resolve()
        await release.promise
      }
      return await durable.append(sessionId, revision, draft)
    })
    const tools = new CountingToolRuntime()
    tools.register(definition('surface.barrier', 'barrier'))
    const generate = vi.fn(async (request: ModelRequest) => {
      const events = await durable.read('barrier')
      const requested = events.filter((event) => event.type === 'model.requested')
      expect(requested).toHaveLength(1)
      expect(requested[0]!.payload.tools).toEqual(recordRequest(request).tools)
      return { content: 'committed', tool_calls: [] }
    })
    const agent = new StandardAgent({ store, model: { generate }, tools, approval })

    const invocation = agent.invoke({
      session_id: 'barrier', scope, input: 'wait for append', system_prompt: 'persist first',
    })
    await entered.promise
    expect(generate).not.toHaveBeenCalled()
    expect((await durable.read('barrier')).filter((event) => event.type === 'model.requested')).toHaveLength(0)
    release.resolve()
    await invocation

    expect(generate).toHaveBeenCalledOnce()
    expect(tools.listCalls).toBe(1)
  })

  it('does not call the adapter when the model.requested append fails', async () => {
    const root = await mkdtemp(join(tmpdir(), 'doai-model-append-fail-'))
    const durable = new JsonlSessionEventStore(root, () => new Date('2026-08-18T00:00:00Z'))
    const appendError = new Error('model request append unavailable')
    const store = wrapStore(durable, async (sessionId, revision, draft) => {
      if (draft.type === 'model.requested') throw appendError
      return await durable.append(sessionId, revision, draft)
    })
    const tools = new CountingToolRuntime()
    tools.register(definition('surface.append-fail', 'append-fail'))
    const generate = vi.fn(async () => ({ content: 'must not run', tool_calls: [] }))
    const agent = new StandardAgent({ store, model: { generate }, tools, approval })

    const error = await agent.invoke({
      session_id: 'append-fail', scope, input: 'fail append', system_prompt: 'persist first',
    }).catch((cause: unknown) => cause)

    expect(error).toBe(appendError)
    expect(generate).not.toHaveBeenCalled()
    expect(tools.listCalls).toBe(1)
    const events = await durable.read('append-fail')
    expect(events.filter((event) => event.type === 'model.requested')).toHaveLength(0)
    expect(events.at(-1)).toMatchObject({ type: 'agent.failed', payload: { error: 'model request append unavailable' } })
  })

  it('keeps model rejection semantics after the durable request is committed', async () => {
    const root = await mkdtemp(join(tmpdir(), 'doai-model-reject-'))
    const store = new JsonlSessionEventStore(root, () => new Date('2026-08-18T00:00:00Z'))
    const tools = new CountingToolRuntime()
    tools.register(definition('surface.model-reject', 'model-reject'))
    const modelError = new Error('adapter unavailable')
    const generate = vi.fn(async () => { throw modelError })
    const agent = new StandardAgent({ store, model: { generate }, tools, approval })

    const error = await agent.invoke({
      session_id: 'model-reject', scope, input: 'call adapter', system_prompt: 'persist first',
    }).catch((cause: unknown) => cause)

    expect(error).toBe(modelError)
    expect(generate).toHaveBeenCalledOnce()
    expect(tools.listCalls).toBe(1)
    const events = await store.read('model-reject')
    expect(events.filter((event) => event.type === 'model.requested')).toHaveLength(1)
    expect(events.find((event) => event.type === 'model.requested')?.payload.tools).toBeDefined()
    expect(events.filter((event) => event.type === 'model.failed')).toEqual([
      expect.objectContaining({ payload: { error: 'adapter unavailable' } }),
    ])
  })

  it('continues a partial idempotent retry only when durable and current tool surfaces match', async () => {
    const root = await mkdtemp(join(tmpdir(), 'doai-model-retry-match-'))
    const store = new JsonlSessionEventStore(root, () => new Date('2026-08-18T00:00:00Z'))
    const mutation = { request_id: 'request-retry-match', idempotency_key: 'retry-match', session_revision: 0 }
    const seededTools = new ToolRuntime()
    seededTools.register(definition('surface.retry-match', 'same'))
    await seedPartialModelRequest(
      store,
      'retry-match',
      mutation,
      structuredClone(seededTools.list()) as unknown as JsonValue,
    )
    const tools = new CountingToolRuntime()
    tools.register(definition('surface.retry-match', 'same'))
    const requests: RecordedRequest[] = []
    const model: ModelProvider = {
      async generate(request) {
        requests.push(recordRequest(request))
        return { content: 'retry complete', tool_calls: [] }
      },
    }
    const agent = new StandardAgent({ store, model, tools, approval })

    await agent.invoke({
      session_id: 'retry-match', scope, input: 'retry input', system_prompt: 'retry system', mutation,
    })

    const events = await store.read('retry-match')
    expect(tools.listCalls).toBe(1)
    expect(requests).toHaveLength(1)
    expect(events.filter((event) => event.type === 'model.requested')).toHaveLength(1)
    expect(projectDurableRequests(events)).toEqual(requests)
    expect(events.at(-1)).toMatchObject({ type: 'agent.completed', payload: { content: 'retry complete' } })
  })

  it('fails a partial idempotent retry before the model when durable and current tools differ', async () => {
    const root = await mkdtemp(join(tmpdir(), 'doai-model-retry-mismatch-'))
    const store = new JsonlSessionEventStore(root, () => new Date('2026-08-18T00:00:00Z'))
    const mutation = { request_id: 'request-retry-mismatch', idempotency_key: 'retry-mismatch', session_revision: 0 }
    const seededTools = new ToolRuntime()
    seededTools.register(definition('surface.persisted', 'PERSISTED_SENTINEL'))
    const persistedTools = structuredClone(seededTools.list()) as unknown as JsonValue
    await seedPartialModelRequest(store, 'retry-mismatch', mutation, persistedTools)
    const tools = new CountingToolRuntime()
    tools.register(definition('surface.current', 'CURRENT_SENTINEL'))
    const generate = vi.fn(async () => ({ content: 'must not run', tool_calls: [] }))
    const agent = new StandardAgent({ store, model: { generate }, tools, approval })

    const error = await agent.invoke({
      session_id: 'retry-mismatch', scope, input: 'retry input', system_prompt: 'retry system', mutation,
    }).catch((cause: unknown) => cause)

    expect(error).toMatchObject({
      name: 'ModelRequestToolSurfaceError', code: 'MODEL_REQUEST_TOOLS_DURABLE_MISMATCH', pointer: '/tools',
    })
    expect((error as Error).message).toBe('durable model request tools do not match current tool surface')
    expect((error as Error).cause).toBeUndefined()
    expect(tools.listCalls).toBe(1)
    expect(generate).not.toHaveBeenCalled()
    const events = await store.read('retry-mismatch')
    const requested = events.filter((event) => event.type === 'model.requested')
    expect(requested).toHaveLength(1)
    expect(requested[0]!.payload.tools).toEqual(persistedTools)
    expect(events.filter((event) => event.type === 'model.completed' || event.type === 'model.failed')).toHaveLength(0)
    expect(events.at(-1)).toMatchObject({
      type: 'agent.failed',
      payload: { error: 'durable model request tools do not match current tool surface' },
    })
    expect(observableText({ error, failed: events.at(-1) })).not.toMatch(/PERSISTED_SENTINEL|CURRENT_SENTINEL/)
  })

  it('fails a partial retry when the same schema values have different own-key order', async () => {
    const root = await mkdtemp(join(tmpdir(), 'doai-model-retry-key-order-'))
    const store = new JsonlSessionEventStore(root, () => new Date('2026-08-18T00:00:00Z'))
    const mutation = { request_id: 'request-retry-key-order', idempotency_key: 'retry-key-order', session_revision: 0 }
    const persistedRuntime = new ToolRuntime()
    const persisted = definition('surface.key-order', 'same')
    persistedRuntime.register(persisted)
    const persistedTools = structuredClone(persistedRuntime.list()) as unknown as JsonValue
    await seedPartialModelRequest(store, 'retry-key-order', mutation, persistedTools)
    const current = definition('surface.key-order', 'same')
    const schema = current.input_schema
    current.input_schema = {
      properties: schema.properties,
      required: schema.required,
      additionalProperties: schema.additionalProperties,
      type: schema.type,
    }
    const tools = new CountingToolRuntime()
    tools.register(current)
    const generate = vi.fn(async () => ({ content: 'must not run', tool_calls: [] }))
    const agent = new StandardAgent({ store, model: { generate }, tools, approval })

    const error = await agent.invoke({
      session_id: 'retry-key-order', scope, input: 'retry input', system_prompt: 'retry system', mutation,
    }).catch((cause: unknown) => cause)

    expect(error).toMatchObject({
      name: 'ModelRequestToolSurfaceError', code: 'MODEL_REQUEST_TOOLS_DURABLE_MISMATCH', pointer: '/tools',
    })
    expect(generate).not.toHaveBeenCalled()
    expect(tools.listCalls).toBe(1)
    const events = await store.read('retry-key-order')
    expect(events.filter((event) => event.type === 'model.requested')).toHaveLength(1)
    expect(events.filter((event) => event.type === 'model.completed' || event.type === 'model.failed')).toHaveLength(0)
    expect(events.at(-1)).toMatchObject({
      type: 'agent.failed',
      payload: { error: 'durable model request tools do not match current tool surface' },
    })
  })

  it.each([
    { label: 'missing', durable: undefined },
    {
      label: 'invalid shape',
      durable: [{ name: 'surface.retry-invalid', description: 7, input_schema: {} }] as unknown as JsonValue,
    },
  ])('fails safely when a partial retry returns $label durable tools', async ({ label, durable }) => {
    const root = await mkdtemp(join(tmpdir(), 'doai-model-retry-invalid-'))
    const store = new JsonlSessionEventStore(root, () => new Date('2026-08-18T00:00:00Z'))
    const mutation = {
      request_id: `request-retry-${label}`,
      idempotency_key: `retry-${label.replace(' ', '-')}`,
      session_revision: 0,
    }
    await seedPartialModelRequest(store, mutation.idempotency_key, mutation, durable)
    const tools = new CountingToolRuntime()
    tools.register(definition('surface.retry-invalid', 'CURRENT_RETRY_SENTINEL'))
    const generate = vi.fn(async () => ({ content: 'must not run', tool_calls: [] }))
    const agent = new StandardAgent({ store, model: { generate }, tools, approval })

    const error = await agent.invoke({
      session_id: mutation.idempotency_key,
      scope,
      input: 'retry input',
      system_prompt: 'retry system',
      mutation,
    }).catch((cause: unknown) => cause)

    expect(error).toMatchObject({
      name: 'ModelRequestToolSurfaceError', code: 'MODEL_REQUEST_TOOLS_DURABLE_MISMATCH', pointer: '/tools',
    })
    expect((error as Error).message).toBe('durable model request tools do not match current tool surface')
    expect((error as Error).cause).toBeUndefined()
    expect(tools.listCalls).toBe(1)
    expect(generate).not.toHaveBeenCalled()
    const events = await store.read(mutation.idempotency_key)
    expect(events.filter((event) => event.type === 'model.requested')).toHaveLength(1)
    expect(events.filter((event) => event.type === 'model.completed' || event.type === 'model.failed')).toHaveLength(0)
    expect(events.at(-1)).toMatchObject({
      type: 'agent.failed',
      payload: { error: 'durable model request tools do not match current tool surface' },
    })
    expect(observableText({ error, failed: events.at(-1) })).not.toContain('CURRENT_RETRY_SENTINEL')
  })

  it.each([
    {
      label: 'name',
      mutate(tool: ToolDefinition) { (tool as unknown as { name: unknown }).name = 7 },
    },
    {
      label: 'description',
      mutate(tool: ToolDefinition) { (tool as unknown as { description: unknown }).description = false },
    },
    {
      label: 'input_schema',
      mutate(tool: ToolDefinition) { (tool as unknown as { input_schema: unknown }).input_schema = [] },
    },
  ])('rejects an invalid required tool $label before persistence/model', async ({ label, mutate }) => {
    const root = await mkdtemp(join(tmpdir(), 'doai-model-shape-invalid-'))
    const store = new JsonlSessionEventStore(root, () => new Date('2026-08-18T00:00:00Z'))
    const tools = new CountingToolRuntime()
    const invalid = definition('surface.shape-invalid', 'SHAPE_SENTINEL')
    tools.register(invalid)
    mutate(invalid)
    const generate = vi.fn(async () => ({ content: 'must not run', tool_calls: [] }))
    const agent = new StandardAgent({ store, model: { generate }, tools, approval })

    const error = await agent.invoke({
      session_id: `shape-${label}`, scope, input: 'invalid shape', system_prompt: 'reject shape',
    }).catch((cause: unknown) => cause)

    expect(error).toMatchObject({
      name: 'ModelRequestToolSurfaceError', code: 'MODEL_REQUEST_TOOLS_INVALID', pointer: `/tools/0/${label}`,
    })
    expect((error as Error).cause).toBeUndefined()
    expect(tools.listCalls).toBe(1)
    expect(generate).not.toHaveBeenCalled()
    const events = await store.read(`shape-${label}`)
    expect(events.filter((event) => event.type === 'model.requested')).toHaveLength(0)
    expect(events.at(-1)).toMatchObject({ type: 'agent.failed' })
    expect(observableText({ error, events })).not.toContain('SHAPE_SENTINEL')
  })

  it.each([
    { label: 'undefined', poison: (schema: Record<PropertyKey, unknown>) => { schema.poison = undefined }, pointer: '/tools/0/input_schema/<property>' },
    { label: 'bigint', poison: (schema: Record<PropertyKey, unknown>) => { schema.poison = 1n }, pointer: '/tools/0/input_schema/<property>' },
    { label: 'function', poison: (schema: Record<PropertyKey, unknown>) => { schema.poison = () => {} }, pointer: '/tools/0/input_schema/<property>' },
    { label: 'symbol value', poison: (schema: Record<PropertyKey, unknown>) => { schema.poison = Symbol('private') }, pointer: '/tools/0/input_schema/<property>' },
    { label: 'NaN', poison: (schema: Record<PropertyKey, unknown>) => { schema.poison = Number.NaN }, pointer: '/tools/0/input_schema/<property>' },
    { label: 'Infinity', poison: (schema: Record<PropertyKey, unknown>) => { schema.poison = Number.POSITIVE_INFINITY }, pointer: '/tools/0/input_schema/<property>' },
    { label: '-Infinity', poison: (schema: Record<PropertyKey, unknown>) => { schema.poison = Number.NEGATIVE_INFINITY }, pointer: '/tools/0/input_schema/<property>' },
    { label: '-0', poison: (schema: Record<PropertyKey, unknown>) => { schema.poison = -0 }, pointer: '/tools/0/input_schema/<property>' },
    { label: 'sparse array', poison: (schema: Record<PropertyKey, unknown>) => { schema.poison = new Array(1) }, pointer: '/tools/0/input_schema/<property>/0' },
    {
      label: 'array extra property',
      poison: (schema: Record<PropertyKey, unknown>) => {
        const array = [true] as unknown[] & Record<string, unknown>
        array.ignored = 'private array value'
        schema.poison = array
      },
      pointer: '/tools/0/input_schema/<property>/<property>',
    },
    {
      label: 'accessor',
      poison: (schema: Record<PropertyKey, unknown>) => {
        Object.defineProperty(schema, 'poison', { enumerable: true, get: () => 'private accessor value' })
      },
      pointer: '/tools/0/input_schema/<property>',
    },
    {
      label: 'non-enumerable property',
      poison: (schema: Record<PropertyKey, unknown>) => {
        Object.defineProperty(schema, 'poison', { enumerable: false, value: 'private hidden value' })
      },
      pointer: '/tools/0/input_schema/<property>',
    },
    { label: 'symbol key', poison: (schema: Record<PropertyKey, unknown>) => { schema[Symbol('private')] = true }, pointer: '/tools/0/input_schema' },
    { label: 'cycle', poison: (schema: Record<PropertyKey, unknown>) => { schema.poison = schema }, pointer: '/tools/0/input_schema/<property>' },
    { label: 'non-plain object', poison: (schema: Record<PropertyKey, unknown>) => { schema.poison = new Date(0) }, pointer: '/tools/0/input_schema/<property>' },
  ])('fails before model/request persistence for JSON-loss surface: $label', async ({ label, poison, pointer }) => {
    const root = await mkdtemp(join(tmpdir(), 'doai-model-invalid-'))
    const store = new JsonlSessionEventStore(root, () => new Date('2026-08-18T00:00:00Z'))
    const tools = new CountingToolRuntime()
    const invalid = definition('surface.invalid', 'safe-marker')
    tools.register(invalid)
    poison(invalid.input_schema as Record<PropertyKey, unknown>)
    const generate = vi.fn(async () => ({ content: 'must not run', tool_calls: [] }))
    const agent = new StandardAgent({ store, model: { generate }, tools, approval })
    const sessionId = `invalid-${label.toLowerCase().replaceAll(/[^a-z0-9]+/g, '-')}`

    const error = await agent.invoke({
      session_id: sessionId,
      scope,
      input: 'invalid surface',
      system_prompt: 'reject before model',
    }).catch((cause: unknown) => cause)

    expect(error).toMatchObject({
      name: 'ModelRequestToolSurfaceError', code: 'MODEL_REQUEST_TOOLS_INVALID', pointer,
    })
    expect((error as Error).message).toBe(`model request tool surface is not JSON-safe at ${pointer}`)
    expect((error as Error).cause).toBeUndefined()
    expect(generate).not.toHaveBeenCalled()
    expect(tools.listCalls).toBe(1)
    const events = await store.read(sessionId)
    expect(events.filter((event) => event.type === 'model.requested')).toHaveLength(0)
    expect(events.filter((event) => event.type === 'model.completed' || event.type === 'model.failed')).toHaveLength(0)
    expect(events.at(-1)).toMatchObject({
      type: 'agent.failed', payload: { error: `model request tool surface is not JSON-safe at ${pointer}` },
    })
    expect(observableText({ error, events })).not.toContain('private')
  })

  it.each([
    {
      label: 'accessor getter',
      mutate(schema: Record<PropertyKey, unknown>, touched: () => void) {
        Object.defineProperty(schema, 'SCHEMA_KEY_SECRET', {
          enumerable: true,
          get() {
            touched()
            throw new Error('RAW_DESCRIPTOR_SENTINEL')
          },
        })
      },
      pointer: '/tools/0/input_schema/<property>',
    },
    {
      label: 'proxy descriptor failure',
      mutate(_schema: Record<PropertyKey, unknown>, touched: () => void, tool: ToolDefinition) {
        tool.input_schema = new Proxy({}, {
          ownKeys() {
            touched()
            throw new Error('RAW_DESCRIPTOR_SENTINEL')
          },
        })
      },
      pointer: '/tools/0/input_schema',
    },
  ])('recursively redacts unsafe descriptor failures: $label', async ({ mutate, pointer }) => {
    const root = await mkdtemp(join(tmpdir(), 'doai-model-redaction-'))
    const store = new JsonlSessionEventStore(root, () => new Date('2026-08-18T00:00:00Z'))
    const tools = new CountingToolRuntime()
    const invalid = definition('surface.redaction', 'safe-marker')
    tools.register(invalid)
    let touches = 0
    mutate(invalid.input_schema as Record<PropertyKey, unknown>, () => { touches += 1 }, invalid)
    const generate = vi.fn(async () => ({ content: 'must not run', tool_calls: [] }))
    const agent = new StandardAgent({ store, model: { generate }, tools, approval })

    const error = await agent.invoke({
      session_id: `redaction-${pointer.length}`, scope, input: 'redact', system_prompt: 'no raw values',
    }).catch((cause: unknown) => cause)
    const events = await store.read(`redaction-${pointer.length}`)

    expect(error).toMatchObject({
      name: 'ModelRequestToolSurfaceError', code: 'MODEL_REQUEST_TOOLS_INVALID', pointer,
    })
    expect((error as Error).cause).toBeUndefined()
    expect(generate).not.toHaveBeenCalled()
    expect(events.filter((event) => event.type === 'model.requested')).toHaveLength(0)
    expect(observableText({ error, events })).not.toMatch(/SCHEMA_KEY_SECRET|RAW_DESCRIPTOR_SENTINEL/)
    if (pointer.endsWith('<property>')) expect(touches).toBe(0)
    else expect(touches).toBe(1)
  })
})
