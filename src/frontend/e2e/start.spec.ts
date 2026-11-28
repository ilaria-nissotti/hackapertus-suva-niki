import AxeBuilder from '@axe-core/playwright';
import {expect, test} from '@playwright/test';

test.describe('a new browser', () => {
  test.use({storageState: {cookies: [], origins: []}});

  test('opening the app asks who is reviewing first', async ({page}) => {
    await page.goto('/?screen=library');
    await expect(page).toHaveURL(/\/start$/);
    await expect(page.getByRole('heading', {name: 'Who is reviewing?'})).toBeVisible();
    await expect(page.getByRole('link', {name: 'Library'})).toHaveCount(0);
    const {violations} = await new AxeBuilder({page}).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    expect(violations.map(({id, nodes}) => ({id, targets: nodes.map(node => node.target)}))).toEqual([]);
  });

  test('the lawyer enters a name with the keyboard and the browser remembers it next time', async ({page}) => {
    await page.goto('/start');
    await page.getByLabel('Name', {exact: true}).fill('Frei Thomas');
    await page.keyboard.press('Tab');
    await page.keyboard.type('frt');
    await page.getByLabel('Function (optional)').fill('Rechtsanwalt');
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', {name: 'Federal Court judgments'})).toBeVisible();
    await expect(page.locator('.account')).toContainText('Reviewing as Frei Thomas (FRT)');

    await page.getByRole('button', {name: 'Change'}).click();
    await expect(page).toHaveURL(/\/start$/);
    await expect(page.getByLabel('Name', {exact: true})).toHaveValue('Frei Thomas');
    await expect(page.getByLabel('Initials')).toHaveValue('FRT');
    await expect(page.getByLabel('Function (optional)')).toHaveValue('Rechtsanwalt');
    expect(await (await page.request.get('/api/session')).json()).toEqual({reviewer: null});

    await page.reload();
    await expect(page.getByLabel('Name', {exact: true})).toHaveValue('Frei Thomas');
    await expect(page.getByLabel('Initials')).toHaveValue('FRT');
    await expect(page.getByLabel('Function (optional)')).toHaveValue('Rechtsanwalt');

    await page.getByLabel('Name', {exact: true}).fill('Villa Rebeca');
    await page.getByLabel('Initials').fill('VIL');
    await page.getByLabel('Function (optional)').clear();
    await page.getByRole('button', {name: 'Start reviewing'}).click();
    await expect(page.locator('.account')).toContainText('Reviewing as Villa Rebeca (VIL)');
    await page.reload();
    await expect(page.locator('.account')).toContainText('Reviewing as Villa Rebeca (VIL)');
  });
});

test('an approved summary shows who signed it', async ({page}) => {
  const {rows} = await (await page.request.get('/api/dashboard')).json();
  await page.goto('/?' + new URLSearchParams({case: rows[0].key, view: 'Legal summaries'}));
  await expect(page.locator('.signature')).toHaveText(
    /Approved by Muster Anna \(MUA\), Rechtsanwältin · \d{1,2} [A-Z][a-z]{2} \d{4}, \d{2}:\d{2}/,
  );
});
