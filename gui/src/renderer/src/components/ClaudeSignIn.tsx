// Signing in to Claude with a subscription: the button, the wait for the browser, and the result.
// Used by first-run setup and by Settings → Models & keys.

import { CheckCircle2, KeyRound } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, Callout, Spinner } from '@renderer/components/ui'
import { useEvent } from '@renderer/lib/rpc'
import './claudesignin.css'

type ClaudeLogin = Awaited<ReturnType<typeof window.watchdog.claude.status>>
/** A login that runs on a subscription. An API key signed in to Claude Code is not one. */
// Only a claude.ai sign-in is the subscription; a token can be a Console account's (D407).
export const onSubscription = (s: ClaudeLogin) => s.loggedIn && s.method === 'subscription'
const planName = (s: ClaudeLogin) => (s.plan ? `${s.plan.charAt(0).toUpperCase()}${s.plan.slice(1)} plan` : null)

export function ClaudeSignIn({ onSignedIn, label = 'Sign in with Claude' }: { onSignedIn: () => void; label?: string }) {
  const [phase, setPhase] = useState<'idle' | 'waiting' | 'done'>('idle')
  const [url, setUrl] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [login, setLogin] = useState<ClaudeLogin | null>(null)
  useEvent('claude.signin', (d) => setUrl(d.url))
  useEffect(() => {
    void window.watchdog.claude.status().then((s) => {
      setLogin(s)
      if (onSubscription(s)) {
        setPhase('done')
        onSignedIn()
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const go = async () => {
    setError('')
    setPhase('waiting')
    const r = await window.watchdog.claude.signIn()
    if (r.ok) {
      setPhase('done')
      onSignedIn()
    } else {
      setPhase('idle')
      setError(r.message ?? 'Sign-in did not complete.')
    }
  }
  if (phase === 'done') {
    const detail = [login && planName(login), login?.email].filter(Boolean).join(', ')
    return <div className="signin-ok"><CheckCircle2 /> Signed in to Claude{detail ? ` (${detail})` : ''}</div>
  }
  return (
    <div className="col gap-12">
      {login?.loggedIn && !onSubscription(login) && (
        <div className="signin-note">
          Claude on this computer is signed in with {login.method === 'api_key' ? 'an API key' : login.method === 'token' ? 'an Anthropic Console account or a sign-in token' : 'another service'}, not a Claude subscription, so its calls would be billed there. Sign in with your Claude account to use a subscription.
        </div>
      )}
      <div className="row">
        {phase === 'waiting' ? (
          <>
            <Spinner />
            <span className="muted">Waiting for you to finish signing in in your browser…</span>
            <Button size="sm" variant="ghost" onClick={() => void window.watchdog.claude.cancel()}>Cancel</Button>
          </>
        ) : (
          <Button icon={KeyRound} onClick={() => void go()}>{label}</Button>
        )}
      </div>
      {phase === 'waiting' && url && (
        <div className="signin-note">
          If the browser did not open, <a href={url} onClick={(e) => { e.preventDefault(); void window.watchdog.shell.openExternal(url) }}>open the sign-in page</a>.
          {' '}If your browser ends on a page that will not load, come back here: sign-in may still have worked, and this screen will say so.
        </div>
      )}
      {error && <Callout tone="danger">{error}</Callout>}
    </div>
  )
}
