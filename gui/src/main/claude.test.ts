// Unit tests for reading `claude auth status --json`. Run with `npm run test:unit`.
// The fields mirror what the bundled Claude Code prints: `authMethod` is `claude.ai` for a
// subscription login and `api_key` for an API key.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parseAuthStatus, usesSubscription } from './claude.ts'

test('a claude.ai login is a subscription, with its plan and email', () => {
  const s = parseAuthStatus(JSON.stringify({ loggedIn: true, authMethod: 'claude.ai', subscriptionType: 'max', email: 'a@example.org' }))
  assert.deepEqual(s, { installed: true, loggedIn: true, method: 'subscription', plan: 'max', email: 'a@example.org' })
  assert.equal(usesSubscription(s), true)
})

test('an API key login is not a subscription', () => {
  const s = parseAuthStatus(JSON.stringify({ loggedIn: true, authMethod: 'api_key', apiKeySource: 'ANTHROPIC_API_KEY' }))
  assert.equal(s.method, 'api_key')
  assert.equal(usesSubscription(s), false)
})

test('a token sign-in is not the subscription: a Console account reports one too (#872)', () => {
  assert.equal(usesSubscription(parseAuthStatus(JSON.stringify({ loggedIn: true, authMethod: 'oauth_token' }))), false)
})

test('anything else signed in is "other"; not signed in or unreadable output is not signed in', () => {
  assert.equal(parseAuthStatus(JSON.stringify({ loggedIn: true, authMethod: 'bedrock' })).method, 'other')
  assert.equal(parseAuthStatus(JSON.stringify({ loggedIn: false, authMethod: 'none' })).loggedIn, false)
  assert.equal(parseAuthStatus('').loggedIn, false)
  assert.equal(parseAuthStatus('not json').method, null)
})

// Sign-in with a stand-in for `claude auth login`: no program runs and no browser opens.
import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'
import { ClaudeSignIn, TIMED_OUT_MESSAGE, type ClaudeStatus } from './claude.ts'

class FakeLogin extends EventEmitter {
  stdout = new PassThrough()
  stderr = new PassThrough()
  exitCode: number | null = null
  killed = false
  kill(): boolean {
    this.killed = true
    this.exitCode = 143
    return true
  }
}

const notSignedIn: ClaudeStatus = { installed: true, loggedIn: false, method: null, plan: null, email: null }

function harness() {
  const logins: FakeLogin[] = []
  const urls: (string | null)[] = []
  const spawn = (() => {
    const c = new FakeLogin()
    logins.push(c)
    return c
  }) as unknown as ConstructorParameters<typeof ClaudeSignIn>[4]
  class Quiet extends ClaudeSignIn {
    status(): ClaudeStatus {
      return notSignedIn
    }
  }
  const s = new Quiet(() => '/fake/claude', (u) => urls.push(u), () => ({ PATH: '/bin' }), false, spawn)
  return { s, logins, urls }
}

test('a second Sign in joins the waiting sign-in instead of restarting it (#840)', async () => {
  const { s, logins, urls } = harness()
  const first = s.signIn()
  logins[0].stdout.write("If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?x=1\n")
  await new Promise((r) => setImmediate(r))
  assert.equal(s.inProgress(), true)
  const second = s.signIn()
  assert.equal(second, first)
  assert.equal(logins.length, 1, 'no second login started')
  assert.equal(logins[0].killed, false, 'the first login keeps its localhost listener')
  assert.equal(urls.at(-1), 'https://claude.com/cai/oauth/authorize?x=1', 'the link is shown again')
  s.cancel()
  assert.deepEqual(await first, { ok: false, message: 'Sign-in was cancelled.' })
  assert.equal(logins[0].killed, true)
  assert.equal(s.inProgress(), false)
})

test('after Cancel, Sign in starts a fresh login', async () => {
  const { s, logins } = harness()
  const first = s.signIn()
  s.cancel()
  await first
  const next = s.signIn()
  assert.equal(logins.length, 2)
  s.cancel()
  await next
})

test('a login that exits without signing in ends the sign-in', async () => {
  const { s, logins } = harness()
  const p = s.signIn()
  logins[0].exitCode = 1
  logins[0].emit('close', 1)
  const r = await p
  assert.equal(r.ok, false)
  assert.equal(s.inProgress(), false)
})

test('the time-out message says what to do if the browser finished later', () => {
  assert.match(TIMED_OUT_MESSAGE, /10 minutes/)
  assert.match(TIMED_OUT_MESSAGE, /Sign in again/)
})
