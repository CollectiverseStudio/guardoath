const {defineConfig} = require("@playwright/test");

// These tests require a real authenticated ASGI fixture and persisted ledger.
// The default demo browser suite intentionally skips them.
process.env.GUARDOATH_INVOCATION_BROWSER = "1";
const server = (name, port) => ({
  command: `python -m uvicorn invocation_browser_app:${name} --app-dir tests --host 127.0.0.1 --port ${port} --no-access-log`,
  url: `http://127.0.0.1:${port}/healthz`, timeout: 60000,
  env: {GUARDOATH_CHECKS_PER_MINUTE: "60000", GUARDOATH_BURST: "1000",
    GUARDOATH_INVOCATIONS_ENABLED: "0", GUARDOATH_ADMIN_ENABLED: "0"},
});
module.exports = defineConfig({
  testDir: "tests/browser", testMatch: "invocation*.spec.js", timeout: 30000,
  fullyParallel: false, workers: 1,
  use: {baseURL: "http://127.0.0.1:8778", viewport: {width: 1440, height: 1100},
    ...(process.env.PLAYWRIGHT_CHANNEL ? {channel: process.env.PLAYWRIGHT_CHANNEL} : {})},
  webServer: [server("app", 8778), server("unconfigured_app", 8779)],
});
