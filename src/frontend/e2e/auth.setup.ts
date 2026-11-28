import {expect, test as setup} from '@playwright/test';
import {signedIn} from '../playwright.config';

// The fictional reviewer of scripts/ui_fixture.py, entered once on the start page; every spec starts from it.
setup('enter the fixture reviewer', async ({request}) => {
  const response = await request.post('/api/session', {
    data: {name: 'Muster Anna', initials: 'MUA', function: 'Rechtsanwältin'},
  });
  expect(response.ok()).toBeTruthy();
  await request.storageState({path: signedIn});
});
