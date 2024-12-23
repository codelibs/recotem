const { test, expect } = require("@playwright/test");

const sleep = (msec) => new Promise((resolve) => setTimeout(resolve, msec));

let i = 1;
async function screenshotWithNumber(elm, name) {
  await elm.screenshot({
    path: `imgs/tutorial/${i++}.${name}.png`,
    fullPage: true,
  });
}
test("test", async ({ page }) => {
  const boundingBoxInfo = [];
  test.setTimeout(120000);
  const projectName = `example`;
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
  await screenshotWithNumber(page, "input-login-info");

  // Click button:has-text("Login")
  await Promise.all([
    page.waitForNavigation(/*{ url: "http://localhost:8000/#/project-list" }*/),
    page.click('button:has-text("Login")'),
  ]);
  await sleep(300);
  await screenshotWithNumber(page, "project-top");

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

  await screenshotWithNumber(page, "fill-project-info");
  // Click button:has-text("Create new project")
  await Promise.all([
    page.waitForNavigation(/*{ url: 'http://localhost:8000/#/project/1/' }*/),
    page.click('button:has-text("Create new project")'),
  ]);
  await sleep(1000);

  await screenshotWithNumber(page, "empty-project-top");
  // Click text=Start upload -> tuningk:w

  await Promise.all([
    page.waitForNavigation(/*{ url: 'http://localhost:8000/#/project/1/first-tuning' }*/),
    page.click("text=Start upload tuning"),
  ]);
  await sleep(2000);
  await screenshotWithNumber(page, "file-input");

  // Click .v-file-input__text
  await page.click(".v-file-input__text");
  await sleep(1000);

  // Upload purchase_log.csv
  await page.setInputFiles(
    'input[type="file"]',
    "e2e/test_data/purchase_log.csv"
  );

  await sleep(1000);
  await screenshotWithNumber(page, "file-selection-done");

  // Click button:has-text("Upload")
  await page.click('button:has-text("Upload")');

  await sleep(1000);
  await screenshotWithNumber(page, "split-config");
  // Click button:has-text("Continue")
  await page.click('button[name="to-step-3"]');
  await sleep(1000);
  await screenshotWithNumber(page, "evaluation-config");
  // Click button:has-text("Continue")
  await page.click('button[name="to-step-4"]');
  await sleep(1000);
  await screenshotWithNumber(page, "job-config");

  //await page.click(':nth-match(:text("Manually Define"), 3)');
  //await page.fill('input[name="n_trials"]', "5");
  // Click button:has-text("Start The job")
  await Promise.all([
    page.waitForNavigation(/*{ url: 'http://localhost:8000/#/project/1/tuning_job/1' }*/),
    page.click('button:has-text("Start The job")'),
  ]);
  await sleep(1000);
  await screenshotWithNumber(page, "tuning-job");

  // Click text=ConfigurationData purchase_log.csv Trials40Train after tuningtrueOverall timeout >> :nth-match(div, 4)
  // Click text=Logs
  await page.click("text=Configuration");
  await page.click("text=Logs");
  await sleep(3000);
  await screenshotWithNumber(page, "tuning-logs");
  await sleep(20000);

  // Click text=Results
  await page.click("text=Logs");
  await page.click("text=Results");

  await sleep(2000);
  await screenshotWithNumber(page, "tuning-results");

  // Click text=model-1 >> i
  await Promise.all([
    page.waitForNavigation(/*{ url: 'http://localhost:8000/#/project/1/trained_model/1' }*/),
    page.click("text=model- >> i"),
  ]);
  await sleep(1000);
  await screenshotWithNumber(page, "model-results");
  // Click text=Preview results
  await page.click("text=Preview results");

  await page.click('button:has-text("Sample")');
  await sleep(2000);
  await screenshotWithNumber(page, "model-results-preview");

  // Click a:has-text("Data")
  await page.click('a:has-text("Data")');
  await page.click("html");

  // Click text=Item Meta Data Upload >> button[role="button"]

  await sleep(1000);
  await screenshotWithNumber(page, "item-metadata-upload");

  await page.click('text=Item Meta Data Upload >> button[role="button"]');

  await sleep(500);
  await screenshotWithNumber(page, "item-metadata-file-input");
  // Click form >> :nth-match(div:has-text("An item meta-data file."), 3)
  await page.click(
    'div:right-of(input[data-file-input-name="item-meta-data-upload"])'
  );
  // Upload item_info.csv
  await sleep(500);
  await page.setInputFiles('input[type="file"]', "e2e/test_data/item_info.csv");

  // Click div[role="document"] button:has-text("Upload")
  await page.click('div[role="document"] button:has-text("Upload")');

  // Click a:has-text("Models")
  await Promise.all([
    page.waitForNavigation(/*{ url: 'http://localhost:8000/#/project/1/trained_model/' }*/),
    page.click('a:has-text("Models")'),
  ]);
  await page.click("html");

  await sleep(500);
  await screenshotWithNumber(page, "model-selection");

  // Click text=model-1.pkl
  await page.click("text=model-");

  // Click text=Preview results
  await page.click("text=Preview results");

  await sleep(500);
  await screenshotWithNumber(page, "item-metadata-selection");

  // Click div[role="button"]:has-text("Item meta-data to view")
  await page.click('div[role="button"]:has-text("Item meta-data to view")');

  // Click text=item_info.csv
  await page.click("text=item_info.csv");

  // Click button:has-text("Sample")
  await page.click('button:has-text("Sample")');

  // Click .v-input.v-input--is-label-active.v-input--is-dirty.theme--light.v-text-field.v-text-field--is-booted.v-select.v-select--chips .v-input__control
  await page.click(
    ".v-input.v-input--is-label-active.v-input--is-dirty.theme--light.v-text-field.v-text-field--is-booted.v-select.v-select--chips .v-input__control"
  );

  // Click button:has-text("Sample")
  await page.click('button:has-text("Sample")');
  await sleep(500);
  await screenshotWithNumber(page, "sample-with-metadata");

  // Clean up using admin page
  await page.goto("http://localhost:8000/api/admin");

  await page.click("text=Projects");

  // Click text=Project Project object (1) >> td
  await page.click("text=Project Project object >> td");

  // Select delete_selected
  await page.selectOption('select[name="action"]', "delete_selected");

  // Click button:has-text("Go")
  await page.click('button:has-text("Go")');

  // Click text=Are you sure? Are you sure you want to delete the selected project? All of the f
  await page.click("text=Are you sure?");

  // Click text=Yes, I’m sure
  await page.click("text=Yes, I’m sure");

  // Click text=View site
  await Promise.all([
    page.waitForNavigation(/*{ url: 'http://192.168.0.23:8000/#/project-list' }*/),
    page.click("text=View site"),
  ]);

  // Close page
  await page.close();
});
