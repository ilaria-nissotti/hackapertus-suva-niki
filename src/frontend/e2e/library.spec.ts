import AxeBuilder from '@axe-core/playwright';
import {expect, test} from '@playwright/test';

test('Library is keyboard accessible and shows search facets', async ({page, request}) => {
  const api = await request.get('/api/library');
  test.skip(api.status() === 404 && !api.headers()['content-type']?.includes('json'),
    'Approved-summary library endpoint is pending');
  expect(api.ok()).toBeTruthy();

  await page.goto('/');
  const library = page.getByRole('link', {name: 'Library', exact: true});
  await library.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', {name: 'Library', exact: true})).toBeVisible();
  await expect(page.getByRole('searchbox', {name: /search/i})).toBeVisible();
  for (const facet of ['Statute', 'Topic', 'Date', 'Outcome', 'Language']) {
    await expect(page.getByLabel(facet, {exact: true})).toBeVisible();
  }
  const {violations} = await new AxeBuilder({page})
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(violations.map(({id, nodes}) => ({id, targets: nodes.map(node => node.target)}))).toEqual([]);
});
