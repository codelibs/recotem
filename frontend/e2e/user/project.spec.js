const { test } = require("@playwright/test");
const { sleep, login, screenshotWithPrefix } = require("./utils");

test("test", async ({ page }) => {
  const projectName = "project example";
  test.setTimeout(120000);
  await login(page);
  await sleep(300);
  await screenshotWithPrefix(page, "project-list", "project-list");

  // Click div[role="tab"]:has-text("Create")
  await page.click('div[role="tab"]:has-text("Create")');

  // Click input[name="project-name"]
  await page.click('input[name="project-name"]');

  await page.fill('input[name="project-name"]', projectName);

  // Press Tab
  await page.press('input[name="project-name"]', "Tab");

  // Fill input[name="user column name"]
  await page.fill('input[name="user column name"]', "user_id");

  // Press Tab
  await page.press('input[name="user column name"]', "Tab");

  // Fill input[name="item column name"]

  await page.fill('input[name="item column name"]', "item_id");
  await sleep(1000);

  await screenshotWithPrefix(page, "project-list", "fill-project-info");
  // Click button:has-text("Create new project")
  await Promise.all([
    page.waitForNavigation(/*{ url: 'http://localhost:8000/#/project/1/' }*/),
    page.click('button:has-text("Create new project")'),
  ]);
  const projectURL = await page.url();
  await page.goto("http://localhost:8000/#/project-list");

  await sleep(1000);
  await screenshotWithPrefix(
    page,
    "project-list",
    "project-list-with-instance"
  );

  await page.goto(projectURL);

  const startTuningButton = await page.$("text=Start upload tuning");

  await screenshotWithPrefix(page, "project", "empty-project-top");
  // Click text=Start upload -> tuning
  await Promise.all([
    page.waitForNavigation(/*{ url: 'http://localhost:8000/#/project/1/first-tuning' }*/),
    startTuningButton.click(),
  ]);
  await sleep(1000);
  const firstTuningURL = await page.url();

  await screenshotWithPrefix(page, "first-tuning", "file-input");

  // Click .v-file-input__text
  await page.click(".v-file-input__text");
  await sleep(1000);

  // Upload purchase_log.csv
  await page.setInputFiles(
    'input[type="file"]',
    "e2e/test_data/purchase_log.csv"
  );

  await sleep(1000);
  await screenshotWithPrefix(page, "first-tuning", "file-selection-done");

  // Click button:has-text("Upload")
  await page.click('button:has-text("Upload")');

  await sleep(1000);
  await screenshotWithPrefix(page, "first-tuning", "split-config-default");

  await page.click(':nth-match(:text("Manually Define"), 1)');
  await sleep(1000);
  await page.fill(
    ':nth-match(input[name="savename"], 1)',
    "split config save name"
  );
  await sleep(500);
  await screenshotWithPrefix(page, "first-tuning", "split-config-manual");

  // Click button:has-text("Start The job")

  // Click button:has-text("Continue")
  await page.click('button[name="to-step-3"]');
  await sleep(500);
  await screenshotWithPrefix(page, "first-tuning", "evaluation-config-default");

  await page.click(':nth-match(:text("Manually Define"), 2)');
  await sleep(500);

  await page.fill(
    ':nth-match(input[name="savename"], 2)',
    "evaluation config save name"
  );

  await screenshotWithPrefix(page, "first-tuning", "evaluation-config-manual");

  // Click button:has-text("Continue")
  await page.click('button[name="to-step-4"]');
  await sleep(500);
  await screenshotWithPrefix(page, "first-tuning", "job-config-default");

  await page.click(':nth-match(:text("Manually Define"), 3)');
  await sleep(500);
  await screenshotWithPrefix(page, "first-tuning", "job-config-manual");

  await page.fill('input[name="n_trials"]', "10");
  // Click button:has-text("Start The job")
  await Promise.all([
    page.waitForNavigation(/*{ url: 'http://localhost:8000/#/project/1/tuning_job/1' }*/),
    page.click('button:has-text("Start The job")'),
  ]);
  await sleep(500);
  await screenshotWithPrefix(page, "first-tuning", "resulting-tuning-job");
  await page.close();
  return;
});
