// Unit tests for moving unreadable files to the trash (D363). Run with `npm run test:unit`. The
// trash is a stand-in that records what it was given and removes it.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { trashUnreadable, unreadableDir, unreadableFiles } from './unreadable.ts'

function vault(files: string[]): string {
  const v = mkdtempSync(join(tmpdir(), 'wd-unreadable-'))
  mkdirSync(unreadableDir(v), { recursive: true })
  for (const f of files) writeFileSync(join(unreadableDir(v), f), f)
  return v
}

function fakeTrash(): { trash: (p: string) => Promise<void>; got: string[] } {
  const got: string[] = []
  return { got, trash: async (p) => { got.push(p); rmSync(p) } }
}

test('trashes each unreadable file with its sidecar and nothing else', async () => {
  const v = vault(['a.pdf', 'a.pdf.yml', 'b.docx', '.DS_Store'])
  writeFileSync(join(v, 'incoming', 'waiting.pdf'), 'w')
  const { trash, got } = fakeTrash()
  assert.equal(await trashUnreadable(v, trash), 2)
  const d = unreadableDir(v)
  assert.deepEqual(got, [join(d, 'a.pdf'), join(d, 'a.pdf.yml'), join(d, 'b.docx')])
  assert.ok(existsSync(join(d, '.DS_Store')))
  assert.ok(existsSync(join(v, 'incoming', 'waiting.pdf')))
  rmSync(v, { recursive: true })
})

test('a link is never followed out of the folder', async () => {
  const v = vault([])
  const outside = join(v, 'elsewhere.pdf')
  writeFileSync(outside, 'keep')
  symlinkSync(outside, join(unreadableDir(v), 'link.pdf'))
  assert.deepEqual(unreadableFiles(v), [])
  const { trash, got } = fakeTrash()
  assert.equal(await trashUnreadable(v, trash), 0)
  assert.deepEqual(got, [])
  assert.ok(existsSync(outside))
  rmSync(v, { recursive: true })
})

test('nothing set aside is nothing to do', async () => {
  const v = mkdtempSync(join(tmpdir(), 'wd-unreadable-'))
  assert.equal(await trashUnreadable(v, fakeTrash().trash), 0)
  rmSync(v, { recursive: true })
})
