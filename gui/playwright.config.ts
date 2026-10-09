import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  timeout: 180_000,
  // Each test launches its own app against its own throwaway home, so they run side by side.
  fullyParallel: true,
  workers: 3,
  reporter: [['list']]
})
