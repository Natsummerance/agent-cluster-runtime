import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it, vi } from 'vitest'

import {
  JsonlSessionEventStore,
  LocalExecutionWorld,
  StandardAgent,
  ToolRuntime,
  projectModelMessages,
  type ApprovalService,
  type ModelProvider,
  type ToolDefinition,
} from '../src/index.ts'

const scope = { tenant_id: 'tenant-a', project_id: 'project-a' }
const EXECUTABLE_SENTINEL = '<NODE_EXECUTABLE>'
const ORDERED_SNAPSHOT = Symbol('ordered-snapshot')

type ModelRequest = Parameters<ModelProvider['generate']>[0]
type RecordedRequest = Pick<ModelRequest, 'messages' | 'tools'>

expect.addSnapshotSerializer({
  test(value) {
    return value !== null && typeof value === 'object' && value[ORDERED_SNAPSHOT] === true
  },
  serialize(value, config, indentation, depth, refs, printer) {
    return printer(value.value, { ...config, compareKeys: null }, indentation, depth, refs)
  },
})

function orderedSnapshot(value: unknown): { [ORDERED_SNAPSHOT]: true; value: unknown } {
  return { [ORDERED_SNAPSHOT]: true, value }
}

function recordRequest(request: ModelRequest): RecordedRequest {
  return structuredClone({ messages: request.messages, tools: request.tools })
}

function readDurableTools(value: unknown): RecordedRequest['tools'] {
  if (!Array.isArray(value)) throw new Error('model.requested tools must be an array')
  for (const [index, tool] of value.entries()) {
    if (tool === null || typeof tool !== 'object' || Array.isArray(tool)) {
      throw new Error(`model.requested tool ${index} must be a record`)
    }
    expect(Object.keys(tool)).toEqual(['name', 'description', 'input_schema'])
    if (typeof tool.name !== 'string' || typeof tool.description !== 'string') {
      throw new Error(`model.requested tool ${index} must have string identity fields`)
    }
    if (tool.input_schema === null || typeof tool.input_schema !== 'object' || Array.isArray(tool.input_schema)) {
      throw new Error(`model.requested tool ${index} must have a record input_schema`)
    }
  }
  return structuredClone(value) as RecordedRequest['tools']
}

function expectOrderedJson(actual: unknown, expected: unknown, path = '$'): void {
  if (actual === null || expected === null || typeof actual !== 'object' || typeof expected !== 'object') {
    expect(Object.is(actual, expected), `value mismatch at ${path}`).toBe(true)
    return
  }
  if (Array.isArray(actual) || Array.isArray(expected)) {
    expect(Array.isArray(actual), `array mismatch at ${path}`).toBe(true)
    expect(Array.isArray(expected), `array mismatch at ${path}`).toBe(true)
    if (!Array.isArray(actual) || !Array.isArray(expected)) return
    expect(actual.length, `array length mismatch at ${path}`).toBe(expected.length)
    for (let index = 0; index < actual.length; index += 1) {
      expectOrderedJson(actual[index], expected[index], `${path}/${index}`)
    }
    return
  }
  const actualRecord = actual as Record<string, unknown>
  const expectedRecord = expected as Record<string, unknown>
  const actualKeys = Object.keys(actualRecord)
  const expectedKeys = Object.keys(expectedRecord)
  expect(actualKeys, `own-key order mismatch at ${path}`).toEqual(expectedKeys)
  for (const key of expectedKeys) {
    expectOrderedJson(actualRecord[key], expectedRecord[key], `${path}/${key}`)
  }
}

function assertNoExecutable(value: unknown, executable: string, path: string): void {
  if (typeof value === 'string') {
    if (value.includes(executable)) throw new Error(`unexpected executable path at ${path}`)
    return
  }
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index += 1) {
      assertNoExecutable(value[index], executable, `${path}/${index}`)
    }
    return
  }
  if (value !== null && typeof value === 'object') {
    for (const [key, child] of Object.entries(value)) {
      assertNoExecutable(child, executable, `${path}/${key}`)
    }
  }
}

