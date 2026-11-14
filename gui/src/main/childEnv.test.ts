// The environment the app hands to the programs it starts is an allowlist (D409).

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { APP_MARKER, allowed, childEnv } from './childEnv.ts'

test('no variable that can decide who pays reaches a child', () => {
  const env = childEnv({
    PATH: '/usr/bin', HOME: '/home/r', LANG: 'en_CA.UTF-8', LC_ALL: 'en_CA.UTF-8', HTTPS_PROXY: 'http://proxy:8080',
    ANTHROPIC_API_KEY: 'sk-ant-x', ANTHROPIC_AUTH_TOKEN: 't', ANTHROPIC_BASE_URL: 'https://gateway', ANTHROPIC_PROFILE: 'p',
    CLAUDE_CODE_OAUTH_TOKEN: 'o', CLAUDE_CODE_USE_BEDROCK: '1', CLAUDE_CONFIG_DIR: '/x', OPENAI_API_KEY: 'sk-o',
    DEEPSEEK_API_KEY: 'd', GEMINI_API_KEY: 'g', OPENROUTER_API_KEY: 'r', LOCAL_BASE_URL: 'http://l', AWS_PROFILE: 'a',
    GOOGLE_APPLICATION_CREDENTIALS: '/g.json', GOOGLE_API_KEY: 'k', AZURE_OPENAI_API_KEY: 'z', CLOUDSDK_CORE_PROJECT: 'c',
    OPENAI_BASE_URL: 'https://elsewhere', HF_TOKEN: 'hf_x', WATCHDOG_ENFORCE_ACCESS: '0', LD_LIBRARY_PATH: '/x',
    PYTHONSTARTUP: '/evil.py', NODE_OPTIONS: '--require x', SOMETHING_ELSE: 'y'
  })
  assert.deepEqual(Object.keys(env).sort(), ['HOME', 'HTTPS_PROXY', 'LANG', 'LC_ALL', 'PATH', APP_MARKER].sort())
  assert.equal(env[APP_MARKER], '1')
})

test('Windows names pass whatever their case; look-alike names do not', () => {
  assert.ok(allowed('Path') && allowed('SystemRoot') && allowed('ProgramFiles(x86)') && allowed('https_proxy'))
  assert.ok(allowed('HF_HOME') && allowed('XDG_RUNTIME_DIR') && allowed('DOCLING_ARTIFACTS_PATH'))
  assert.ok(!allowed('HF_TOKEN') && !allowed('WATCHDOG_SRC') && !allowed('LC'))
  assert.ok(!allowed('ANTHROPIC_API_KEY') && !allowed('Anthropic_Base_Url') && !allowed('CLAUDE_CONFIG_DIR'))
})
