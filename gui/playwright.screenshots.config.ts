import { defineConfig } from '@playwright/test'

// `npm run screenshots`: the sample investigation's screens for the docs and the website.
export default defineConfig({
  testDir: './screenshots',
  globalSetup: './tests/global-setup.ts',
  timeout: 900_000,
  workers: 1,
  reporter: [['list']]
})
