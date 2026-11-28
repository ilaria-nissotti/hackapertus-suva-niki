import AxeBuilder from '@axe-core/playwright';
import {expect, test} from '@playwright/test';
import type {Page} from '@playwright/test';

// Fictional cases from scripts/ui_fixture.py:
// 8C_901 has an approved German summary, 8C_902 a German draft waiting for approval, 9C_903 and 2C_904 no summary.
const APPROVED = '8C_901/2026@2026-10-01';
const DRAFTED = '8C_902/2026@2026-09-15';
const NO_SUMMARY = '9C_903/2026@2026-10-20';
const caseUrl = (key: string, lang?: string) => '/?' + new URLSearchParams({case: key, ...(lang ? {lang} : {})});

async function openList(page: Page, url = '/') {
  await page.goto(url);
  await expect(page.getByRole('heading', {name: /Review queue/})).toBeVisible();
}

async function openCase(page: Page, key: string, lang?: string) {
  await page.goto(caseUrl(key, lang));
  await expect(page.locator('#case-title')).toBeVisible();
}

const toolbar = (page: Page) => page.locator('.case-toolbar');

async function expectAccessible(page: Page) {
  const {violations} = await new AxeBuilder({page}).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  // Report rule ids and the offending elements, not the whole axe result.
  expect(violations.map(v => `${v.id} (${v.impact}): ${v.nodes.map(n => n.target.join(' ')).join(', ')}`)).toEqual([]);
}

test.describe('accessibility (axe, WCAG 2.1 A/AA)', () => {
  test('list screen', async ({page}) => {
    await openList(page);
    await expectAccessible(page);
  });

  test('case with an approved summary', async ({page}) => {
    await openCase(page, APPROVED);
    await expectAccessible(page);
  });

  test('case with a draft, questions and full judgment open', async ({page}) => {
    await openCase(page, DRAFTED);
    await page.getByText('Ask questions about this judgment').click();
    await page.getByText('Read the full judgment').click();
    await expect(page.locator('#chat-mic')).toBeVisible();
    await expectAccessible(page);
  });

  test('add judgment dialog', async ({page}) => {
    await openList(page);
    await page.getByRole('button', {name: 'Add judgment'}).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expectAccessible(page);
  });

  test('out-of-date scores banner', async ({page}) => {
    await page.route('**/api/dashboard**', async route => {
      const data = await (await route.fetch()).json();
      await route.fulfill({json: {...data, stale: data.count}});
    });
    await openList(page);
    await expect(page.getByText(/scores are out of date/)).toBeVisible();
    await expectAccessible(page);
  });
});

test.describe('list screen', () => {
  test('shows plain-language statuses and no score column', async ({page}) => {
    await openList(page);
    await expect(page.getByRole('columnheader')).toHaveText(['Judgment', 'Published', 'Status']);
    for (const label of ['Read first', 'Worth reviewing', 'Low relevance']) {
      await expect(page.locator('tbody').getByText(label, {exact: true}).first()).toBeVisible();
    }
  });

  test('back to list keeps the filter', async ({page}) => {
    await openList(page, '/?status=Review');
    await page.getByRole('link', {name: '9C_903/2026'}).click();
    await expect(page.locator('#case-title')).toBeVisible();
    await page.getByRole('link', {name: 'Back to list'}).click();
    await expect(page).toHaveURL(/status=Review/);
    await expect(page.getByRole('link', {name: '9C_903/2026'})).toBeVisible();
  });
});

test.describe('case workspace', () => {
  test('shows why it is queued and what the court decided, without the signatures', async ({page}) => {
    await openCase(page, NO_SUMMARY);
    await expect(page.getByRole('region', {name: 'Why it’s in the queue'})).toContainText('Worth reviewing, score 45 of 100');
    const decision = page.getByRole('region', {name: 'What the court decided'});
    await expect(decision).toContainText('Die Beschwerde wird abgewiesen.');
    await expect(decision).not.toContainText('Luzern');
  });

  test('one main action at a time, in the same toolbar spot', async ({page}) => {
    const spots: number[] = [];
    const expectAction = async (name: string) => {
      const button = toolbar(page).getByRole('button', {name, exact: true});
      await expect(button).toBeVisible();
      await expect(toolbar(page).locator('.primary-action button')).toHaveCount(1);
      const box = (await button.boundingBox())!;
      spots.push(Math.round(box.x + box.width));
    };
    await openCase(page, NO_SUMMARY);
    await expectAction('Generate summaries');
    await openCase(page, DRAFTED);
    await expectAction('Approve summary');
    await expect(toolbar(page).getByRole('button', {name: 'Approve summary'})).toHaveAttribute('form', 'summary-de');
    await openCase(page, APPROVED, 'fr');
    await expectAction('Generate summaries');
    expect(new Set(spots).size).toBe(1);  // right-aligned in the same place every time
  });

  test('after approval, the audio briefing is next (unavailable without ElevenLabs)', async ({page}) => {
    await openCase(page, APPROVED);
    await expect(toolbar(page).locator('.primary-action button')).toHaveCount(0);
    await expect(toolbar(page)).toContainText('Audio briefings need ElevenLabs for Deutsch.');
  });

  test('the language tabs switch the summary', async ({page}) => {
    await openCase(page, APPROVED);
    await expect(page.getByRole('heading', {name: 'Summary in Deutsch'})).toBeVisible();
    await toolbar(page).getByRole('button', {name: 'Français'}).click();
    await expect(page).toHaveURL(/lang=fr/);
    await expect(page.getByRole('heading', {name: 'Summary in Français'})).toBeVisible();
  });
});

test.describe('keyboard', () => {
  test('a case opens from the keyboard', async ({page}) => {
    await openList(page);
    await page.getByRole('link', {name: '8C_901/2026'}).focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/case=8C_901/);
    await expect(page.locator('#case-title')).toBeVisible();
  });

  test('tab order reaches the search, the filters and the first case', async ({page}) => {
    await openList(page);
    const reached: string[] = [];
    for (let i = 0; i < 12; i++) {
      await page.keyboard.press('Tab');
      reached.push(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')
        ?? document.activeElement?.textContent?.trim() ?? ''));
    }
    for (const name of ['Search cases', 'Review status', 'Sort by', '8C_901/2026']) expect(reached).toContain(name);
  });

  test('Escape closes the add dialog and focus returns to its button', async ({page}) => {
    await openList(page);
    const button = page.getByRole('button', {name: 'Add judgment'});
    await button.click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toBeHidden();
    await expect(button).toBeFocused();
  });
});

test.describe('screenshots at 1440×900', () => {
  test('list', async ({page}) => {
    await openList(page);
    await expect(page).toHaveScreenshot('list.png');
  });

  test('case with a draft to approve', async ({page}) => {
    await openCase(page, DRAFTED);
    await expect(page).toHaveScreenshot('case-draft.png');
  });

  test('case with an approved summary', async ({page}) => {
    await openCase(page, APPROVED);
    await expect(page).toHaveScreenshot('case-approved.png');
  });

  test('no filter match', async ({page}) => {
    await openList(page, '/?q=nothing-matches-this');
    await expect(page.getByText('No judgments match these filters.')).toBeVisible();
    await expect(page).toHaveScreenshot('no-match.png');
  });
});
