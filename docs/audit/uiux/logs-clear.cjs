const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {setup,BASE,OUT,chromium}=require('./audit.cjs');

async function choose(page,label,option){
 const trigger=page.getByRole('combobox',{name:label});
 await trigger.click();
 await page.waitForTimeout(100);
 await page.getByRole('option',{name:option}).dispatchEvent('click');
 await page.waitForTimeout(150);
}

(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.UI_AUDIT_BROWSER});
 const results=[];
 try{
  for(const theme of ['light','dark']) for(const width of [1440,390]){
   const s=await setup(browser,theme,width,width===390?844:1000),p=s.page;
   let clearCalls=0;
   await p.route('**/api/logs/clear',route=>{
    clearCalls++;
    return clearCalls===1
      ? route.fulfill({status:500,json:{detail:'模拟清空失败'}})
      : route.fulfill({json:{ok:true,file:'machine_vision.log',removed_backups:0}});
   });
   await p.route('**/api/logs', route => {
     const level = new URL(route.request().url()).searchParams.get('level');
     const all = [
       '2026-09-05 12:00:00 [INFO] 系统启动成功',
       '2026-09-05 12:00:02 [WARNING] 摄像头连接中断，正在重试',
       '2026-09-05 12:00:05 [ERROR] 模拟推理超时',
       '2026-09-05 12:00:06 [DEBUG] fixture-only debug message'
     ];
     return route.fulfill({json:{lines:level ? all.filter(line => line.includes(`[${level}]`)) : all}});
   });
   await p.goto(`${BASE}/app/logs`);
   await p.getByRole('combobox',{name:'日志等级'}).waitFor();
   await choose(p,'日志等级','ERROR');
   await p.getByText('模拟推理超时').waitFor();
   await p.screenshot({path:path.join(OUT,`${theme}-${width}-logs-error.png`),fullPage:true});
   await choose(p,'日志等级','CRITICAL');
   await p.getByText('当前筛选下暂无 CRITICAL 日志').waitFor();
   await p.screenshot({path:path.join(OUT,`${theme}-${width}-logs-empty.png`),fullPage:true});
   await choose(p,'日志等级','全部级别');
   await p.getByRole('button',{name:/清空日志/}).click();
   await p.locator('#confirm-yes').click();
   await p.getByText('模拟清空失败',{exact:true}).waitFor();
   assert.equal(clearCalls,1);
   await p.getByRole('button',{name:/清空日志/}).click();
   await p.locator('#confirm-yes').click();
   await p.getByText('日志已清空',{exact:true}).waitFor();
   assert.equal(clearCalls,2);
   await p.screenshot({path:path.join(OUT,`${theme}-${width}-logs-clear.png`),fullPage:true});
   assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth),width);
   results.push({theme,width,clearCalls,passed:true,unmapped:s.events.unmapped.slice()});
   await s.context.close();
  }
 }finally{
  fs.writeFileSync(path.join(OUT,'logs-clear-regression.json'),JSON.stringify(results,null,2));
  await browser.close();
 }
 console.table(results.map(({theme,width,clearCalls})=>({theme,width,clearCalls})));
})().catch(err=>{console.error(err);process.exitCode=1});
