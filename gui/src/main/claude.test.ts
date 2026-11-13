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
