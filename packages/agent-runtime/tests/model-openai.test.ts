import { describe, expect, it, vi } from 'vitest'

import { OpenAICompatibleModelProvider, type CredentialResolver } from '../src/index.ts'

describe('OpenAICompatibleModelProvider', () => {
  it('resolves an opaque credential and translates tool calls', async () => {
    const credentials: CredentialResolver = { resolve: vi.fn().mockResolvedValue('top-secret') }
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      choices: [{ message: { content: null, tool_calls: [{
        id: 'call-1', function: { name: 'workspace.read', arguments: '{"path":"README.md"}' },
      }] } }],
    }), { status: 200, headers: { 'content-type': 'application/json' } }))
    const provider = new OpenAICompatibleModelProvider({
      baseUrl: 'https://model.invalid/v1/', model: 'deepseek-chat', credentialHandle: 'deepseek', credentials, fetch,
    })

    const result = await provider.generate({
      messages: [{ role: 'user', content: 'read' }],
      tools: [{ name: 'workspace.read', description: 'read', input_schema: { type: 'object' } }],
    })

    expect(credentials.resolve).toHaveBeenCalledWith('deepseek')
    expect(fetch).toHaveBeenCalledWith('https://model.invalid/v1/chat/completions', expect.objectContaining({
      headers: expect.objectContaining({ authorization: 'Bearer top-secret' }),
    }))
    expect(result.tool_calls).toEqual([{ id: 'call-1', name: 'workspace.read', arguments: { path: 'README.md' } }])
  })

  it('fails structurally without echoing response bodies or credentials', async () => {
    const provider = new OpenAICompatibleModelProvider({
      baseUrl: 'https://model.invalid/v1', model: 'x', credentialHandle: 'key',
      credentials: { resolve: async () => 'secret-value' },
      fetch: vi.fn().mockResolvedValue(new Response('sensitive upstream body', { status: 401 })),
    })

    await expect(provider.generate({ messages: [], tools: [] })).rejects.toThrow('HTTP 401')
    await expect(provider.generate({ messages: [], tools: [] })).rejects.not.toThrow('secret-value')
    await expect(provider.generate({ messages: [], tools: [] })).rejects.not.toThrow('sensitive upstream body')
  })
})
