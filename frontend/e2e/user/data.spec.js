const { test } = require("@playwright/test");
const {
  sleep,
  loginAndCreateProject,
  screenshotWithPrefix,
} = require("./utils");
test("test-data", async ({ page }) => {
  await loginAndCreateProject(page, "data-page");
  await screenshotWithPrefix(page, "empty-project", "empty-project");

  await page.click('a[data-nav-link-name="data"]');
  await page.mouse.move(320, 320);
  await sleep(200);

  await screenshotWithPrefix(page, "data-list", "data-list");
  // metadata upload
  await page.click('text=Item Meta Data Upload >> button[role="button"]');
  await sleep(500);
  await screenshotWithPrefix(page, "data-list", "metadata-upload");
  await sleep(500);
  await page.click(".v-file-input__text");
  await sleep(500);
  await page.setInputFiles('input[type="file"]', "e2e/test_data/item_info.csv");
  await sleep(500);

  await screenshotWithPrefix(page, "data-list", "meta-upload-selected");

  await page.click('button[data-upload-button-name="item-meta-data-upload"]');
  await sleep(500);
  await screenshotWithPrefix(page, "data-list", "meta-upload-complete");

  // data upload
  await page.click('button[role="button"]:has-text("Upload")');
  await sleep(1000);
  await screenshotWithPrefix(page, "data-list", "data-upload");
  await page.click(
    'div:right-of(input[data-file-input-name="training-data-upload"])'
  );

  await page.setInputFiles(
    'input[data-file-input-name="training-data-upload"]',
    "e2e/test_data/purchase_log.csv"
  );
  await sleep(1000);
  await screenshotWithPrefix(page, "data-list", "data-selected");

  await page.click('button[data-upload-button-name="training-data-upload"]');
  await sleep(1000);
  await screenshotWithPrefix(page, "data-list", "data-upload-complete");
  await page.close();
  return;
});
