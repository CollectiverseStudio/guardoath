const { defineConfig } = require("@playwright/test");
module.exports = defineConfig({
  testDir: "tests/browser", timeout: 30000, fullyParallel: false,
  use: { baseURL: "http://127.0.0.1:8777", viewport: { width: 1440, height: 1100 },
         ...(process.env.PLAYWRIGHT_CHANNEL ? {channel: process.env.PLAYWRIGHT_CHANNEL} : {}) },
  webServer: {
    command: "python -m uvicorn valinator.app:app --host 127.0.0.1 --port 8777 --no-access-log",
    url: "http://127.0.0.1:8777/healthz", timeout: 60000,
    env: { VALINATOR_API_KEY: "playwright-test-key", VALINATOR_ENABLE_PII: "1", VALINATOR_POLICY_FILE: "policies/pii-only.json" },
  },
});
