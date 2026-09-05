// Assertions against the real React UI with the audit's intercepted API fixtures.
const assert = require('node:assert/strict');
const {setup,BASE,OUT,chromium}=require('./audit.cjs');
const fs=require('node:fs'), path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.UI_AUDIT_BROWSER?{executablePath:process.env.UI_AUDIT_BROWSER}:{})});
 const passed=[];
 try {
  for(const theme of ['light','dark']) {
   const s=await setup(browser,theme),p=s.page;
   await p.goto(`${BASE}/app/cameras`);
   const remove=p.getByRole('button',{name:'删除',exact:true}).first();
   await remove.click();await p.getByRole('alertdialog',{name:'操作确认'}).waitFor();
   assert.equal(await p.evaluate(()=>document.activeElement.textContent.trim()),'取消');
   await p.keyboard.press('Enter');await p.waitForTimeout(100);
   assert.equal(s.events.writes.length,0);assert.equal(await p.getByRole('alertdialog').count(),0);
   await remove.click();await p.locator('#confirm-yes').focus();await p.keyboard.press('Enter');await p.waitForTimeout(100);
   assert.equal(s.events.writes.length,1);assert.equal(s.events.writes[0].method,'DELETE');
   await p.getByRole('button',{name:'新增监控',exact:true}).click();
   const dialog=p.getByRole('dialog',{name:'新增监控',exact:true});await dialog.waitFor();
   assert.equal(await p.evaluate(()=>document.body.style.overflow),'hidden');
   await p.keyboard.press('Shift+Tab');assert.equal(await p.evaluate(()=>!!document.activeElement.closest('[role="dialog"]')),true);
   await p.keyboard.press('Tab');assert.equal(await p.evaluate(()=>document.activeElement.getAttribute('aria-label')),'关闭');
   await p.keyboard.press('Escape');assert.equal(await dialog.count(),0);
   assert.equal(await p.evaluate(()=>document.activeElement.textContent.trim()),'新增监控');
   assert.equal(await p.evaluate(()=>document.body.style.overflow),'');
   await p.goto(`${BASE}/app/rules`);
   await p.getByRole('button',{name:'新建规则',exact:true}).click();
   await p.locator('.graph-pal-item').first().click();
   await p.getByRole('button',{name:'删除节点',exact:true}).click();
   await p.getByRole('alertdialog').waitFor();
   await p.keyboard.press('Escape');
   assert.equal(await p.getByRole('alertdialog').count(),0);
   assert.equal(await p.getByRole('dialog').count(),1);
   assert.equal(await p.evaluate(()=>document.body.style.overflow),'hidden');
   await p.keyboard.press('Escape');assert.equal(await p.getByRole('dialog').count(),0);
   await p.goto(`${BASE}/app/login`);
   await p.getByLabel('用户名',{exact:true}).fill('fixture');await p.getByLabel('密码',{exact:true}).fill('wrong');
   await p.getByRole('button',{name:'登 录'}).click();await p.getByRole('alert').waitFor();
   assert.equal(await p.getByLabel('密码',{exact:true}).getAttribute('aria-invalid'),'true');
   await s.context.close();
   const e=await setup(browser,theme,1440,1000,'error');
   await e.page.goto(`${BASE}/app/settings`);await e.page.getByRole('button',{name:'重试',exact:true}).waitFor();
   assert.match(await e.page.getByRole('alert').innerText(),/加载失败/);assert.equal(e.events.errors.length,0);
   await e.page.route('**/api/settings',async route=>route.fulfill({json:{sections:{},pending_restart:{}}}));
   await e.page.getByRole('button',{name:'重试',exact:true}).click();await e.page.getByRole('button',{name:'重启服务'}).waitFor();
   assert.equal(await e.page.getByRole('alert').count(),0);await e.context.close();
   for (const width of [320,390,768]) {
    const n=await setup(browser,theme,width,844);
    for(const route of ['login','models','settings','train']) {
     await n.page.goto(`${BASE}/app/${route}`);await n.page.waitForTimeout(150);
     assert.equal(await n.page.evaluate(()=>document.documentElement.scrollWidth),width,`${theme}/${route}/${width} overflow`);
    }
    const nav=await n.page.locator('.nav').ariaSnapshot();assert.match(nav,/link "监控管理"/);
    await n.context.close();
   }
   passed.push(`${theme}: cancel/confirm Enter, focus trap/restore, scroll lock, login labels/error, settings error/retry, responsive 320/390/768, named navigation`);
  }
  fs.mkdirSync(OUT,{recursive:true});fs.writeFileSync(path.join(OUT,'regression.json'),JSON.stringify({passed},null,2));console.log(passed.join('\n'));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
