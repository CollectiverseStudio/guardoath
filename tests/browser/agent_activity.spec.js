const { test, expect } = require("@playwright/test");

test("authenticated AI agent activity panel distinguishes mismatch, matching reports and missing evidence", async ({page}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");

  await page.locator("#agent-sample-matching").click();
  await page.locator("#agent-activity-check").click();
  await expect(page.locator("#agent-activity-result")).toContainText("execution is NOT verified");
  await expect(page.locator("#agent-activity-result")).toContainText("No tool is executed or authorized");

  await page.locator("#agent-sample-changed").click();
  await page.locator("#agent-activity-check").click();
  await expect(page.locator("#agent-activity-result")).toContainText("Mismatch");
  await expect(page.locator("#agent-activity-result")).toContainText("agent_activity.action_mismatch");
  await expect(page.locator("#agent-activity-result")).not.toContainText("Clear-EventLog");

  await page.locator("#agent-sample-missing").click();
  await page.locator("#agent-activity-check").click();
  await expect(page.locator("#agent-activity-result")).toContainText("cannot be verified");
  await expect(page.locator("#agent-activity-result")).not.toContainText("No findings");

  await page.locator("#agent-proposed").fill('{"tool":"shell.run","target":"host-42","arguments":{"command":"Get-Process"},"trusted":true}');
  await page.locator("#agent-activity-check").click();
  await expect(page.locator("#agent-activity-result")).toContainText("could not complete");
  await expect(page.locator("#agent-activity-result")).toContainText("No action was authorized");
});
