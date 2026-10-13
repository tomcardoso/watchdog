import '@fontsource-variable/inter'
import '@fontsource-variable/source-serif-4'
import '@fontsource-variable/jetbrains-mono'
import './styles/tokens.css'
import './styles/base.css'
import './styles/ui.css'
import './styles/shell.css'
import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { queryClient } from './lib/rpc'
import App from './App'

// The platform class goes on <html>, not only on the app shell, so first-run setup (which renders
// before the shell) also clears the macOS window buttons.
document.documentElement.classList.add(`platform-${window.watchdog.platform}`)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>
)

// End-to-end tests and the screenshot script drive navigation through this handle.
;(window as unknown as { __watchdogApp: unknown }).__watchdogApp = (await import('./lib/store')).useApp
// …and check what the cache holds through this one.
;(window as unknown as { __watchdogQueries: unknown }).__watchdogQueries = queryClient
// …and the guided tour (D303) through this one: its state, and Help → Take the tour.
;(window as unknown as { __watchdogTour: unknown }).__watchdogTour = await import('./tour/Tour').then((m) => ({ useTour: m.useTour, takeTour: m.takeTour, exploreSample: m.exploreSample }))
