const {defineConfig} = require("@playwright/test");
const path = require("node:path");

// A unique disk database is shared only by this real-router acceptance run.
// Identity and payment boundaries are synthetic; no cloud client is created.
process.env.GUARDOATH_ADMIN_TEST_DB ||= path.resolve(
  ".guardoath-reports/admin-integration", `acceptance-${process.pid}-${Date.now()}.sqlite3`);
module.exports = defineConfig({
  testDir:"tests/browser",testMatch:"admin_integration.spec.js",timeout:30000,
  fullyParallel:false,workers:1,
  use:{baseURL:"http://127.0.0.1:8780",viewport:{width:1440,height:1100},
    ...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {})},
  webServer:{
    cwd:__dirname,
    command:"python -m uvicorn admin_browser_app:app --app-dir tests --host 127.0.0.1 --port 8780 --no-access-log",
    url:"http://127.0.0.1:8780/healthz",timeout:60000,
    env:{GUARDOATH_ADMIN_TEST_DB:process.env.GUARDOATH_ADMIN_TEST_DB,GUARDOATH_ADMIN_TEST_WRITER_PHASE:"source",
      GUARDOATH_ADMIN_TEST_REFUND_WRITER_PHASE:"source",
      GUARDOATH_ADMIN_TEST_ORIGIN:"http://127.0.0.1:8780",
      GUARDOATH_ADMIN_ENABLED:"0",GUARDOATH_INVOCATIONS_ENABLED:"0",
      GUARDOATH_TENANT_STORE:"memory",GUARDOATH_DEMO_KEY_SECRET:"synthetic-admin-browser-demo-key-not-production-0123456789",
      GUARDOATH_COGNITO_POOL_ID:"",GUARDOATH_COGNITO_CLIENT_ID:"",GUARDOATH_COGNITO_DOMAIN:"",
      GUARDOATH_COGNITO_REDIRECT_URI:"",GUARDOATH_SELF_REGISTRATION:"0",GUARDOATH_GOOGLE_LOGIN_ENABLED:"0",
      GUARDOATH_PACK_FILE:"",GUARDOATH_POLICY_FILE:"",
      GUARDOATH_CHECKS_PER_MINUTE:"60000",GUARDOATH_BURST:"1000",AWS_EC2_METADATA_DISABLED:"true"},
  },
});
