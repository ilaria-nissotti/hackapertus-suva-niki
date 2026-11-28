import AxeBuilder from '@axe-core/playwright';
import {expect, test} from '@playwright/test';
import type {Page} from '@playwright/test';

// Fictional cases from scripts/ui_fixture.py. The first one has an approved German summary.
const RELEVANT = '8C_901/2026@2026-10-01';
const SUBJECT = 'Unfallversicherung (Unfallkausalität)';
const caseUrl = (key: string, view?: string) =>
  '/?' + new URLSearchParams({case: key, ...(view ? {view} : {})});

async function open(page: Page, url: string) {
  await page.goto(url);
  await expect(page.getByRole('heading', {name: /Review queue/})).toBeVisible();
}

async function expectAccessible(page: Page) {
  const {violations} = await new AxeBuilder({page}).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  // Report rule ids and the offending elements, not the whole axe result.
  expect(violations.map(v => `${v.id} (${v.impact}): ${v.nodes.map(n => n.target.join(' ')).join(', ')}`)).toEqual([]);
}

test.describe('accessibility (axe, WCAG 2.1 A/AA)', () => {
  test('queue with no case open', async ({page}) => {
    await open(page, '/');
    await expectAccessible(page);
  });

  for (const view of ['Overview', 'Legal summaries', 'Ask this judgment']) {
    test(`case pane: ${view}`, async ({page}) => {
      await open(page, caseUrl(RELEVANT, view));
      await expect(page.getByRole('heading', {name: SUBJECT})).toBeVisible();
      await expectAccessible(page);
    });
  }

  test('add judgment dialog', async ({page}) => {
    await open(page, '/');
    await page.getByRole('button', {name: 'Add judgment'}).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expectAccessible(page);
  });

  test('out-of-date scores banner', async ({page}) => {
    await page.route('**/api/dashboard**', async route => {
      const data = await (await route.fetch()).json();
      await route.fulfill({json: {...data, stale: data.count}});
    });
    await open(page, '/');
    await expect(page.getByText(/scores are out of date/)).toBeVisible();
    await expectAccessible(page);
  });
});

test.describe('keyboard', () => {
  test('a case opens from the keyboard', async ({page}) => {
    await open(page, '/');
    const link = page.getByRole('link', {name: '8C_901/2026'});
    await link.focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/case=8C_901/);
    await expect(page.getByRole('heading', {name: SUBJECT})).toBeVisible();
  });

  test('tab order reaches the search, the filters and the first case', async ({page}) => {
    await open(page, '/');
    const reached: string[] = [];
    for (let i = 0; i < 12; i++) {
      await page.keyboard.press('Tab');
      reached.push(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')
        ?? document.activeElement?.textContent?.trim() ?? ''));
    }
    for (const name of ['Search cases', 'Review status', 'Sort by', '8C_901/2026']) expect(reached).toContain(name);
  });

  test('Escape closes the add dialog and focus returns to its button', async ({page}) => {
    await open(page, '/');
    const button = page.getByRole('button', {name: 'Add judgment'});
    await button.click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toBeHidden();
    await expect(button).toBeFocused();
  });
});

test('overview leads with the decision and keeps score and coverage details on demand', async ({page}) => {
  await open(page, caseUrl(RELEVANT));
  const casePane = page.getByRole('article', {name: SUBJECT});
  await expect(casePane.getByRole('heading', {name: 'Court’s decision'})).toBeVisible();
  await expect(casePane.getByText(/Die Beschwerde wird abgewiesen/).first()).toBeVisible();
  await expect(casePane.getByText('Read full decision')).toHaveCount(0);
  await expect(page.getByRole('columnheader', {name: 'Score', exact: true})).toHaveCount(0);
  await expect(casePane.getByText('Score 88', {exact: true})).toBeHidden();
  await casePane.getByText('Score details', {exact: true}).click();
  await expect(casePane.getByText('Score 88', {exact: true})).toBeVisible();
  await expect(page.getByText(/Publication window:/)).toBeHidden();
  await page.getByText('Coverage', {exact: true}).click();
  await expect(page.getByText(/Publication window:/)).toBeVisible();
  await casePane.getByRole('button', {name: 'Open summary'}).click();
  await expect(casePane.getByRole('textbox', {name: 'Approved summary'})).toBeVisible();
  await expect(casePane.getByRole('button', {name: 'Save approved edit'})).toBeHidden();
  await casePane.getByRole('textbox', {name: 'Approved summary'}).fill('Die Beschwerde wird abgewiesen.');
  await expect(casePane.getByRole('button', {name: 'Save approved edit'})).toBeVisible();
});

test('long decisions show an excerpt until the reviewer expands them', async ({page}) => {
  await page.route('**/api/dashboard**', async route => {
    const data = await (await route.fetch()).json();
    if (data.selected) {
      data.selected.decision = `${data.selected.decision}\n\n${'Weitere Anordnungen. '.repeat(40)}\n\nLetzte Anordnung.`;
    }
    await route.fulfill({json: data});
  });
  await open(page, caseUrl(RELEVANT));
  const casePane = page.getByRole('article', {name: SUBJECT});
  await expect(casePane.getByText('Decision excerpt', {exact: true})).toBeVisible();
  await expect(casePane.getByText('Letzte Anordnung.', {exact: true})).toBeHidden();
  await casePane.getByText('Read full decision', {exact: true}).click();
  await expect(casePane.getByText('Decision excerpt', {exact: true})).toBeHidden();
  await expect(casePane.getByText('Letzte Anordnung.', {exact: true})).toBeVisible();
});

test.describe('screenshots at 1440×900', () => {
  test('queue', async ({page}) => {
    await open(page, '/');
    await expect(page).toHaveScreenshot('queue.png');
  });

  test('case open', async ({page}) => {
    await open(page, caseUrl(RELEVANT));
    await expect(page.getByRole('heading', {name: SUBJECT})).toBeVisible();
    await expect(page).toHaveScreenshot('case-open.png');
  });

  test('approved German summary', async ({page}) => {
    await open(page, caseUrl(RELEVANT, 'Legal summaries'));
    await expect(page.getByText('Approved summary')).toBeVisible();
    await expect(page).toHaveScreenshot('summary-approved.png');
  });

  test('no filter match', async ({page}) => {
    await open(page, '/?q=nothing-matches-this');
    await expect(page.getByText('No judgments match these filters.')).toBeVisible();
    await expect(page).toHaveScreenshot('no-match.png');
  });
});
