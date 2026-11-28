import {expect, test} from '@playwright/test';

// Classic scrollbars that take space, as on macOS with "Show scroll bars: Always" or on Windows.
test.use({launchOptions: {ignoreDefaultArgs: ['--hide-scrollbars']}});

test('switching screens does not shift the layout sideways', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 600});  // the queue scrolls, Find precedents does not
  await page.goto('/');
  await expect(page.locator('.queue-link').first()).toBeVisible();
  // Right edges of the masthead and of the page content: they move when the scrollbar comes and goes.
  const layout = () => page.evaluate(() => ({
    masthead: Math.round(document.querySelector('.challenge')!.getBoundingClientRect().right),
    content: Math.round(document.querySelector('main')!.getBoundingClientRect().right),
  }));
  const queue = await layout();
  for (const name of ['Find precedents', 'Library', 'Review queue']) {
    await page.getByRole('link', {name, exact: true}).click();
    await expect(page.locator('main h1').first()).toBeVisible();
    expect(await layout()).toEqual(queue);
  }
});

test('the library filters and the search button stay on one row', async ({page}) => {
  await page.goto('/?screen=library');
  await expect(page.getByLabel('Topic', {exact: true}).locator('option').nth(1)).toBeAttached();
  const tops = await page.locator('.library-filters > :not(.query)').evaluateAll(elements =>
    elements.map(element => Math.round(element.getBoundingClientRect().bottom)));
  expect(tops).toHaveLength(6);
  expect(new Set(tops).size).toBe(1);
});
