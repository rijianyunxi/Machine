const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {setup,capture,BASE,OUT,chromium}=require('./audit.cjs');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.UI_AUDIT_BROWSER});const passed=[];
 try {
  for(const theme of ['light','dark']) for(const width of [1440,390,320]) {
   const s=await setup(browser,theme,width,900),p=s.page;
   let records=[1,2].map(id=>({id,camera_id:'cam-01',camera_name:'一号车间',rule_id:1,rule_name:'安全通道人员检测',confidence:.93,status:'new',timestamp:1788580800,snapshot_status:'none'}));
   let deleted=[],fail=false;
   await p.route('**/api/alerts?**',r=>r.fulfill({json:{items:records,total:records.length}}));
   await p.route('**/api/alerts/batch-delete',r=>{const ids=r.request().postDataJSON().ids;deleted.push(ids);if(fail)return r.fulfill({status:500,json:{detail:'模拟删除失败'}});records=records.filter(x=>!ids.includes(x.id));return r.fulfill({json:{deleted:ids.length,snapshots_deleted:ids.length,shared_snapshots_kept:0,cleanup_pending:false}})});
   await p.goto(`${BASE}/app/alerts`);
   const all=p.getByRole('checkbox',{name:'全选当前页告警'});
   await all.waitFor();
   assert.equal(await p.getByRole('button',{name:'批量删除',exact:true}).isDisabled(),true);
   await p.getByRole('checkbox',{name:'选择告警 1',exact:true}).check();
   assert.equal(await all.evaluate(e=>e.indeterminate),true);
   await p.getByRole('button',{name:'批量删除 (1)',exact:true}).click();
   await p.getByRole('alertdialog').waitFor();
   assert.match(await p.getByRole('alertdialog').innerText(),/快照.*无法恢复/);
   await p.keyboard.press('Enter');
   assert.equal(deleted.length,0);
   await capture(p,`${theme}-${width}-alerts-selected`);
   fail=true;
   await p.getByRole('button',{name:'批量删除 (1)',exact:true}).click();
   await p.getByRole('button',{name:'删除告警及快照',exact:true}).click();
   await p.getByText('模拟删除失败',{exact:true}).waitFor();
   assert.equal(await p.getByRole('checkbox',{name:'选择告警 1',exact:true}).isChecked(),true);
   fail=false;
   await all.check();
   await p.getByRole('button',{name:'批量删除 (2)',exact:true}).click();
   await capture(p,`${theme}-${width}-alerts-confirm`);
   await p.getByRole('button',{name:'删除告警及快照',exact:true}).click();
   await p.getByText('无符合条件的告警',{exact:true}).waitFor();
   assert.deepEqual(deleted.at(-1),[1,2]);
   assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth),width);
   await p.goto(`${BASE}/app/dashboard`);
   await p.getByRole('heading',{name:'暂无告警记录',exact:true}).waitFor();
   await capture(p,`${theme}-${width}-dashboard-empty`);
   assert.equal(await p.getByRole('link',{name:'检查监控状态'}).getAttribute('href'),'/app/cameras');
   await p.route('**/api/alerts?limit=10',r=>r.fulfill({status:500,json:{detail:'test'}}));
   await p.getByRole('heading',{name:'暂时无法获取告警',exact:true}).waitFor({timeout:10000});
   await capture(p,`${theme}-${width}-dashboard-error`);
   await p.unroute('**/api/alerts?limit=10');
   records=[{id:3,camera_id:'cam-01',camera_name:'一号车间',rule_id:1,rule_name:'测试长规则名称_安全通道人员检测_请及时处理',confidence:.93,status:'new',timestamp:1788580800}];
   await p.getByRole('button',{name:'重试',exact:true}).click();
   await p.getByRole('list',{name:'最近告警'}).waitFor();
   await capture(p,`${theme}-${width}-dashboard-list`);
   assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth),width);
   assert.deepEqual(s.events.errors,[]);assert.equal(s.events.writes.length,0);
   passed.push({theme,width,selection:true,cancel:true,failureRetry:true,delete:true,empty:true,feedErrorRecovery:true,noOverflow:true});await s.context.close();
  }
  fs.writeFileSync(path.join(OUT,'alerts-regression.json'),JSON.stringify(passed,null,2));console.log(passed);
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
