/* Real SPA + synthetic API fixtures; never sends API writes to a backend.
   Usage: PLAYWRIGHT_MODULE=/absolute/path/to/playwright node audit.cjs
   Start Vite separately on 127.0.0.1:5173. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const OUT = process.env.UI_AUDIT_OUT || path.join(__dirname, 'results');
fs.mkdirSync(OUT, {recursive:true});
const BASE = process.env.UI_AUDIT_BASE || 'http://127.0.0.1:5173';
const routes = ['login','dashboard','cameras','models','datasets','annotate','rules','detect','alerts','snapshots','settings','logs','train'];
const now = 1788580800;
const camera = {id:'cam-01',name:'一号车间 · 安全通道',url:'rtsp://fixture.invalid/stream',enabled:true,revision:1,connected:true,thread_alive:true,rules:[1],frames_captured:24518,frame_age:0.1};
const rule = {id:1,revision:1,name:'安全通道人员检测',description:'检测人员进入限制区域并生成告警',category:'作业安全',template:'presence',models:['safety-yolo'],params:{classes:['person']},severity:3,enabled:true,cameras:['cam-01'],warnings:[]};
const model = {name:'safety-yolo',revision:1,path:'models/safety.pt',file_exists:true,config_enabled:true,loaded:true,device:'cpu',confidence:0.45,iou:0.5,img_size:640,classes:{0:'person',1:'helmet'},confidence_override:null};
const dataset = {name:'workshop-safety',classes:['person','helmet'],images:120,labeled:96,splits:{train:{images:100,labeled:80},val:{images:20,labeled:16},test:{images:0,labeled:0}}};
const alert = {id:1,camera_id:'cam-01',camera_name:camera.name,rule_id:1,rule_name:rule.name,confidence:0.93,status:'new',timestamp:now,snapshot_url:'/snapshots/fixture.svg',snapshot_status:'available',note:'模拟审查数据'};
const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540"><rect width="960" height="540" fill="#1e293b"/><path d="M0 380 L960 300 M180 0 L250 540 M720 0 L650 540" stroke="#64748b" stroke-width="8"/><rect x="360" y="130" width="140" height="300" fill="none" stroke="#34d399" stroke-width="4"/><circle cx="430" cy="180" r="32" fill="#cbd5e1"/><path d="M430 220v120m0-90l-45 65m45-65l45 65m-45 25l-40 80m40-80l40 80" stroke="#cbd5e1" stroke-width="18"/><text x="24" y="46" fill="#fff" font-size="24">SYNTHETIC UI FIXTURE - NOT LIVE VIDEO</text></svg>`;
const settings = {sections:{capture:{label:'视频采集',restart_required:true,revision:1,keys:[{key:'fps',type:'int',value:15,desc:'采集帧率'},{key:'reconnect_interval',type:'float',value:5,desc:'断线重连间隔（秒）'}]},snapshot:{label:'快照存储',restart_required:false,revision:1,keys:[{key:'enabled',type:'bool',value:true,desc:'启用告警快照'},{key:'retention_days',type:'int',value:30,desc:'保留天数'}]},alert:{label:'告警策略',restart_required:false,revision:1,keys:[{key:'cooldown',type:'int',value:60,desc:'同规则告警冷却（秒）'}]},logging:{label:'日志',restart_required:true,revision:1,keys:[{key:'level',type:'str',value:'INFO',desc:'日志等级'}]},panel:{label:'面板',restart_required:true,revision:1,keys:[{key:'port',type:'int',value:8000,desc:'监听端口'}]},llm:{label:'大模型服务',restart_required:false,revision:1,keys:[{key:'enabled',type:'bool',value:false,desc:'开启辅助复核'},{key:'model',type:'str',value:'fixture-model',desc:'模型名称'},{key:'base_url',type:'str',value:'https://fixture.invalid/v1',desc:'服务地址'},{key:'api_key',type:'str',value:'',configured:true,desc:'API 密钥'}]}},pending_restart:{}};
function fixture(p,empty){
 const list = v => empty ? [] : v;
 const map={
 '/api/system/info':{}, '/api/system/stats':{standalone:false,uptime:86400,frames_processed:24518,avg_fps:14.8},
 '/api/cameras':{cameras:list([camera,{...camera,id:'cam-02',name:'二号仓库 · 装卸入口',connected:false,frame_age:300}])},
 '/api/models':{models:list([model]),files:list([{file:'safety.pt',size_mb:6.2,validation:{status:'valid',classes:model.classes},registered_as:model.name}])},
 '/api/rules':{rules:list([rule])}, '/api/rules/templates':{templates:{presence:{label:'目标在场',logic:'presence',revision:1,params:[{name:'classes',type:'classes',default:['person'],desc:'目标类别'}]}}},
 '/api/rules/template-logics':{logics:{presence:{label:'目标在场',desc:'检测目标出现'}}},
 '/api/rules/node-types':{node_types:{presence:{label:'类别在场',category:'目标',inputs:0,outputs:1,model_binding:true,params:[]},alert:{label:'告警',category:'输出',inputs:1,outputs:0,params:[]}}},
 '/api/alerts/recent-snapshots':{items:list([])},
 '/api/alerts/summary':{by_rule:{1:{false_positive_rate:0.03}}}, '/api/alerts':{items:list([alert,{...alert,id:2,status:'confirmed',timestamp:now-3600}]),total:empty?0:2},
 '/api/storage/usage':{snapshots_total_mb:128.5,disk_used_pct:42,disk_free_gb:68,watermark:'ok'},
 '/api/system/stats/history':{trend:Array.from({length:7},(_,i)=>({day:new Date(Date.UTC(2026,7,30+i)).toISOString().slice(0,10),total:12+i*3,confirmed:8+i,pending:2,false_positive:2}))},
 '/api/datasets':{datasets:list([dataset])}, '/api/datasets/workshop-safety':dataset,
 '/api/datasets/workshop-safety/images':{images:list([{file:'fixture.svg',stem:'fixture',split:'train',labeled:true}])},
 '/api/datasets/workshop-safety/labels/fixture':{boxes:[{cls:0,x:0.45,y:0.52,w:0.15,h:0.56}]},
 '/api/datasets/workshop-safety/prelabel_status':{running:false,dataset:dataset.name,done:96,total:120,failed:0,models:[model.name],logs:[]},
 '/api/snapshots':{dates:list([{date:'2026-09-05',count:2,size_mb:0.4}]),files:list([1,2].map(i=>({name:`fixture-${i}.jpg`,size_kb:220,thumb:'/snapshots/fixture.svg',url:'/snapshots/fixture.svg',camera:'cam-01',rule_dir:'safety',date:'2026-09-05',mtime:now}))),total:empty?0:2,total_size_mb:0.4,offset:0,limit:60},
 '/api/detect/test/history':{results:list([{time:'2026-09-05 12:00:00',detections:[{class_name:'person',confidence:0.93,bbox:[360,130,500,430]}],latency_ms:42,annotated_url:'/test_results/fixture.svg'}])},
 '/api/train/runs':{runs:list([{name:'safety-baseline',best:'runs/safety-baseline/weights/best.pt',size_mb:6.2}])},
 '/api/train/status':{state:empty?'idle':'running',name:'safety-baseline',epoch:12,epochs_total:50,mAP50:0.89,mAP50_95:0.65,best_path:'runs/safety-baseline/weights/best.pt',log_tail:['Epoch 12/50 loss=0.234 mAP50=0.89']},
 '/api/logs':{lines:list(['2026-09-05 12:00:00 [INFO] 系统启动成功','2026-09-05 12:00:02 [WARNING] 摄像头连接中断，正在重试','2026-09-05 12:00:05 [ERROR] 模拟推理超时','2026-09-05 12:00:06 [DEBUG] fixture-only debug message'])},
 '/api/settings':settings
 };
 return map[p];
}
async function setup(browser,theme,width=1440,height=1000,mode='normal'){
 const context=await browser.newContext({viewport:{width,height},colorScheme:theme,locale:'zh-CN'});
 await context.addInitScript(t=>{if(!localStorage.getItem('panel-theme'))localStorage.setItem('panel-theme',t)},theme);
 const events={unmapped:[],writes:[],errors:[],console:[]};
 await context.route('**/*',async route=>{
  const req=route.request(),u=new URL(req.url()),p=u.pathname;
  if(u.origin!==BASE){events.unmapped.push(req.url()); return route.abort();}
  if(p.startsWith('/snapshots/')||p.startsWith('/test_results/')||p.includes('/image/')||/^\/api\/cameras\/[^/]+\/frame\.jpg$/.test(p)) return route.fulfill({contentType:'image/svg+xml',body:svg});
  if(!p.startsWith('/api/'))return route.continue();
  if(req.method()!=='GET'){
   events.writes.push({method:req.method(),path:p,body:req.postData()});
   if(p==='/api/login')return route.fulfill({status:401,json:{detail:'用户名或密码错误'}});
   if(req.method()==='DELETE'&&p.startsWith('/api/cameras/'))return route.fulfill({json:{ok:true}});
   events.unmapped.push(req.method()+' '+p); return route.fulfill({status:501,json:{detail:'Audit blocked unmapped write'}});
  }
  if(mode==='loading'&&p==='/api/system/info') await new Promise(r=>setTimeout(r,10000));
  if(mode==='error'&&p==='/api/settings')return route.fulfill({status:500,json:{detail:'模拟服务异常'}});
  const data=fixture(p,mode==='empty');
  if(data===undefined){events.unmapped.push(p);return route.fulfill({status:501,json:{detail:'Unmapped audit fixture'}});}
  return route.fulfill({json:data});
 });
 const page=await context.newPage();
 page.on('pageerror',e=>events.errors.push(e.message));
 page.on('console',m=>{if(m.type()==='error')events.console.push(m.text())});
 return {context,page,events};
}
async function capture(page,name){
 await page.screenshot({path:path.join(OUT,name+'.png'),fullPage:true,animations:'disabled'});
 return page.evaluate(()=>{
  const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right}};
  const sample=sel=>{const e=document.querySelector(sel);if(!e)return null;const s=getComputedStyle(e);return {text:e.textContent.trim().slice(0,90),color:s.color,bg:s.backgroundColor,image:s.backgroundImage,radius:s.borderRadius,size:s.fontSize,...rect(e)}};
  const visible=e=>!!(e.getBoundingClientRect().width&&e.getBoundingClientRect().height)&&getComputedStyle(e).visibility!=='hidden';
  return {theme:document.documentElement.dataset.theme,width:innerWidth,scrollWidth:document.documentElement.scrollWidth,bodyText:document.body.innerText.slice(0,9000),
   samples:{card:sample('.card'),main:sample('.main'),sidebar:sample('.sidebar'),button:sample('.page-actions button, .page-header button, button:not([class])'),muted:sample('.muted'),nav:sample('.nav .active'),pageSub:sample('.page-sub'),label:sample('label'),logInfo:sample('pre.log .INFO'),log:sample('pre.log')},
   tokens:Object.fromEntries(['--accent','--muted','--surface','--surface-2','--text','--text-2','--radius','--radius-sm'].map(k=>[k,getComputedStyle(document.documentElement).getPropertyValue(k).trim()])),
   overflow:[...document.querySelectorAll('main, .main, .card, table, input, button, .anno-layout, canvas, .page-head')].filter(visible).filter(e=>e.getBoundingClientRect().right>innerWidth+1||e.getBoundingClientRect().left < -1).slice(0,25).map(e=>({tag:e.tagName,cls:e.className,text:e.textContent.trim().slice(0,70),...rect(e)})),
   unnamedFields:[...document.querySelectorAll('input,textarea,button[role="combobox"]')].filter(visible).filter(e=>!e.labels?.length&&!e.getAttribute('aria-label')&&!e.getAttribute('aria-labelledby')&&!e.title).map(e=>({tag:e.tagName,type:e.type,placeholder:e.getAttribute('placeholder'),id:e.id})),
   smallControls:[...document.querySelectorAll('button,a,input')].filter(visible).filter(e=>e.getBoundingClientRect().height<24).map(e=>({text:e.textContent.trim().slice(0,25),...rect(e)}))};
 });
}
async function main(){
 const browser=await chromium.launch({headless:true, ...(process.env.UI_AUDIT_BROWSER ? {executablePath:process.env.UI_AUDIT_BROWSER} : {})});
 const report={date:'2026-09-05',browser:browser.version(),environment:'Chromium + real Vite SPA + intercepted synthetic API',matrix:[],interactions:[]};
 try{
  for(const theme of ['light','dark'])for(const width of [1440,390]){
   const s=await setup(browser,theme,width,width===390?844:1000);
   for(const r of routes){
    const start={errors:s.events.errors.length,console:s.events.console.length,unmapped:s.events.unmapped.length};
    await s.page.goto(`${BASE}/app/${r}${r==='annotate'?'?ds=workshop-safety':''}`);
    await s.page.waitForTimeout(550);
    const data=await capture(s.page,`${theme}-${width}-${r}`);
    report.matrix.push({route:r,theme,width,...data,errors:s.events.errors.slice(start.errors),console:s.events.console.slice(start.console),unmapped:s.events.unmapped.slice(start.unmapped)});
    console.log(theme,width,r,'overflow',data.overflow.length,'errors',s.events.errors.length-start.errors);
   }
   await s.context.close();
  }
  for(const theme of ['light','dark']){
   const s=await setup(browser,theme); const p=s.page; const result={theme};
   await p.goto(`${BASE}/app/cameras`); await p.getByRole('button',{name:'新增监控',exact:true}).click();
   const dialog=p.getByRole('dialog');await dialog.waitFor();
   await capture(p,`${theme}-modal-camera`);
   result.dialogName=await dialog.evaluate(e=>({label:e.getAttribute('aria-label'),labelledby:e.getAttribute('aria-labelledby')}));
   result.initialFocus=await p.evaluate(()=>document.activeElement.outerHTML);
   await p.keyboard.press('Shift+Tab');result.shiftTabEscapes=await p.evaluate(()=>!document.activeElement.closest('[role="dialog"]'));
   result.focusAfterShiftTab=await p.evaluate(()=>document.activeElement.outerHTML);
   await p.keyboard.press('Escape');result.escapeClosed=await dialog.count()===0;
   result.focusRestored=await p.evaluate(()=>document.activeElement.textContent.trim());
   await p.getByRole('button',{name:'删除',exact:true}).first().click();
   await p.getByRole('alertdialog').waitFor();
   result.confirmInitialFocus=await p.evaluate(()=>document.activeElement.textContent.trim());
   await capture(p,`${theme}-confirm-cancel-focus`);
   await p.keyboard.press('Enter');await p.waitForTimeout(150);
   result.cancelEnterWrites=s.events.writes.slice();
   await p.locator('.theme-toggle').click();result.afterToggle=await p.locator('html').getAttribute('data-theme');
   await p.locator('a[href="/app/models"]').click();result.afterNavigation=await p.locator('html').getAttribute('data-theme');
   await p.reload();await p.waitForTimeout(150);result.afterReload=await p.locator('html').getAttribute('data-theme');
   // Custom select keyboard and expanded state.
   await p.goto(`${BASE}/app/alerts`);await p.waitForTimeout(300);
   const select=p.locator('.select-trigger').first();
   if(await select.count()){
    await select.focus();await p.keyboard.press('ArrowDown');
    result.selectAfterArrow={expanded:await select.getAttribute('aria-expanded'),options:await p.getByRole('option').count()};
    await p.keyboard.press('ArrowDown');await p.keyboard.press('Enter');result.selectAfterEnter={text:await select.innerText(),expanded:await select.getAttribute('aria-expanded')};
    await select.click();await p.keyboard.press('Escape');result.selectEscape={expanded:await select.getAttribute('aria-expanded'),focused:await select.evaluate(e=>e===document.activeElement)};
   }
   await s.context.close();
   for(const mode of ['empty','error','loading']){
    const t=await setup(browser,theme,1440,1000,mode);
    const r=mode==='empty'?'datasets':mode==='error'?'settings':'dashboard';
    await t.page.goto(`${BASE}/app/${r}`,{waitUntil:'domcontentloaded'});await t.page.waitForTimeout(mode==='loading'?350:600);
    result[mode]={...await capture(t.page,`${theme}-${mode}-${r}`),events:t.events};
    await t.context.close();
   }
   const l=await setup(browser,theme);await l.page.goto(`${BASE}/app/login`);
   await l.page.getByPlaceholder('请输入用户名').fill('fixture');await l.page.getByPlaceholder('请输入密码').fill('wrong');
   await l.page.getByRole('button',{name:'登 录'}).click();await l.page.getByText('用户名或密码错误').waitFor();
   result.loginError=await capture(l.page,`${theme}-login-error`);await l.context.close();
   report.interactions.push(result);
  }
 }finally{fs.writeFileSync(path.join(OUT,'results.json'),JSON.stringify(report,null,2));await browser.close();}
}
if(require.main===module) main();
module.exports={setup,capture,BASE,OUT,chromium};
