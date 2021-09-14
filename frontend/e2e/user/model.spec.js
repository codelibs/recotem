const { test } = require("@playwright/test");
const {
  sleep,
  loginAndCreateProject,
  screenshotWithPrefix,
} = require("./utils");

test("test-tuning", async ({ page }) => {
  test.setTimeout(120000);
  await loginAndCreateProject(page, "model-page");
  await page.click('a[data-nav-link-name="data"]');

  // data upload
  await page.click('button[role="button"]:has-text("Upload")');
  await sleep(1000);
  await page.click(
    'div:right-of(input[data-file-input-name="training-data-upload"])'
  );

  await page.setInputFiles(
    'input[data-file-input-name="training-data-upload"]',
    "e2e/test_data/purchase_log.csv"
  );
  await page.click('button[data-upload-button-name="training-data-upload"]');

  await page.click('text=Item Meta Data Upload >> button[role="button"]');
  await page.click(
    'div:right-of(input[data-file-input-name="item-meta-data-upload"])'
  );
  await page.setInputFiles(
    'input[data-file-input-name="item-meta-data-upload"]',
    "e2e/test_data/item_info.csv"
  );
  await page.click('button[data-upload-button-name="item-meta-data-upload"]');

  await page.click('a[data-nav-link-name="tuning"]');
  await page.mouse.move(320, 320);
  await page.click('a[data-v-btn-link-to="start-tuning"]');

  await page.click("div.v-simple-checkbox");
  await page.click('button[data-next-step="2"]');
  await page.click('button[data-next-step="3"]');
  await page.click('button[data-next-step="4"]');
  await page.click(':nth-match(:text("Manually Define"), 3)');
  await page.fill('input[name="n_trials"]', "3");
  await Promise.all([
    page.waitForNavigation(/*{ url: 'http://localhost:8000/#/project/1/tuning_job/1' }*/),
    page.click('button:has-text("Start The job")'),
  ]);
  await page.click('a[data-nav-link-name="models"]');
  await page.mouse.move(320, 320);
  await sleep(5000);
  await screenshotWithPrefix(page, "trained-model-list", "trained-model-list");

  await page.click(
    '[data-table-name="trained-model-list"] table tbody tr td:nth-child(2)'
  );
  await sleep(500);
  await screenshotWithPrefix(page, "trained-model-detail", "model-information");
  await page.click("text=Preview results");
  await page.click(':has-text("Sample")');

  await screenshotWithPrefix(page, "trained-model-detail", "model-preview-raw");
  await sleep(500);

  // Click div[role="button"]:has-text("Item meta-data to view")
  await page.click('div[role="button"]:has-text("Item meta-data to view")');

  // Click text=item_info.csv
  await page.click("text=item_info.csv");

  // Click button:has-text("Sample")
  await page.click('button:has-text("Sample")');

  await sleep(5000);
  await screenshotWithPrefix(
    page,
    "trained-model-detail",
    "model-preview-with-metadata"
  );

  await page.close();
});
