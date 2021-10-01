const { test } = require("@playwright/test");
const {
  sleep,
  loginAndCreateProject,
  screenshotWithPrefix,
} = require("./utils");

test("test-tuning", async ({ page }) => {
  test.setTimeout(200000);
  await loginAndCreateProject(page, "tuning-page");
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

  await page.click('a[data-nav-link-name="tuning"]');
  await page.mouse.move(320, 320);
  await sleep(200);
  await screenshotWithPrefix(page, "tuning-job-list", "empty-tuning-job-list");

  await page.click('a[data-v-btn-link-to="start-tuning"]');
  await sleep(1000);

  const tuningJobSetupURL = await page.url();
  await page.click("div.v-simple-checkbox");
  await screenshotWithPrefix(page, "start-tuning", "select-data");

  // Step 2
  const splitConfigSaveName = "Split config save name";
  await page.click('button[data-next-step="2"]');
  await sleep(1000);
  await screenshotWithPrefix(page, "start-tuning", "split-config-default");
  await page.click(':nth-match(:text("Manually Define"), 1)');
  await page.fill(':nth-match(input[name="savename"], 1)', splitConfigSaveName);
  await sleep(500);
  await screenshotWithPrefix(
    page,
    "start-tuning",
    "split-config-manually-define"
  );

  // Step 3
  await page.click('button[data-next-step="3"]');
  await sleep(1000);
  await screenshotWithPrefix(page, "start-tuning", "evaluation-config-default");
  await page.click(':nth-match(:text("Manually Define"), 2)');

  const evaluationConfigSaveName = "Evaluation config save name";
  await page.fill(
    ':nth-match(input[name="savename"], 2)',
    evaluationConfigSaveName
  );
  await sleep(500);
  await screenshotWithPrefix(
    page,
    "start-tuning",
    "evaluation-config-manually-define"
  );

  // Step 4
  await page.click('button[data-next-step="4"]');
  await sleep(1000);
  await screenshotWithPrefix(page, "start-tuning", "training-job-default");

  await page.click(':nth-match(:text("Manually Define"), 3)');
  await page.fill('input[name="n_trials"]', "10");
  await sleep(1000);
  await screenshotWithPrefix(
    page,
    "start-tuning",
    "training-job-manually-define"
  );

  await Promise.all([
    page.waitForNavigation(/*{ url: 'http://localhost:8000/#/project/1/tuning_job/1' }*/),
    page.click('button:has-text("Start The job")'),
  ]);
  await sleep(500);
  await screenshotWithPrefix(page, "tuning-job-detail", "tuning-job");

  const tuningJobDetailURL = await page.url();
  await page.goto(tuningJobSetupURL);

  await sleep(1000);

  await page.click("div.v-simple-checkbox");
  await page.click('button[data-next-step="2"]');

  await page.click(':nth-match(:text("Use Preset Config"), 1)');
  await sleep(500);

  await page.click(
    '[data-table-name="split-config-preset-name"] table tbody tr td:nth-child(2)'
  );
  await sleep(500);
  await screenshotWithPrefix(page, "start-tuning", "split-config-preset");
  // Click button:has-text("Start The job")

  // Click button:has-text("Continue")
  await page.click('button[name="to-step-3"]');
  await page.click(':nth-match(:text("Use Preset Config"), 2)');
  await page.click(
    '[data-table-name="evaluation-config-preset-name"] table tbody tr td:nth-child(2)'
  );
  await sleep(500);
  await screenshotWithPrefix(page, "start-tuning", "evaluation-config-preset");

  await page.goto(tuningJobDetailURL);

  await page.click("text=Configuration");
  await page.click("text=Logs");
  await sleep(500);
  await screenshotWithPrefix(page, "tuning-job-detail", "log-unfinished");

  await sleep(10000);
  await page.click("text=Logs");
  await sleep(1000);
  await screenshotWithPrefix(page, "tuning-job-detail", "log-finished");

  await page.click("text=Results");
  await sleep(1000);
  await screenshotWithPrefix(page, "tuning-job-detail", "result");

  await sleep(1000);
  await page.close();
});
