import { ArrowLeft, ArrowRight, FilePlus2, Moon, Search, Sun } from 'lucide-react'
import { Button, Kbd } from '@renderer/components/ui'
import { useApp } from '@renderer/lib/store'
import { routeTitle } from './titles'

export function Topbar() {
  const { route, back, forward, goBack, goForward, project, setPalette, openAdd, theme, setTheme } = useApp()
  const isMac = window.watchdog.platform === 'darwin'
  const dark = document.documentElement.dataset.theme === 'dark'
  return (
    <header className="topbar">
      <Button variant="ghost" size="sm" icon={ArrowLeft} tip="Back" disabled={!back.length} onClick={goBack} />
      <Button variant="ghost" size="sm" icon={ArrowRight} tip="Forward" disabled={!forward.length} onClick={goForward} />
      <div className="crumbs" style={{ marginLeft: 6 }}>
        {project && route.view !== 'projects' && (
          <>
            <span className="dim truncate" style={{ maxWidth: 220 }}>{project.name}</span>
            <span className="sep">/</span>
          </>
        )}
        <span className="truncate">{routeTitle(route)}</span>
      </div>
      <div className="spacer" style={{ WebkitAppRegion: 'drag', alignSelf: 'stretch' } as React.CSSProperties} />
      <button className="search-trigger" onClick={() => setPalette(true)}>
        <Search />
        <span>{project ? 'Search or jump to…' : 'Jump to…'}</span>
        <Kbd>{isMac ? '⌘K' : 'Ctrl K'}</Kbd>
      </button>
      <Button
        variant="ghost"
        size="sm"
        icon={dark ? Sun : Moon}
        tip={dark ? 'Light appearance' : 'Dark appearance'}
        onClick={() => setTheme(dark ? 'light' : theme === 'light' ? 'dark' : 'dark')}
      />
      {project && (
        <Button variant="primary" icon={FilePlus2} onClick={() => openAdd()}>
          Add documents
        </Button>
      )}
    </header>
  )
}
