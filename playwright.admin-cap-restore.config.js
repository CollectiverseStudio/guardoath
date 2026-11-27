const path = require("node:path");

// Isolate future restoration writes from the reader-first source/default suite.
process.env.GUARDOATH_ADMIN_CAP_RESTORE_TEST_DB ||= path.resolve(
  ".guardoath-reports/admin-integration", `cap-restore-${process.pid}-${Date.now()}.sqlite3`);
process.env.GUARDOATH_ADMIN_TEST_DB = process.env.GUARDOATH_ADMIN_CAP_RESTORE_TEST_DB;
const config = require("./playwright.admin.config");

module.exports = {...config,
  testMatch:"admin_cap_restore_mounted.spec.js",
  use:{...config.use,baseURL:"http://127.0.0.1:8783"},
  webServer:{...config.webServer,
    command:"python -m uvicorn admin_browser_app:app --app-dir tests --host 127.0.0.1 --port 8783 --no-access-log",
    url:"http://127.0.0.1:8783/healthz",
    env:{...config.webServer.env,GUARDOATH_ADMIN_TEST_CAP_RESTORE_WRITER_PHASE:"source",
      GUARDOATH_ADMIN_TEST_ORIGIN:"http://127.0.0.1:8783"},
  },
};
