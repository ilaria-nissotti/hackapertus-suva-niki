import {defineConfig, devices} from '@playwright/test';

// Browser tests of the real review app on fixed, fictional data (scripts/ui_fixture.py). Build the frontend first.
const port = 5180;
// auth.setup.ts enters the fixture's fictional reviewer once; every spec starts from that session.
export const signedIn = 'playwright/.auth/reviewer.json';

export default defineConfig({
  testDir: './e2e',
  workers: 1,  // one fixture server
  reporter: [['list']],
  expect: {toHaveScreenshot: {maxDiffPixelRatio: 0.01, animations: 'disabled'}},
  // Baseline names without the project name, as before the setup project existed.
  snapshotPathTemplate: '{testDir}/{testFilePath}-snapshots/{arg}{-snapshotSuffix}{ext}',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: `http://127.0.0.1:${port}`,
    viewport: {width: 1440, height: 900},
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
  },
  projects: [
    {name: 'setup', testMatch: /auth\.setup\.ts/},
    {name: 'review', dependencies: ['setup'], use: {storageState: signedIn}},
  ],
  webServer: {
    command: `../../.venv/bin/python ../../scripts/ui_fixture.py --port ${port}`,
    url: `http://127.0.0.1:${port}/api/session`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
