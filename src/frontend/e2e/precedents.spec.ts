import AxeBuilder from '@axe-core/playwright';
import {expect, test} from '@playwright/test';

test('Find precedents ranks judgments for a case description, accessibly', async ({page}) => {
  await page.goto('/');
  await page.getByRole('link', {name: 'Find precedents', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Find precedents', exact: true})).toBeVisible();
  await page.getByLabel('Case description').fill('Sturz bei der Arbeit, Kausalzusammenhang nach Art. 6 UVG');
  await page.getByRole('button', {name: 'Find precedents'}).click();
  await expect(page.getByRole('status').getByRole('heading', {level: 2})).toBeVisible();
  const {violations} = await new AxeBuilder({page})
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(violations.map(({id, nodes}) => ({id, targets: nodes.map(node => node.target)}))).toEqual([]);
});
