import {defineConfig, devices} from '@playwright/test';

// Browser tests of the real review app on fixed, fictional data (scripts/ui_fixture.py). Build the frontend first.
const port = 5180;

export default defineConfig({
  testDir: './e2e',
  workers: 1,  // one fixture server
  reporter: [['list']],
  expect: {toHaveScreenshot: {maxDiffPixelRatio: 0.01, animations: 'disabled'}},
  use: {
    ...devices['Desktop Chrome'],
    baseURL: `http://127.0.0.1:${port}`,
    viewport: {width: 1440, height: 900},
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `../../.venv/bin/python ../../scripts/ui_fixture.py --port ${port}`,
    url: `http://127.0.0.1:${port}/api/dashboard`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
