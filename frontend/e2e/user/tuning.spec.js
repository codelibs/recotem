const { test } = require("@playwright/test");
const {
  sleep,
  loginAndCreateProject,
  screenshotWithPrefix,
} = require("./utils");

test("test-tuning", async ({ page }) => {
  await loginAndCreateProject(page, "tuning-page");

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

  await page.click('a[data-nav-link-name="tuning"]');
  await page.mouse.move(320, 320);
  await sleep(200);
  await screenshotWithPrefix(page, "tuning-job-list", "empty-tuning-job-list");
});
