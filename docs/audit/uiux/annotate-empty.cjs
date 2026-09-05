// Synthetic API fixtures only; never writes dataset/annotation data.
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {setup,capture,BASE,OUT,chromium} = require('./audit.cjs');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.UI_AUDIT_BROWSER});
 const passed=[];
 try {
  for(const theme of ['light','dark']) for(const width of [1440,390,320]) {
   for(const state of ['empty','no-dataset','unlabeled','empty-split','error','loading']) {
    const s=await setup(browser,theme,width,900),p=s.page;
    let release;
    if(state==='no-dataset') await p.route('**/api/datasets',r=>r.fulfill({json:{datasets:[]}}));
    if(['empty','error','loading'].includes(state)) await p.route('**/api/datasets/workshop-safety/images',async r=>{
     if(state==='loading') await new Promise(resolve=>{release=resolve});
     await r.fulfill({status:state==='error'?500:200,json:state==='error'?{detail:'模拟网络故障'}:{images:[]}});
    });
    if(state==='unlabeled') await p.route('**/api/datasets/workshop-safety/labels/**',r=>r.fulfill({json:{boxes:[]}}));
    await p.goto(`${BASE}/app/annotate`);
    if(state==='empty-split') {
     await p.locator('.anno-stage-inner').waitFor();
     await p.getByRole('combobox',{name:'图片分区筛选'}).click();
     await p.getByRole('option',{name:'验证集',exact:true}).click();
    }
    const title={empty:'导入第一张图片，开始标注','no-dataset':'创建数据集，开始标注',unlabeled:'这张图片还没有标注','empty-split':'选择图片，开始标注',error:'暂时无法加载图片',loading:'正在准备标注画布'}[state];
    await p.getByRole('heading',{name:title,exact:true}).waitFor();
    assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth),width);
    assert.equal(await p.getByRole('button',{name:'YOLO 预标注',exact:true}).isDisabled(),state!=='unlabeled');
    assert.equal(await p.getByRole('button',{name:'保存 (Ctrl+S)',exact:true}).isDisabled(),state!=='unlabeled');
    if(['empty','no-dataset'].includes(state)) assert.equal(await p.locator('.anno-empty a').getAttribute('href'),'/app/datasets');
    if(state==='empty') {
     const grid=await p.locator('.snap-grid').boundingBox(),empty=await p.locator('.snap-grid .anno-empty').boundingBox();
     assert.ok(Math.abs(grid.width-empty.width)<2,'empty state spans both thumbnail columns');
    }
    await capture(p,`${theme}-${width}-annotate-${state}`);
    if(state==='empty-split') {
     await p.getByRole('button',{name:'查看全部分区'}).click();
     await p.locator('.anno-stage-inner').waitFor();
    }
    if(state==='error') {
     await p.unroute('**/api/datasets/workshop-safety/images');
     await p.getByRole('button',{name:'重新加载',exact:true}).click();
     await p.locator('.anno-stage-inner').waitFor();
    }
    if(state==='loading') {release(); await p.getByRole('heading',{name:'导入第一张图片，开始标注',exact:true}).waitFor();}
    assert.deepEqual(s.events.errors,[]);assert.equal(s.events.writes.length,0);
    passed.push({theme,width,state,noOverflow:true});
    await s.context.close();
   }
  }
  fs.writeFileSync(path.join(OUT,'annotate-empty-regression.json'),JSON.stringify(passed,null,2)); console.log(`${passed.length} cases passed`);
 } finally {await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
