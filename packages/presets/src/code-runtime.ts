import { spawn } from 'node:child_process'

import type { JsonValue } from '@doai/protocol'
import ts from 'typescript'

export interface CodeEvaluation {
  code: string
  bindings: { [key: string]: JsonValue }
  timeoutMs?: number
}

export interface CodeRuntime {
  readonly language: 'python' | 'typescript'
  evaluate(request: CodeEvaluation): Promise<JsonValue>
}

const PYTHON_WRAPPER = String.raw`
import json, sys
request = json.load(sys.stdin)
safe = {"len": len, "range": range, "sum": sum, "min": min, "max": max,
        "sorted": sorted, "enumerate": enumerate, "zip": zip,
        "str": str, "int": int, "float": float, "bool": bool}
locals_ = {"bindings": request["bindings"]}
exec(compile(request["code"], "<doai-code>", "exec"), {"__builtins__": safe}, locals_)
json.dump(locals_.get("result"), sys.stdout, ensure_ascii=False, separators=(",", ":"))
`

const NODE_WRAPPER = String.raw`
const vm = require('node:vm');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  try {
    const request = JSON.parse(input);
    const context = vm.createContext({ bindings: structuredClone(request.bindings) });
    const script = new vm.Script(request.javascript + '\n;__user(bindings)', { filename: '<doai-code>' });
    const value = await script.runInContext(context, { timeout: request.timeoutMs });
    process.stdout.write(JSON.stringify(value));
  } catch (error) {
    process.stderr.write(error && error.stack ? error.stack : String(error));
    process.exitCode = 1;
  }
});
`

async function runJsonProcess(
  command: string,
  args: string[],
  input: unknown,
  timeoutMs: number,
): Promise<JsonValue> {
  return await new Promise((resolveProcess, reject) => {
    const child = spawn(command, args, {
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
      },
    })
    let stdout = ''
    let stderr = ''
    const timer = setTimeout(() => {
      child.kill()
      reject(new Error(`code runtime timed out after ${timeoutMs}ms`))
    }, timeoutMs + 250)
    child.stdout.setEncoding('utf8').on('data', (chunk: string) => {
      stdout += chunk
      if (stdout.length > 1_000_000) child.kill()
    })
    child.stderr.setEncoding('utf8').on('data', (chunk: string) => {
      stderr += chunk
      if (stderr.length > 64_000) child.kill()
    })
    child.once('error', (error) => { clearTimeout(timer); reject(error) })
    child.once('close', (code) => {
      clearTimeout(timer)
      if (code !== 0) reject(new Error(`code runtime failed: ${stderr.slice(-4_000)}`))
      else {
        try { resolveProcess(JSON.parse(stdout) as JsonValue) }
        catch { reject(new Error('code runtime returned invalid JSON')) }
      }
    })
    child.stdin.end(JSON.stringify(input))
  })
}

export class PythonCodeRuntime implements CodeRuntime {
  readonly language = 'python' as const

  constructor(readonly executable: string) {}

  async evaluate(request: CodeEvaluation): Promise<JsonValue> {
    const timeoutMs = request.timeoutMs ?? 2_000
    return await runJsonProcess(
      this.executable,
      ['-I', '-S', '-B', '-c', PYTHON_WRAPPER],
      { code: request.code, bindings: request.bindings },
      timeoutMs,
    )
  }
}

export class TypeScriptCodeRuntime implements CodeRuntime {
  readonly language = 'typescript' as const

  async evaluate(request: CodeEvaluation): Promise<JsonValue> {
    const timeoutMs = request.timeoutMs ?? 2_000
    const javascript = ts.transpileModule(`const __user = ${request.code}`, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None, strict: true },
      reportDiagnostics: true,
    })
    const errors = javascript.diagnostics?.filter((item) => item.category === ts.DiagnosticCategory.Error) ?? []
    if (errors.length > 0) throw new Error(`TypeScript compilation failed: ${errors.map((item) => item.messageText).join('; ')}`)
    return await runJsonProcess(
      process.execPath,
      ['-e', NODE_WRAPPER],
      { javascript: javascript.outputText, bindings: request.bindings, timeoutMs },
      timeoutMs,
    )
  }
}
