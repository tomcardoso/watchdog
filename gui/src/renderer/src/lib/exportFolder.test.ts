// Unit tests for the export folder shown in Maintenance. Run with `npm run test:unit`.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { lastExportFolder } from './exportFolder.ts'

const job = (id: string, over: Record<string, unknown>) => ({
  [id]: { op: 'export', vault: '/v/Harbour Files', state: 'done', finished: '2026-10-11T10:00:00', result: null, ...over }
})

test('the folder is the one the export reports, not one built from the slug', () => {
  const jobs = job('a', { result: { output: '/v/Harbour Files/harbour-files-export' } })
  assert.equal(lastExportFolder(jobs, '/v/Harbour Files'), '/v/Harbour Files/harbour-files-export')
})

test('the latest finished export wins; failed, running and other investigations are ignored', () => {
  const jobs = {
    ...job('old', { finished: '2026-10-11T09:00:00', result: { output: '/old' } }),
    ...job('new', { finished: '2026-10-11T11:00:00', result: { output: '/new' } }),
    ...job('failed', { finished: '2026-10-11T12:00:00', state: 'failed', result: { output: '/failed' } }),
    ...job('running', { finished: null, state: 'running', result: null }),
    ...job('elsewhere', { vault: '/other', finished: '2026-10-11T13:00:00', result: { output: '/elsewhere' } }),
    ...job('other-op', { op: 'reindex', finished: '2026-10-11T14:00:00', result: { output: '/x' } })
  }
  assert.equal(lastExportFolder(jobs, '/v/Harbour Files'), '/new')
})

test('no finished export, or a result without a folder, shows nothing', () => {
  assert.equal(lastExportFolder({}, '/v'), null)
  assert.equal(lastExportFolder(job('a', { result: {} }), '/v/Harbour Files'), null)
})

test('another operation is asked for by name', () => {
  const jobs = {
    ...job('graph', { finished: '2026-10-11T12:00:00', result: { output: '/graph' } }),
    ...job('copy', { op: 'troubleshooting-copy', finished: '2026-10-11T11:00:00', result: { output: '/out/copy.zip' } })
  }
  assert.equal(lastExportFolder(jobs, '/v/Harbour Files', 'troubleshooting-copy'), '/out/copy.zip')
  assert.equal(lastExportFolder(jobs, '/v/Harbour Files'), '/graph')
})
