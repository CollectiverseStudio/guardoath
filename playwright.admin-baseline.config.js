const path = require("node:path");

// Keep the same database path when Playwright workers reload configuration.
process.env.GUARDOATH_ADMIN_BASELINE_TEST_DB ||= path.resolve(
  ".guardoath-reports/admin-integration", `baseline-${process.pid}-${Date.now()}.sqlite3`);
process.env.GUARDOATH_ADMIN_TEST_DB = process.env.GUARDOATH_ADMIN_BASELINE_TEST_DB;
const config = require("./playwright.admin.config");

module.exports = {...config,
  testMatch:"admin_baseline.spec.js",
  use:{...config.use,baseURL:"http://127.0.0.1:8781"},
  webServer:{...config.webServer,
    command:"python -m uvicorn admin_browser_app:app --app-dir tests --host 127.0.0.1 --port 8781 --no-access-log",
    url:"http://127.0.0.1:8781/healthz",
    env:{...config.webServer.env,GUARDOATH_ADMIN_TEST_WRITER_PHASE:"0",
      GUARDOATH_ADMIN_TEST_REFUND_WRITER_PHASE:"disabled",
      GUARDOATH_ADMIN_TEST_CAP_RESTORE_WRITER_PHASE:"disabled",
      GUARDOATH_ADMIN_TEST_ORIGIN:"http://127.0.0.1:8781"},
  },
};
