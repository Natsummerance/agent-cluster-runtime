import { copyFile, mkdir, readFile, readdir, rename, rm, writeFile } from 'node:fs/promises'
import { basename, dirname, join, relative, resolve } from 'node:path'

interface LegacySession {
  session_id?: string
  project_id?: string
  goal?: string
  status?: string
  workspace?: string
  created_at?: string
  transcript?: Array<{ question?: string; answer?: string }>
  gate_decisions?: Array<{ kind?: string; last_decision?: string }>
}

export interface MigrationOptions {
  source: string
  target: string
  mode: 'dry-run' | 'apply'
}

export interface MigrationReport {
  mode: MigrationOptions['mode']
  discovered: number
  converted: number
  written: number
  skipped: number
  validated: number
  backup?: string
}

async function discover(directory: string): Promise<string[]> {
  const result: string[] = []
  const walk = async (current: string): Promise<void> => {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      const path = join(current, entry.name)
      if (entry.isDirectory()) await walk(path)
      else if (entry.isFile() && entry.name === 'session.json') result.push(path)
    }
  }
  await walk(directory)
  return result.sort()
}

function convert(session: LegacySession, fallbackId: string): Array<Record<string, unknown>> {
  const sessionId = session.session_id || fallbackId
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(sessionId)) throw new Error(`invalid legacy session id: ${sessionId}`)
  const scope = { tenant_id: 'default', project_id: session.project_id || 'default' }
  const timestamp = session.created_at || new Date().toISOString()
  const drafts: Array<{ type: string; payload: Record<string, unknown> }> = [{
    type: 'session.created',
    payload: { goal: session.goal || '', status: session.status || '', workspace: session.workspace || '' },
  }]
  for (const turn of session.transcript ?? []) {
    drafts.push({ type: 'input.received', payload: { content: turn.question || '' } })
    drafts.push({ type: 'model.completed', payload: { content: turn.answer || '', tool_calls: [] } })
  }
  for (const gate of session.gate_decisions ?? []) {
    drafts.push({
      type: 'approval.resolved',
      payload: {
        gate: gate.kind || '',
        approved: gate.last_decision === 'approve',
        reason: `migrated legacy decision: ${gate.last_decision || 'unknown'}`,
      },
    })
  }
  return drafts.map((draft, index) => ({
    schema_version: '1.0', session_id: sessionId, seq: index + 1,
    type: draft.type, ts: timestamp, scope, payload: draft.payload, ignorable: false,
  }))
}

function validate(events: Array<Record<string, unknown>>, sessionId: string): void {
  if (events.length === 0) throw new Error(`migration produced an empty log: ${sessionId}`)
  events.forEach((event, index) => {
    if (event.schema_version !== '1.0' || event.session_id !== sessionId || event.seq !== index + 1) {
      throw new Error(`migration validation failed for ${sessionId} at seq ${index + 1}`)
    }
  })
}

export async function migrateLegacySessions(options: MigrationOptions): Promise<MigrationReport> {
  const source = resolve(options.source)
  const target = resolve(options.target)
  if (source === target) throw new Error('migration source and target must differ')
  const sourceToTarget = relative(source, target)
  const targetToSource = relative(target, source)
  if (!sourceToTarget.startsWith('..') || !targetToSource.startsWith('..')) {
    throw new Error('migration source and target must not contain one another')
  }
  const files = await discover(source)
  const report: MigrationReport = {
    mode: options.mode, discovered: files.length, converted: 0, written: 0, skipped: 0, validated: 0,
  }
  const converted: Array<{ source: string; target: string; sessionId: string; text: string }> = []
  const sessionIds = new Set<string>()
  for (const path of files) {
    try {
      const legacy = JSON.parse(await readFile(path, 'utf8')) as LegacySession
      const sessionId = legacy.session_id || basename(dirname(path))
      if (sessionIds.has(sessionId)) throw new Error(`duplicate legacy session id: ${sessionId}`)
      sessionIds.add(sessionId)
      const events = convert(legacy, sessionId)
      validate(events, sessionId)
      converted.push({
        source: path,
        target: join(target, `${sessionId}.jsonl`),
        sessionId,
        text: events.map((event) => JSON.stringify(event)).join('\n') + '\n',
      })
      report.converted += 1
    } catch (cause) {
      throw new Error(`failed to convert legacy session ${basename(dirname(path))}: ${cause instanceof Error ? cause.message : String(cause)}`)
    }
  }
  if (options.mode === 'dry-run') return report

  const backup = join(target, '.migration-backup', new Date().toISOString().replace(/[:.]/g, '-'))
  report.backup = backup
  const written: string[] = []
  try {
    await mkdir(target, { recursive: true })
    await mkdir(backup, { recursive: true })
    for (const item of converted) {
      try {
        await readFile(item.target)
        report.skipped += 1
        continue
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
      }
      await copyFile(item.source, join(backup, `${item.sessionId}.session.json`))
      const temporary = `${item.target}.${process.pid}.tmp`
      await writeFile(temporary, item.text, { encoding: 'utf8', flag: 'wx' })
      await rename(temporary, item.target)
      const check = (await readFile(item.target, 'utf8')).trim().split('\n').map((line) => JSON.parse(line) as Record<string, unknown>)
      validate(check, item.sessionId)
      written.push(item.target)
      report.written += 1
      report.validated += 1
    }
    return report
  } catch (cause) {
    await Promise.all(written.map(async (path) => await rm(path, { force: true })))
    throw cause
  }
}
