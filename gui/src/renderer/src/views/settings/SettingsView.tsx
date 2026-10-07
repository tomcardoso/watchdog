// Settings: every `watchdog settings` key as a form, plus sign-in and keys, skills, appearance,
// the vault check, setup and about. Works with no investigation open.

import { BookOpen, FolderLock, HeartPulse, Info, KeyRound, Palette, Wrench } from 'lucide-react'
import { LucideIcon } from 'lucide-react'
import { Callout, ErrorNote, Skeleton, cx } from '@renderer/components/ui'
import { useRpc } from '@renderer/lib/rpc'
import { navigate, useApp } from '@renderer/lib/store'
import AuthPanel from './AuthPanel'
import { AboutPanel, AppearancePanel, DoctorPanel, SetupPanel, SkillsPanel } from './MiscPanels'
import { FolderAccessPanel } from './FolderAccessPanel'
import { SettingField } from './SettingField'
import './settings.css'

const slug = (t: string) => t.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
const OTHER: { id: string; label: string; icon: LucideIcon }[] = [
  { id: 'auth', label: 'Models & keys', icon: KeyRound },
  { id: 'access', label: 'Folder access', icon: FolderLock },
  { id: 'skills', label: 'Record skills', icon: BookOpen },
  { id: 'appearance', label: 'Appearance', icon: Palette },
  { id: 'doctor', label: 'Check vaults', icon: HeartPulse },
  { id: 'setup', label: 'Setup', icon: Wrench },
  { id: 'about', label: 'About', icon: Info }
]

function General({ id }: { id: string }) {
  const schema = useRpc('settings.schema', {})
  const models = useRpc('settings.models', {})
  if (schema.isLoading) return <Skeleton h={400} />
  if (schema.error) return <ErrorNote error={schema.error} retry={() => void schema.refetch()} />
  const section = schema.data!.sections.find((s) => slug(s.title) === id)
  if (!section) return null
  return (
    <div>
      <h2 className="set-title">{section.title}</h2>
      {section.blurb && <p className="set-blurb">{section.blurb}</p>}
      <div className="card set-card">
        {section.keys.map((k) => (
          <div key={k.key}>
            {k.key === 'auto_approve' && (
              <Callout tone="warning" title="What auto-approve does" style={{ margin: 16 }}>
                Before documents are sent to a model, Watchdog shows the public-records warning and waits for you. With auto-approve on, that pause is skipped only when you are signed in with your Claude subscription and every step of the run uses it; a one-line notice is shown instead. Any run that uses a paid API key still asks, however small. Turn it on only if you already check that what you add is public record.
              </Callout>
            )}
            <SettingField key={`${k.key}:${k.display}`} setting={k} models={models.data?.models ?? []} efforts={models.data?.efforts ?? ['low', 'medium', 'high', 'xhigh', 'max']} />
          </div>
        ))}
      </div>
    </div>
  )
}

export default function SettingsView() {
  const route = useApp((s) => s.route)
  const schema = useRpc('settings.schema', {})
  const sections = schema.data?.sections ?? []
  const tab = (route.view === 'settings' && route.tab) || (sections[0] ? slug(sections[0].title) : 'auth')
  const go = (t: string) => navigate({ view: 'settings', tab: t }, { replace: true })
  const isOther = OTHER.some((o) => o.id === tab)
  return (
    <div className="page">
      <div className="page-inner">
        <div className="page-header">
          <div className="grow">
            <h1 className="page-title">Settings</h1>
            <div className="page-sub">How Watchdog works on this computer. Changes apply to every investigation.</div>
          </div>
        </div>
        <div className="set-layout">
          <nav className="set-nav" aria-label="Settings sections">
            <div className="eyebrow">General</div>
            {schema.isLoading && Array.from({ length: 6 }, (_, i) => <Skeleton key={i} h={28} style={{ margin: '4px 0' }} />)}
            {sections.map((s) => (
              <button key={s.title} className={cx('set-nav-item', tab === slug(s.title) && 'on')} onClick={() => go(slug(s.title))}>
                {s.title}
              </button>
            ))}
            <div className="eyebrow" style={{ marginTop: 14 }}>App</div>
            {OTHER.map((o) => (
              <button key={o.id} className={cx('set-nav-item', tab === o.id && 'on')} onClick={() => go(o.id)}>
                <o.icon /> {o.label}
              </button>
            ))}
          </nav>
          <main className="set-main">
            {tab === 'auth' && <><h2 className="set-title">Models & keys</h2><p className="set-blurb">How Watchdog signs in to Claude and to each model provider.</p><AuthPanel /></>}
            {tab === 'access' && <><h2 className="set-title">Folder access</h2><p className="set-blurb">The folders Watchdog may read and change.</p><FolderAccessPanel /></>}
            {tab === 'skills' && <><h2 className="set-title">Record skills</h2><SkillsPanel /></>}
            {tab === 'appearance' && <><h2 className="set-title">Appearance</h2><AppearancePanel /></>}
            {tab === 'doctor' && <><h2 className="set-title">Check vaults</h2><DoctorPanel /></>}
            {tab === 'setup' && <><h2 className="set-title">Setup</h2><SetupPanel /></>}
            {tab === 'about' && <><h2 className="set-title">About</h2><AboutPanel /></>}
            {!isOther && <General id={tab} />}
          </main>
        </div>
      </div>
    </div>
  )
}
