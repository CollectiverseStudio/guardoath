const path = require("node:path");

// The dedicated database exercises the activated refund writer with unchanged
// source gates; the baseline browser configuration keeps explicit disabled coverage.
process.env.GUARDOATH_ADMIN_REFUND_TEST_DB ||= path.resolve(
  ".guardoath-reports/admin-integration", `refund-${process.pid}-${Date.now()}.sqlite3`);
process.env.GUARDOATH_ADMIN_TEST_DB = process.env.GUARDOATH_ADMIN_REFUND_TEST_DB;
const config = require("./playwright.admin.config");

module.exports = {...config,
  testMatch:"admin_refund_mounted.spec.js",
  use:{...config.use,baseURL:"http://127.0.0.1:8782"},
  webServer:{...config.webServer,
    command:"python -m uvicorn admin_browser_app:app --app-dir tests --host 127.0.0.1 --port 8782 --no-access-log",
    url:"http://127.0.0.1:8782/healthz",
    env:{...config.webServer.env,GUARDOATH_ADMIN_TEST_WRITER_PHASE:"source",
      GUARDOATH_ADMIN_TEST_REFUND_WRITER_PHASE:"source",
      GUARDOATH_ADMIN_TEST_ORIGIN:"http://127.0.0.1:8782"},
  },
};
