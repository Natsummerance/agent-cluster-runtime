import type { JsonValue } from '@doai/protocol'

import type { ModelMessage, ToolCall } from './projection.ts'
import type { ModelProvider, ModelResult } from './agent.ts'

export interface CredentialResolver {
  resolve(handle: string): Promise<string>
}

export interface OpenAICompatibleModelOptions {
  baseUrl: string
  model: string
  credentialHandle: string
  credentials: CredentialResolver
  fetch?: typeof globalThis.fetch
}

interface OpenAIToolCall {
  id?: string
  function?: { name?: string; arguments?: string }
}

interface OpenAIResponse {
  choices?: Array<{
    message?: {
      content?: string | null
      tool_calls?: OpenAIToolCall[]
    }
  }>
}

function encodeMessages(messages: ModelMessage[]): unknown[] {
  return messages.map((message) => {
    if (message.role === 'assistant') {
      return {
        role: message.role,
        content: message.content,
        ...(message.tool_calls === undefined ? {} : {
          tool_calls: message.tool_calls.map((call) => ({
            id: call.id,
            type: 'function',
            function: { name: call.name, arguments: JSON.stringify(call.arguments) },
          })),
        }),
      }
    }
    return message
  })
}

function decodeToolCall(value: OpenAIToolCall, index: number): ToolCall {
  const name = value.function?.name
  if (name === undefined || name === '') throw new Error(`model returned tool call ${index} without a name`)
  let arguments_: unknown
  try {
    arguments_ = JSON.parse(value.function?.arguments ?? '{}')
  } catch {
    throw new Error(`model returned invalid JSON arguments for tool: ${name}`)
  }
  if (arguments_ === null || typeof arguments_ !== 'object' || Array.isArray(arguments_)) {
    throw new Error(`model returned non-object arguments for tool: ${name}`)
  }
  return {
    id: value.id ?? `call-${index}`,
    name,
    arguments: arguments_ as { [key: string]: JsonValue },
  }
}

export class OpenAICompatibleModelProvider implements ModelProvider {
  readonly #fetch: typeof globalThis.fetch

  constructor(readonly options: OpenAICompatibleModelOptions) {
    this.#fetch = options.fetch ?? globalThis.fetch
  }

  async generate(request: Parameters<ModelProvider['generate']>[0]): Promise<ModelResult> {
    const credential = await this.options.credentials.resolve(this.options.credentialHandle)
    const response = await this.#fetch(`${this.options.baseUrl.replace(/\/$/, '')}/chat/completions`, {
      method: 'POST',
      headers: {
        authorization: `Bearer ${credential}`,
        'content-type': 'application/json',
      },
      body: JSON.stringify({
        model: this.options.model,
        messages: encodeMessages(request.messages),
        tools: request.tools.map((tool) => ({
          type: 'function',
          function: { name: tool.name, description: tool.description, parameters: tool.input_schema },
        })),
      }),
      ...(request.signal === undefined ? {} : { signal: request.signal }),
    })
    if (!response.ok) throw new Error(`model request failed with HTTP ${response.status}`)
    const payload = await response.json() as OpenAIResponse
    const message = payload.choices?.[0]?.message
    if (message === undefined) throw new Error('model response has no assistant message')
    return {
      content: message.content ?? '',
      tool_calls: (message.tool_calls ?? []).map(decodeToolCall),
    }
  }
}

export class EnvironmentCredentialResolver implements CredentialResolver {
  constructor(readonly handles: Record<string, string>) {}

  async resolve(handle: string): Promise<string> {
    const environmentName = this.handles[handle]
    if (environmentName === undefined) throw new Error(`credential handle is not configured: ${handle}`)
    const value = process.env[environmentName]
    if (value === undefined || value === '') throw new Error(`credential is missing: ${handle}`)
    return value
  }
}
