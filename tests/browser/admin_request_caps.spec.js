"use strict";

const {test,expect} = require("@playwright/test");
const {installAdminFixture,openFixtureSession} = require("./admin_fixture");

for (const scenario of [
  {name:"recognized zero-cap conflict",status:409,code:"unbound-zero-cap",explained:true},
  {name:"unrecognized conflict",status:409,code:"provider-private-detail",explained:false},
  {name:"ordinary revision conflict",status:409,explained:false},
  {name:"unavailable service",status:503,code:"unbound-zero-cap",explained:false},
]) {
  test(`request cap shows only a fixed safe explanation for ${scenario.name}`,async ({page})=>{
    const fixture = await installAdminFixture(page,{features:{monthly_cost_caps:true},override:request=>{
      if (request.method==="POST" && request.path==="/v1/admin/tenants/sample-studio/allowance-cap")
        return {status:scenario.status,contentType:"application/json",
          headers:scenario.code ? {"X-GuardOath-Admin-Error":scenario.code} : {},
          body:JSON.stringify({detail:"sensitive-provider-body-must-not-render"})};
      return null;
    }});
    const before = structuredClone(fixture.tenants);
    await openFixtureSession(page);
    await expect(page.locator("#status")).toContainText("Administration loaded");
    await page.locator("#nav-tenants").click();
    const answers = ["0","sample-studio","Verify synthetic budget refusal"];
    page.on("dialog",async dialog=>{
      if (dialog.type()==="confirm") await dialog.accept();
      else { expect(answers.length).toBeGreaterThan(0); await dialog.accept(answers.shift()); }
    });
    await page.locator("#tenant-rows tr").filter({hasText:"sample-studio"})
      .getByRole("button",{name:"Cap usage",exact:true}).click();
    await expect(page.locator("#status")).toContainText(scenario.explained
      ? "Use Suspend to block access" : `Request failed (${scenario.status})`);
    if (!scenario.explained) await expect(page.locator("#status")).not.toContainText("Use Suspend");
    await expect(page.locator("body")).not.toContainText("sensitive-provider-body-must-not-render");
    expect(fixture.tenants).toEqual(before);
    expect(fixture.calls.filter(request=>request.method==="POST" && request.path.endsWith("/allowance-cap"))).toHaveLength(1);
    expect(answers).toEqual([]);
  });
}
