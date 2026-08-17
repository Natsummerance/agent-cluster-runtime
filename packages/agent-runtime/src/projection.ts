import type { JsonValue, SessionEvent } from '@doai/protocol'

export interface ToolCall {
  id: string
  name: string
  arguments: { [key: string]: JsonValue }
}

export type ModelMessage =
  | { role: 'system' | 'user'; content: string }
  | { role: 'assistant'; content: string; tool_calls?: ToolCall[] }
  | { role: 'tool'; content: string; tool_call_id: string; name: string }

function toolCalls(value: JsonValue | undefined): ToolCall[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => item as unknown as ToolCall)
}

export function projectModelMessages(events: SessionEvent[]): ModelMessage[] {
  const result: ModelMessage[] = []
  for (const event of events) {
    if (event.type === 'agent.system-prompt') {
      result.push({ role: 'system', content: String(event.payload.content ?? '') })
    } else if (event.type === 'input.received') {
      result.push({ role: 'user', content: String(event.payload.content ?? '') })
    } else if (event.type === 'model.completed') {
      const calls = toolCalls(event.payload.tool_calls)
      result.push({
        role: 'assistant',
        content: String(event.payload.content ?? ''),
        ...(calls.length === 0 ? {} : { tool_calls: calls }),
      })
    } else if (event.type === 'tool.completed') {
      result.push({
        role: 'tool',
        content: String(event.payload.result ?? ''),
        tool_call_id: String(event.payload.tool_call_id ?? ''),
        name: String(event.payload.name ?? ''),
      })
    } else if (event.type === 'tool.failed') {
      result.push({
        role: 'tool',
        content: `error: ${String(event.payload.error ?? 'tool failed')}`,
        tool_call_id: String(event.payload.tool_call_id ?? ''),
        name: String(event.payload.name ?? ''),
      })
    } else if (event.type === 'approval.resolved') {
      result.push({
        role: 'system',
        content: `Approval for ${String(event.payload.tool_call_id ?? '')}: ${event.payload.approved ? 'approved' : 'denied'} (${String(event.payload.reason ?? '')})`,
      })
    }
  }
  return result
}
