import {expect, test} from '@playwright/test';

test('switching screens keeps the queue and the precedent search as the reviewer left them', async ({page}) => {
  const queueRequests: string[] = [];
  page.on('request', request => {
    if (request.url().includes('/api/dashboard')) queueRequests.push(new URL(request.url()).search);
  });
  await page.goto('/?q=Unfall&status=Relevant');
  await expect(page.getByRole('link', {name: '8C_901/2026'})).toBeVisible();
  await page.getByRole('link', {name: '8C_901/2026'}).click();
  await expect(page.getByRole('heading', {level: 2, name: /Unfallkausalität/})).toBeVisible();
  await page.mouse.wheel(0, 600);

  await page.getByRole('link', {name: 'Find precedents', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Find precedents', exact: true})).toBeInViewport();
  await page.getByLabel('Case description').fill('Sturz bei der Arbeit nach Art. 6 UVG');
  await page.getByRole('button', {name: 'Find precedents'}).click();
  await expect(page.getByRole('status').getByRole('heading', {level: 2})).toBeVisible();
  expect(queueRequests.filter(search => search.includes('screen='))).toEqual([]);

  await page.getByRole('link', {name: 'Review queue', exact: true}).click();
  await expect(page).toHaveURL(/q=Unfall&status=Relevant&case=8C_901/);
  await expect(page.getByRole('searchbox', {name: 'Search cases'})).toHaveValue('Unfall');
  await expect(page.getByRole('heading', {level: 2, name: /Unfallkausalität/})).toBeVisible();

  await page.getByRole('link', {name: 'Find precedents', exact: true}).click();
  await expect(page.getByLabel('Case description')).toHaveValue('Sturz bei der Arbeit nach Art. 6 UVG');
  await expect(page.getByRole('status').getByRole('heading', {level: 2})).toBeVisible();
});
