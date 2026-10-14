import type { WatchdogBridge } from '../shared/api'

declare global {
  interface Window {
    watchdog: WatchdogBridge
  }
}
export {}
