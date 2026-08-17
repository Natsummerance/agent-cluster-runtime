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
} from '../src/index.ts'

const scope = { tenant_id: 'tenant-a', project_id: 'project-a' }

describe('StandardAgent vertical slice', () => {
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

  it('repairs and verifies an existing repository through structured tools', async () => {
    const data = await mkdtemp(join(tmpdir(), 'doai-repair-data-'))
    const workspace = await mkdtemp(join(tmpdir(), 'doai-repair-workspace-'))
    await writeFile(join(workspace, 'calc.cjs'), 'module.exports = (a, b) => a - b\n', 'utf8')
    await writeFile(join(workspace, 'test.cjs'), "const add=require('./calc.cjs'); if(add(2,3)!==5) process.exit(1)\n", 'utf8')
    const store = new JsonlSessionEventStore(data)
    const model: ModelProvider = {
      generate: vi.fn()
        .mockResolvedValueOnce({
          content: '',
          tool_calls: [
            { id: 'fix', name: 'workspace.write', arguments: { path: 'calc.cjs', content: 'module.exports = (a, b) => a + b\n' } },
            { id: 'verify', name: 'process.run', arguments: { argv: [process.execPath, 'test.cjs'] } },
          ],
        })
        .mockResolvedValueOnce({ content: 'Fixed and verified.', tool_calls: [] }),
    }
    const approval: ApprovalService = { request: async () => ({ approved: true, reason: 'test policy' }) }
    const tools = ToolRuntime.withLocalTools(new LocalExecutionWorld(workspace), approval)
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
  })
})
