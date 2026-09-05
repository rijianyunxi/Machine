const assert=require('node:assert/strict');const fs=require('node:fs'),path=require('node:path');
const {setup,capture,BASE,OUT,chromium}=require('./audit.cjs');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.UI_AUDIT_BROWSER});const passed=[];
 try {
  for(const theme of ['light','dark']) for(const width of [1440,1024,768,390,320]) {
   const s=await setup(browser,theme,width,1000),p=s.page;
   let mode='normal';
   const items=[1,2,3,4].map(id=>({id,camera_id:'cam-01',camera_name:'一号车间',rule_id:id,rule_name:['未戴安全帽','进入危险区域','未穿反光背心','不应显示第四张'][id-1],status:id===1?'new':'confirmed',timestamp:1788580800-id,snapshot_url:`/snapshots/2026-09-05/rule/${id}.jpg`,snapshot_status:'available'}));
   await p.route('**/api/alerts/recent-snapshots',r=>r.fulfill({status:mode==='error'?500:200,json:mode==='error'?{detail:'模拟失败'}:{items:mode==='empty'?[]:mode==='one'?items.slice(0,1):items}}));
   await p.route('**/api/snapshots/thumb?**',r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#334155"/><rect x="180" y="80" width="200" height="300" fill="none" stroke="#facc15" stroke-width="4"/><text x="40" y="50" fill="white" font-size="26">SYNTHETIC TEST IMAGE</text></svg>'}));
   await p.goto(`${BASE}/app/dashboard`);
   await p.locator('.dashboard-snapshot').nth(2).waitFor();
   await p.waitForFunction(() => document.querySelectorAll('.dashboard-snapshot').length === 3);
   assert.equal(await p.locator('.dashboard-snapshot').count(),3);
   assert.equal(await p.getByText('不应显示第四张',{exact:true}).count(),0);
   assert.equal(await p.locator('.dashboard-snapshot__pending').innerText(),'待复核');
   assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth),width);
   const left=await p.locator('.dashboard-live-card').boundingBox(),top=await p.locator('.dashboard-snapshots-card').boundingBox(),bottom=await p.locator('.dashboard-trend-card').boundingBox();
   assert.ok(bottom.y>=top.y+top.height);
   if(width===1440){assert.ok(top.x>left.x);assert.ok(Math.abs(left.y-top.y)<2);assert.ok(Math.abs(left.y+left.height-bottom.y-bottom.height)<2)}
   assert.ok(bottom.height<360,'trend stays compact');
   await capture(p,`${theme}-${width}-dashboard-three`);
   await p.locator('.dashboard-snapshot__image').first().click();
   await p.locator('#img-modal').waitFor();
   await p.keyboard.press('Escape');
   mode='one';await p.reload();await p.locator('.dashboard-snapshot').waitFor();
   assert.equal(await p.locator('.dashboard-snapshot').count(),1);
   mode='empty';await p.reload();await p.getByText('暂无违规快照',{exact:true}).waitFor();
   await capture(p,`${theme}-${width}-dashboard-no-snapshots`);
   mode='error';await p.reload();await p.getByText('暂时无法获取快照',{exact:true}).waitFor();
   assert.equal(await p.locator('.dashboard-snapshot').count(),0);
   mode='normal';await p.getByRole('button',{name:'重试快照'}).click();await p.locator('.dashboard-snapshot').nth(2).waitFor();
   assert.deepEqual(s.events.errors,[]);assert.equal(s.events.writes.length,0);
   passed.push({theme,width,threeOnly:true,layout:true,lightbox:true,empty:true,errorRetry:true});await s.context.close();
  }
  fs.writeFileSync(path.join(OUT,'dashboard-snapshots-regression.json'),JSON.stringify(passed,null,2));console.log(passed);
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
