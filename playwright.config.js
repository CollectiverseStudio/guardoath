const { defineConfig } = require("@playwright/test");
module.exports = defineConfig({
  // Browser suites reuse the same memory-backed demo tenants and mutate their
  // checks/revisions. Separate Playwright workers share the one API process;
  // per-file fullyParallel=false does not isolate them from each other.
  // Serial execution prevents one suite's Reset/Save from invalidating another
  // suite's in-flight Input/Output check; keep per-test 30s deadlines intact.
  testDir: "tests/browser", timeout: 30000, fullyParallel: false, workers: 1,
  use: { baseURL: "http://127.0.0.1:8777", viewport: { width: 1440, height: 1100 },
         ...(process.env.PLAYWRIGHT_CHANNEL ? {channel: process.env.PLAYWRIGHT_CHANNEL} : {}) },
  webServer: {
    command: "python -m uvicorn guardoath.app:app --host 127.0.0.1 --port 8777 --no-access-log",
    url: "http://127.0.0.1:8777/healthz", timeout: 60000,
    env: { GUARDOATH_CHECKS_PER_MINUTE: "60000", GUARDOATH_BURST: "1000", GUARDOATH_API_KEY: "playwright-test-key", GUARDOATH_ENABLE_PII: "1", GUARDOATH_POLICY_FILE: "policies/playground.json", GUARDOATH_TENANT_STORE: "memory", GUARDOATH_DEMO_KEY_SECRET: "playwright-demo-key-secret-0123456789" },
  },
});
