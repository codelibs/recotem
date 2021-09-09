const { writeFile } = require("fs");

const sleep = (msec) => new Promise((resolve) => setTimeout(resolve, msec));

async function login(page) {
  await page.goto("http://localhost:8000/#/login?redirect=%2Fproject-list");

  // Click input[name="username"]
  await page.click('input[name="username"]');

  // Fill input[name="username"]
  await page.fill('input[name="username"]', "admin");

  // Press Tab
  await page.press('input[name="username"]', "Tab");

  // Fill input[name="password"]
  await page.fill('input[name="password"]', "very_bad_password");

  // Click button:has-text("Login")
  await Promise.all([
    page.waitForNavigation(/*{ url: "http://localhost:8000/#/project-list" }*/),
    page.click('button:has-text("Login")'),
  ]);
}

async function loginAndCreateProject(page, projectName) {
  await login(page);
  // Go to http://localhost:8000/#/login?redirect=%2Fproject-list

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

  // Click button:has-text("Create new project")
  await Promise.all([
    page.waitForNavigation(/*{ url: 'http://localhost:8000/#/project/1/' }*/),
    page.click('button:has-text("Create new project")'),
  ]);
}

async function savePageHTML(page, savePath) {
  const html = await page.innerHTML("body");
  writeFile(savePath, html, (err) => {
    if (err) throw err;
    console.log("The file has been saved!");
  });
}

async function screenshotWithPrefix(elm, pageName, name) {
  console.log(`${pageName}.${name}`);
  await elm.screenshot({
    path: `imgs/user/${pageName}.${name}.png`,
    fullPage: true,
  });
}
module.exports = {
  sleep,
  screenshotWithPrefix,
  savePageHTML,
  login,
  loginAndCreateProject,
};