function normalizeRepairRequests(requests: RecordedRequest[], executable: string): {
  requests: Array<RecordedRequest & { request_index: number }>
  replacements: string[]
} {
  const normalized = structuredClone(requests)
  const replacements: string[] = []
  for (let requestIndex = 0; requestIndex < normalized.length; requestIndex += 1) {
    const request = normalized[requestIndex]!
    for (let messageIndex = 0; messageIndex < request.messages.length; messageIndex += 1) {
      const message = request.messages[messageIndex]!
      const messagePath = `requests/${requestIndex}/messages/${messageIndex}`
      assertNoExecutable(message.content, executable, `${messagePath}/content`)
      if (message.role !== 'assistant' || message.tool_calls === undefined) continue
      for (let callIndex = 0; callIndex < message.tool_calls.length; callIndex += 1) {
        const call = message.tool_calls[callIndex]!
        const callPath = `${messagePath}/tool_calls/${callIndex}`
        assertNoExecutable(call.id, executable, `${callPath}/id`)
        assertNoExecutable(call.name, executable, `${callPath}/name`)
        for (const [key, value] of Object.entries(call.arguments)) {
          const argumentPath = `${callPath}/arguments/${key}`
          if (call.name === 'process.run' && key === 'argv' && Array.isArray(value)) {
            for (let argumentIndex = 0; argumentIndex < value.length; argumentIndex += 1) {
              const valuePath = `${argumentPath}/${argumentIndex}`
              if (argumentIndex === 0 && value[argumentIndex] === executable) {
                value[argumentIndex] = EXECUTABLE_SENTINEL
                replacements.push(valuePath)
              } else {
                assertNoExecutable(value[argumentIndex], executable, valuePath)
              }
            }
          } else {
            assertNoExecutable(value, executable, argumentPath)
          }
        }
      }
    }
  }
  return {
    requests: normalized.map((request, request_index) => ({ request_index, ...request })),
    replacements,
  }
}

