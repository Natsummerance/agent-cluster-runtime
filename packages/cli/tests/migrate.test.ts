import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { migrateLegacySessions } from '../src/migrate.ts'

describe('one-time v1 migration', () => {
  it('dry-run reports conversion without writing', async () => {
    const root = await mkdtemp(join(tmpdir(), 'doai-migrate-dry-'))
    const source = join(root, 'legacy')
    const target = join(root, 'v1')
    await mkdir(join(source, 's1'), { recursive: true })
    await writeFile(join(source, 's1', 'session.json'), JSON.stringify({
      session_id: 's1', project_id: 'p1', goal: 'hello', status: 'completed',
      transcript: [{ question: 'q', answer: 'a' }], gate_decisions: [],
    }), 'utf8')

    const report = await migrateLegacySessions({ source, target, mode: 'dry-run' })

    expect(report).toMatchObject({ mode: 'dry-run', discovered: 1, converted: 1, written: 0 })
    await expect(readFile(join(target, 's1.jsonl'), 'utf8')).rejects.toMatchObject({ code: 'ENOENT' })
  })

  it('applies canonical events and is idempotent on rerun', async () => {
    const root = await mkdtemp(join(tmpdir(), 'doai-migrate-apply-'))
    const source = join(root, 'legacy')
    const target = join(root, 'v1')
    await mkdir(join(source, 's1'), { recursive: true })
    await writeFile(join(source, 's1', 'session.json'), JSON.stringify({
      session_id: 's1', project_id: 'p1', goal: 'hello', status: 'completed', workspace: root,
      transcript: [{ question: 'q', answer: 'a' }],
      gate_decisions: [{ kind: 'release', last_decision: 'approve' }],
    }), 'utf8')

    const first = await migrateLegacySessions({ source, target, mode: 'apply' })
    const second = await migrateLegacySessions({ source, target, mode: 'apply' })
    const events = (await readFile(join(target, 's1.jsonl'), 'utf8')).trim().split('\n').map((line) => JSON.parse(line))

    expect(first).toMatchObject({ converted: 1, written: 1, validated: 1 })
    expect(second).toMatchObject({ converted: 1, written: 0, skipped: 1 })
    expect(events.map((event) => event.seq)).toEqual([1, 2, 3, 4])
    expect(events.map((event) => event.type)).toEqual([
      'session.created', 'input.received', 'model.completed', 'approval.resolved',
    ])
  })

  it('rolls back every output when validation fails', async () => {
    const root = await mkdtemp(join(tmpdir(), 'doai-migrate-rollback-'))
    const source = join(root, 'legacy')
    const target = join(root, 'v1')
    await mkdir(join(source, 'good'), { recursive: true })
    await mkdir(join(source, 'second'), { recursive: true })
    await writeFile(join(source, 'good', 'session.json'), JSON.stringify({ session_id: 'good' }), 'utf8')
    await writeFile(join(source, 'second', 'session.json'), JSON.stringify({ session_id: 'second' }), 'utf8')
    await mkdir(join(target, 'second.jsonl'), { recursive: true })

    await expect(migrateLegacySessions({ source, target, mode: 'apply' })).rejects.toBeDefined()
    await expect(readFile(join(target, 'good.jsonl'), 'utf8')).rejects.toMatchObject({ code: 'ENOENT' })
  })

  it('rejects overlapping roots and duplicate session ids', async () => {
    const root = await mkdtemp(join(tmpdir(), 'doai-migrate-safety-'))
    const source = join(root, 'legacy')
    await mkdir(join(source, 'one'), { recursive: true })
    await mkdir(join(source, 'two'), { recursive: true })
    await writeFile(join(source, 'one', 'session.json'), JSON.stringify({ session_id: 'same' }), 'utf8')
    await writeFile(join(source, 'two', 'session.json'), JSON.stringify({ session_id: 'same' }), 'utf8')

    await expect(migrateLegacySessions({ source, target: join(source, 'output'), mode: 'dry-run' }))
      .rejects.toThrow('must not contain')
    await expect(migrateLegacySessions({ source, target: join(root, 'v1'), mode: 'dry-run' }))
      .rejects.toThrow('duplicate legacy session id')
  })
})
