import { spawn } from 'node:child_process'
import { realpathSync } from 'node:fs'
import { readFile, realpath, writeFile } from 'node:fs/promises'
import { dirname, isAbsolute, relative, resolve } from 'node:path'

import type { JsonValue } from '@doai/protocol'
import type { EventHub } from '@doai/host'
import Ajv, { type ValidateFunction } from 'ajv'

export type ToolRisk = 'read' | 'write' | 'process'

export interface ToolDefinition {
  name: string
  description: string
  input_schema: Record<string, unknown>
  risk: ToolRisk
  execute(arguments_: { [key: string]: JsonValue }, signal?: AbortSignal): Promise<JsonValue>
}

export interface ApprovalDecision {
  approved: boolean
  reason: string
}

export interface ApprovalService {
  request(request: {
    session_id: string
    tool: string
    risk: ToolRisk
    arguments: { [key: string]: JsonValue }
  }): Promise<ApprovalDecision>
}

export class LocalExecutionWorld {
  readonly #root: string

  constructor(root: string) {
    this.#root = realpathSync(resolve(root))
  }

  async read(path: string): Promise<string> {
    return await readFile(await this.#existingPath(path), 'utf8')
  }

  async write(path: string, content: string): Promise<void> {
    const target = this.#lexicalPath(path)
    const parent = await realpath(dirname(target))
    this.#assertContained(parent)
    await writeFile(target, content, 'utf8')
  }

  async run(argv: string[], signal?: AbortSignal): Promise<{ stdout: string; stderr: string; exit_code: number }> {
    if (!Array.isArray(argv) || argv.length === 0 || argv.some((value) => typeof value !== 'string' || value === '')) {
      throw new Error('process.run requires a non-empty argv array; shell strings are forbidden')
    }
    const [command, ...args] = argv
    return await new Promise((resolveRun, reject) => {
      const child = spawn(command!, args, {
        cwd: this.#root,
        shell: false,
        windowsHide: true,
        signal,
        env: {
          PATH: process.env.PATH,
          PATHEXT: process.env.PATHEXT,
          SystemRoot: process.env.SystemRoot,
          TEMP: process.env.TEMP,
          TMP: process.env.TMP,
        },
      })
      let stdout = ''
      let stderr = ''
      child.stdout.setEncoding('utf8').on('data', (chunk: string) => { stdout += chunk })
      child.stderr.setEncoding('utf8').on('data', (chunk: string) => { stderr += chunk })
      child.once('error', reject)
      child.once('close', (code) => resolveRun({ stdout, stderr, exit_code: code ?? -1 }))
    })
  }

  #lexicalPath(path: string): string {
    if (isAbsolute(path)) throw new Error(`path is outside workspace: ${path}`)
    const candidate = resolve(this.#root, path)
    this.#assertContained(candidate)
    return candidate
  }

  async #existingPath(path: string): Promise<string> {
    const candidate = await realpath(this.#lexicalPath(path))
    this.#assertContained(candidate)
    return candidate
  }

  #assertContained(path: string): void {
    const fromRoot = relative(this.#root, path)
    if (fromRoot === '..' || fromRoot.startsWith(`..\\`) || fromRoot.startsWith('../') || isAbsolute(fromRoot)) {
      throw new Error(`path is outside workspace: ${path}`)
    }
  }
}

export class ToolRuntime {
  readonly #tools = new Map<string, { definition: ToolDefinition; validate: ValidateFunction }>()
  readonly #ajv = new Ajv({ allErrors: true, strict: false })

  constructor(readonly events?: EventHub) {}

  register(definition: ToolDefinition): void {
    if (this.#tools.has(definition.name)) throw new Error(`tool already registered: ${definition.name}`)
    this.#tools.set(definition.name, {
      definition,
      validate: this.#ajv.compile(definition.input_schema),
    })
  }

  get(name: string): ToolDefinition {
    const entry = this.#tools.get(name)
    if (entry === undefined) throw new Error(`unknown tool: ${name}`)
    return entry.definition
  }

  list(): Array<Pick<ToolDefinition, 'name' | 'description' | 'input_schema'>> {
    return [...this.#tools.values()].map(({ definition }) => ({
      name: definition.name,
      description: definition.description,
      input_schema: definition.input_schema,
    }))
  }

  async execute(name: string, arguments_: { [key: string]: JsonValue }, signal?: AbortSignal): Promise<JsonValue> {
    const entry = this.#tools.get(name)
    if (entry === undefined) throw new Error(`unknown tool: ${name}`)
    if (!entry.validate(arguments_)) throw new Error(`invalid arguments for ${name}: ${this.#ajv.errorsText(entry.validate.errors)}`)
    const terminal = async () => await entry.definition.execute(arguments_, signal)
    if (this.events === undefined) return await terminal()
    return await this.events.onion('tool.execute', {
      name,
      risk: entry.definition.risk,
      arguments: arguments_,
    }, terminal)
  }

  static withLocalTools(world: LocalExecutionWorld, _approval: ApprovalService, events?: EventHub): ToolRuntime {
    const runtime = new ToolRuntime(events)
    runtime.register({
      name: 'workspace.read', description: 'Read a UTF-8 file inside the workspace', risk: 'read',
      input_schema: { type: 'object', additionalProperties: false, required: ['path'], properties: { path: { type: 'string' } } },
      async execute(args) { return await world.read(String(args.path)) },
    })
    runtime.register({
      name: 'workspace.write', description: 'Replace a UTF-8 file inside the workspace', risk: 'write',
      input_schema: { type: 'object', additionalProperties: false, required: ['path', 'content'], properties: { path: { type: 'string' }, content: { type: 'string' } } },
      async execute(args) { await world.write(String(args.path), String(args.content)); return { written: String(args.path) } },
    })
    runtime.register({
      name: 'process.run', description: 'Run an argv command without a shell', risk: 'process',
      input_schema: { type: 'object', additionalProperties: false, required: ['argv'], properties: { argv: { type: 'array', minItems: 1, items: { type: 'string', minLength: 1 } } } },
      async execute(args, signal) { return await world.run(args.argv as string[], signal) },
    })
    return runtime
  }
}