describe('StandardAgent vertical slice', () => {
  it('normalizes only the allowlisted executable argument field', () => {
    const fixture: RecordedRequest[] = [{
      messages: [
        { role: 'system', content: 'Keep deterministic content verbatim.' },
        {
          role: 'assistant',
          content: '',
          tool_calls: [{ id: 'call-stable', name: 'process.run', arguments: { argv: [process.execPath, 'test.cjs'] } }],
        },
      ],
      tools: [{
        name: 'schema.literal',
        description: 'Schema content is never normalized',
        input_schema: { type: 'string', const: process.execPath },
      }],
    }]

    const normalized = normalizeRepairRequests(fixture, process.execPath)

    expect(normalized.replacements).toEqual(['requests/0/messages/1/tool_calls/0/arguments/argv/0'])
    expect(normalized.requests[0]!.messages[1]).toHaveProperty(
      'tool_calls.0.arguments.argv.0', EXECUTABLE_SENTINEL,
    )
    expect(normalized.requests[0]!.tools[0]!.input_schema).toHaveProperty('const', process.execPath)
    expect(fixture[0]!.messages[1]).toHaveProperty('tool_calls.0.arguments.argv.0', process.execPath)

    const unexpected = structuredClone(fixture)
    unexpected[0]!.messages[0]!.content = `Do not conceal ${process.execPath}`
    expect(() => normalizeRepairRequests(unexpected, process.execPath))
      .toThrow('unexpected executable path at requests/0/messages/0/content')

    expect(() => expectOrderedJson(
      { nested: { first: 1, second: 2 } },
      { nested: { second: 2, first: 1 } },
    )).toThrow('own-key order mismatch at $/nested')
  })

  it('logs every model-visible input, tool call, result, and final answer', async () => {
    const data = await mkdtemp(join(tmpdir(), 'doai-agent-data-'))
    const workspace = await mkdtemp(join(tmpdir(), 'doai-agent-workspace-'))
    await writeFile(join(workspace, 'README.md'), 'hello', 'utf8')
    const store = new JsonlSessionEventStore(data, () => new Date('2026-08-17T00:00:00Z'))
    const model: ModelProvider = {
      generate: vi.fn()
        .mockResolvedValueOnce({ content: '', tool_calls: [{ id: 'call-1', name: 'workspace.read', arguments: { path: 'README.md' } }] })
        .mockResolvedValueOnce({ content: 'README contains hello.', tool_calls: [] }),
    }
    const approval: ApprovalService = { request: vi.fn().mockResolvedValue({ approved: true, reason: 'policy' }) }
    const tools = ToolRuntime.withLocalTools(new LocalExecutionWorld(workspace), approval)
    const agent = new StandardAgent({ store, model, tools, approval, maxSteps: 4 })

    const result = await agent.invoke({
      session_id: 'session-1', scope, input: 'Inspect the README.', system_prompt: 'Be precise.',
    })

    expect(result.content).toBe('README contains hello.')
    const events = await store.read('session-1')
    expect(events.map((event) => event.type)).toEqual([
      'session.created', 'agent.system-prompt', 'input.received', 'agent.started',
      'model.requested', 'model.completed', 'tool.requested', 'tool.completed',
      'model.requested', 'model.completed', 'agent.completed',
    ])
    expect(projectModelMessages(events)).toEqual([
      { role: 'system', content: 'Be precise.' },
      { role: 'user', content: 'Inspect the README.' },
      { role: 'assistant', content: '', tool_calls: [{ id: 'call-1', name: 'workspace.read', arguments: { path: 'README.md' } }] },
      { role: 'tool', content: 'hello', tool_call_id: 'call-1', name: 'workspace.read' },
      { role: 'assistant', content: 'README contains hello.' },
    ])
  })

  it('logs approval before a mutating tool and denies without executing', async () => {
    const data = await mkdtemp(join(tmpdir(), 'doai-agent-deny-'))
    const workspace = await mkdtemp(join(tmpdir(), 'doai-agent-deny-workspace-'))
    const store = new JsonlSessionEventStore(data)
    const model: ModelProvider = {
      generate: vi.fn().mockResolvedValue({
        content: '', tool_calls: [{ id: 'write-1', name: 'workspace.write', arguments: { path: 'x.txt', content: 'x' } }],
      }),
    }
    const approval: ApprovalService = { request: vi.fn().mockResolvedValue({ approved: false, reason: 'denied by user' }) }
    const tools = ToolRuntime.withLocalTools(new LocalExecutionWorld(workspace), approval)
    const agent = new StandardAgent({ store, model, tools, approval, maxSteps: 1 })

    await expect(agent.invoke({ session_id: 'deny', scope, input: 'write', system_prompt: 'safe' }))
      .rejects.toThrow('denied by user')

    expect((await store.read('deny')).map((event) => event.type)).toContain('approval.resolved')
    expect((await store.read('deny')).at(-1)?.type).toBe('agent.failed')
  })

  it('prevents workspace path escape and shell-string execution', async () => {
    const workspace = await mkdtemp(join(tmpdir(), 'doai-world-'))
    const world = new LocalExecutionWorld(workspace)
    await expect(world.read('../secret')).rejects.toThrow('outside workspace')
    await expect(world.run('echo hello' as never)).rejects.toThrow('argv')
  })

  it('repairs and snapshots every durable model-visible request through structured tools', async () => {
    const data = await mkdtemp(join(tmpdir(), 'doai-repair-data-'))
    const workspace = await mkdtemp(join(tmpdir(), 'doai-repair-workspace-'))
    await writeFile(join(workspace, 'calc.cjs'), 'module.exports = (a, b) => a - b\n', 'utf8')
    await writeFile(join(workspace, 'test.cjs'), "const add=require('./calc.cjs'); if(add(2,3)!==5) process.exit(1)\n", 'utf8')
    const store = new JsonlSessionEventStore(data)
    const approval: ApprovalService = { request: async () => ({ approved: true, reason: 'test policy' }) }
    const tools = ToolRuntime.withLocalTools(new LocalExecutionWorld(workspace), approval)
    const transient: ToolDefinition = {
      name: 'diagnostic.transient',
      description: 'Inspect a nested diagnostic payload for this request only',
      input_schema: {
        type: 'object',
        additionalProperties: false,
        required: ['diagnostic'],
        properties: {
          diagnostic: {
            type: 'object',
            additionalProperties: false,
            required: ['labels'],
            properties: {
              labels: { type: 'array', minItems: 1, items: { type: 'string', minLength: 1 } },
            },
          },
        },
      },
      risk: 'read',
      async execute() { return 'unused' },
    }
    const unselected: ToolDefinition = {
      ...transient,
      name: 'diagnostic.never-selected',
      description: 'Constructed but never registered',
    }
    const disposeTransient = tools.register(transient)
    const recordings: RecordedRequest[] = []
    const model: ModelProvider = {
      async generate(request) {
        recordings.push(recordRequest(request))
        if (recordings.length === 1) {
          disposeTransient()
          return {
          content: '',
          tool_calls: [
            { id: 'fix', name: 'workspace.write', arguments: { path: 'calc.cjs', content: 'module.exports = (a, b) => a + b\n' } },
            { id: 'verify', name: 'process.run', arguments: { argv: [process.execPath, 'test.cjs'] } },
          ],
          }
        }
        return { content: 'Fixed and verified.', tool_calls: [] }
      },
    }
    const agent = new StandardAgent({ store, model, tools, approval })

    const result = await agent.invoke({
      session_id: 'repair', scope, input: 'Fix the failing addition test.', system_prompt: 'Use tools and verify.',
    })

    expect(result.content).toBe('Fixed and verified.')
    expect(await (new LocalExecutionWorld(workspace)).read('calc.cjs')).toContain('a + b')
    const events = await store.read('repair')
    expect(events.filter((event) => event.type === 'approval.resolved')).toHaveLength(2)
    expect(events.find((event) => event.type === 'tool.completed' && event.payload.name === 'process.run')?.payload.result)
      .toContain('"exit_code":0')

    const requested = events.filter((event) => event.type === 'model.requested')
    const expectedRequests: RecordedRequest[] = requested.map((requestEvent) => ({
      messages: projectModelMessages(events.filter((event) => event.seq < requestEvent.seq)),
      tools: readDurableTools(requestEvent.payload.tools),
    }))
    expect(recordings.length).toBeGreaterThanOrEqual(2)
    expect(recordings).toHaveLength(requested.length)
    expect(expectedRequests).toHaveLength(recordings.length)
    for (let index = 0; index < recordings.length; index += 1) {
      expectOrderedJson(recordings[index], expectedRequests[index], `requests/${index}`)
    }

    for (const request of recordings) {
      const names = request.tools.map((tool) => tool.name)
      expect(new Set(names).size).toBe(names.length)
      expect(names.filter((name) => name === 'workspace.read')).toHaveLength(1)
      expect(names.filter((name) => name === 'workspace.write')).toHaveLength(1)
      expect(names.filter((name) => name === 'process.run')).toHaveLength(1)
      expect(names).not.toContain(unselected.name)
    }
    expect(recordings[0]!.tools.map((tool) => tool.name)).toContain(transient.name)
    expect(recordings.slice(1).flatMap((request) => request.tools.map((tool) => tool.name)))
      .not.toContain(transient.name)

    const snapshot = normalizeRepairRequests(recordings, process.execPath)
    expect(snapshot.requests).toHaveLength(recordings.length)
    expect(snapshot.replacements).toEqual(['requests/1/messages/2/tool_calls/1/arguments/argv/0'])
    expect(orderedSnapshot(snapshot.requests)).toMatchSnapshot()
  })
})
