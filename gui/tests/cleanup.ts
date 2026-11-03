// Removes a test's throwaway folder. On Windows a closed app's helper processes keep its Chromium
// files locked for a while (EBUSY, EPERM), so a folder still locked after the retries is reported
// and left to the system's temporary-files cleanup rather than failing a test that passed.
// Elsewhere the retries cover a late write, and anything else fails the test.
import { rmSync } from 'node:fs'

export function removeTestFolder(root: string): void {
  try {
    rmSync(root, { recursive: true, force: true, maxRetries: 5 })
  } catch (e) {
    const code = (e as NodeJS.ErrnoException).code
    if (process.platform !== 'win32' || (code !== 'EBUSY' && code !== 'EPERM')) throw e
    console.warn(`Left ${root} behind: ${code}, still locked after the app closed`)
  }
}
