/* Optional: npm install --no-save playwright && npx playwright install chromium
 * Start a rehearsal server, then: node tests/browser-smoke.cjs
 * JARVIS_URL can select another loopback rehearsal instance. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(process.env.JARVIS_URL || 'http://127.0.0.1:8765');
    await page.locator('#mode-badge').filter({ hasText: 'Rehearsal mode' }).waitFor();
    await page.locator('#command-input').fill('routine focus');
    await page.locator('#plan-button').click();
    await page.locator('#approve-plan').waitFor();
    assert.match(await page.locator('#plan-preview').innerText(), /Open Safari/);
    await page.locator('#approve-plan').click();
    await page.locator('#execution-result').waitFor({ state: 'visible' });
    assert.match(await page.locator('#execution-result').innerText(), /rehearsal/i);
    await page.locator('a[data-view="activity"]').click();
    await page.locator('#activity-list').waitFor({ state: 'visible' });
    await page.locator('a[data-view="setup"]').click();
    await page.locator('#setup-details').waitFor({ state: 'visible' });
    await page.locator('a[data-view="command"]').click();
    await page.locator('#command-input').fill('message unconfigured-test-contact: hello');
    await page.locator('#plan-button').click();
    await page.locator('#command-error').waitFor({ state: 'visible' });
    assert.match(await page.locator('#command-error').innerText(), /Unknown contact/);
    await page.locator('#command-input').fill('open Safari');
    await page.locator('#plan-button').click();
    await page.locator('#approve-plan').waitFor({ state: 'visible' });
    await page.locator('#command-input').fill('open Notes');
    await page.locator('#plan-preview').waitFor({ state: 'hidden' });
    for (const width of [375, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 1050 });
      const fits = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
      assert.ok(fits, `Horizontal overflow at ${width}px`);
    }
    assert.deepEqual(errors, []);
    console.log('Browser smoke passed: preview, rehearsal, receipt, navigation, errors, edit invalidation, responsive layout.');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
