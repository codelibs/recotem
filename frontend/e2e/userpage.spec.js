const { test, expect } = require("@playwright/test");
const { writeFile } = require("fs");

const sleep = (msec) => new Promise((resolve) => setTimeout(resolve, msec));

let i = 1;
async function screenshotWithNumber(elm, pageName, name) {
  console.log(`${pageName}.${name}`);
  await elm.screenshot({
    path: `imgs/user/${pageName}.${name}.png`,
    fullPage: true,
  });
}
test("test", async ({ page }) => {
  async function savePageHTML(savePath) {
    const html = await page.innerHTML("body");
    writeFile(savePath, html, (err) => {
      if (err) throw err;
      console.log("The file has been saved!");
    });
  }

  test.setTimeout(120000);
  // Go to http://localhost:8000/#/login?redirect=%2Fproject-list
  await page.goto("http://localhost:8000/#/login?redirect=%2Fproject-list");

  // Click input[name="username"]
  await page.click('input[name="username"]');

  // Fill input[name="username"]
  await page.fill('input[name="username"]', "admin");

  // Press Tab
  await page.press('input[name="username"]', "Tab");

  // Fill input[name="password"]
  await page.fill('input[name="password"]', "very_bad_password");
  await sleep(1000);

  // Click button:has-text("Login")
  await Promise.all([
    page.waitForNavigation(/*{ url: "http://localhost:8000/#/project-list" }*/),
    page.click('button:has-text("Login")'),
  ]);
  await sleep(300);
  await screenshotWithNumber(page, "project-list", "empty-project-list");

  // Click div[role="tab"]:has-text("Create")
  await page.click('div[role="tab"]:has-text("Create")');

  // Click input[name="project-name"]
  await page.click('input[name="project-name"]');

  const projectName = `my ec example`;
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

  await screenshotWithNumber(page, "project-list", "fill-project-info");
  // Click button:has-text("Create new project")
  await Promise.all([
    page.waitForNavigation(/*{ url: 'http://localhost:8000/#/project/1/' }*/),
    page.click('button:has-text("Create new project")'),
  ]);
  const projectURL = await page.url();
  await page.goto("http://localhost:8000/#/project-list");

  await sleep(1000);
  await screenshotWithNumber(
    page,
    "project-list",
    "project-list-with-instance"
  );

  await page.goto(projectURL);

  const startTuningButton = await page.$("text=Start upload tuning");
  expect(startTuningButton).not.toBe(undefined);

  await screenshotWithNumber(page, "project", "empty-project-top");
  // Click text=Start upload -> tuning
  await Promise.all([
    page.waitForNavigation(/*{ url: 'http://localhost:8000/#/project/1/first-tuning' }*/),
    startTuningButton.click(),
  ]);
  await sleep(1000);
  const firstTuningURL = await page.url();

  await screenshotWithNumber(page, "first-tuning", "file-input");

  // Click .v-file-input__text
  await page.click(".v-file-input__text");
  await sleep(1000);

  // Upload purchase_log.csv
  await page.setInputFiles(
    'input[type="file"]',
    "e2e/test_data/purchase_log.csv"
  );

  await sleep(1000);
  await screenshotWithNumber(page, "first-tuning", "file-selection-done");

  // Click button:has-text("Upload")
  await page.click('button:has-text("Upload")');

  await sleep(1000);
  await screenshotWithNumber(page, "first-tuning", "split-config-default");

  await page.click(':nth-match(:text("Manually Define"), 1)');
  await sleep(1000);
  await page.fill(
    ':nth-match(input[name="savename"], 1)',
    "split config save name"
  );
  await sleep(500);
  await screenshotWithNumber(page, "first-tuning", "split-config-manual");

  // Click button:has-text("Start The job")

  // Click button:has-text("Continue")
  await page.click('button[name="to-step-3"]');
  await sleep(500);
  await screenshotWithNumber(page, "first-tuning", "evaluation-config-default");

  await page.click(':nth-match(:text("Manually Define"), 2)');
  await sleep(500);

  await page.fill(
    ':nth-match(input[name="savename"], 2)',
    "evaluation config save name"
  );

  await screenshotWithNumber(page, "first-tuning", "evaluation-config-manual");

  // Click button:has-text("Continue")
  await page.click('button[name="to-step-4"]');
  await sleep(500);
  await screenshotWithNumber(page, "first-tuning", "job-config-default");

  await page.click(':nth-match(:text("Manually Define"), 3)');
  await sleep(500);
  await screenshotWithNumber(page, "first-tuning", "job-config-manual");

  await page.fill('input[name="n_trials"]', "10");
  // Click button:has-text("Start The job")
  await Promise.all([
    page.waitForNavigation(/*{ url: 'http://localhost:8000/#/project/1/tuning_job/1' }*/),
    page.click('button:has-text("Start The job")'),
  ]);
  await sleep(500);
  await screenshotWithNumber(page, "tuning-job-detail", "tuning-job");
  const tuningJobURL = await page.url();
  await page.click("text=Configuration");
  await page.click("text=Logs");
  await sleep(500);
  await screenshotWithNumber(page, "tuning-job-detail", "log-unfinished");

  await page.goto(firstTuningURL);
  // Click .v-file-input__text
  await page.click(".v-file-input__text");
  await sleep(1000);
  // Upload purchase_log.csv
  await page.setInputFiles(
    'input[type="file"]',
    "e2e/test_data/purchase_log.csv"
  );

  await sleep(1000);
  //await screenshotWithNumber(page, "first-tuning", "file-selection-done");

  // Click button:has-text("Upload")
  await page.click('button:has-text("Upload")');

  await sleep(1000);
  await screenshotWithNumber(page, "first-tuning", "split-config-default");

  await page.click(':nth-match(:text("Use Preset Config"), 1)');
  await sleep(500);

  await page.click(
    ":nth-match(.v-data-table, 1) .v-data-table__wrapper table tbody tr td:nth-child(2)"
  );
  await sleep(500);
  await screenshotWithNumber(page, "first-tuning", "split-config-preset");
  // Click button:has-text("Start The job")

  // Click button:has-text("Continue")
  await page.click('button[name="to-step-3"]');
  await page.click(':nth-match(:text("Use Preset Config"), 2)');
  await page.click(
    ":nth-match(.v-data-table, 2) .v-data-table__wrapper table tbody tr td:nth-child(2)"
  );
  await sleep(500);
  await screenshotWithNumber(page, "first-tuning", "evaluation-config-preset");

  await page.goto(tuningJobURL);
  await page.click("text=Configuration");
  await sleep(5000);
  await page.click("text=Logs");
  await sleep(1000);
  await screenshotWithNumber(page, "tuning-job-detail", "log-finished");

  await page.click("text=Results");
  await sleep(1000);
  await screenshotWithNumber(page, "tuning-job-detail", "result");

  await page.click('a:has-text("Tuning")');
  await sleep(500);

  const newJobButton = await page.$("text=Start new job");
  await page.mouse.move(
    (
      await newJobButton.boundingBox()
    ).x,
    (
      await newJobButton.boundingBox()
    ).y
  );

  await sleep(500);
  await screenshotWithNumber(page, "tuning-job-list", "job-list");
  await newJobButton.click();
  await sleep(500);
  await screenshotWithNumber(page, "start-tuning", "start-tuning");

  await page.click('a:has-text("Data")');
  await page.mouse.move(320, 320);
  await sleep(200);

  await screenshotWithNumber(page, "data-list", "data-list");
  // metadata upload
  await page.click('text=Item Meta Data Upload >> button[role="button"]');
  await sleep(500);
  await screenshotWithNumber(page, "data-list", "metadata-upload");
  await sleep(500);
  await page.click(".v-file-input__text");
  await sleep(500);
  await page.setInputFiles('input[type="file"]', "e2e/test_data/item_info.csv");
  await sleep(500);

  await screenshotWithNumber(page, "data-list", "meta-upload-selected");

  await page.click('button[data-upload-button-name="item-meta-data-upload"]');
  await sleep(500);
  await screenshotWithNumber(page, "data-list", "meta-upload-complete");

  // data upload
  await page.click('button[role="button"]:has-text("Upload")');
  await sleep(1000);
  await screenshotWithNumber(page, "data-list", "data-upload");
  await page.click(
    'div:right-of(input[data-file-input-name="training-data-upload"])'
  );

  await page.setInputFiles(
    'input[data-file-input-name="training-data-upload"]',
    "e2e/test_data/purchase_log.csv"
  );
  await sleep(1000);
  await screenshotWithNumber(page, "data-list", "data-selected");

  await page.click('button[data-upload-button-name="training-data-upload"]');
  await sleep(1000);
  await screenshotWithNumber(page, "data-list", "data-upload-complete");
  await page.close();
  return;
});
