// Supplementary interaction/viewport checks; shares the same isolated fixtures.
const {setup,capture,BASE,OUT,chromium}=require('./audit.cjs');
const fs=require('node:fs');const path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.UI_AUDIT_BROWSER?{executablePath:process.env.UI_AUDIT_BROWSER}:{})});
 const results=[];
 try{
  for(const theme of ['light','dark']){
   for(const width of [1440,768,390]){
    const s=await setup(browser,theme,width,width===390?844:1000),p=s.page;
    const r={theme,width};results.push(r);
    await p.goto(`${BASE}/app/rules`);await p.getByRole('button',{name:'新建规则',exact:true}).click();
    await p.getByRole('dialog').waitFor();
    r.graph=await capture(p,`${theme}-${width}-graph-editor`);
    r.graphControls=await p.getByRole('dialog').evaluate(el=>[...el.querySelectorAll('button')].map(e=>({text:e.innerText,title:e.title,aria:e.getAttribute('aria-label'),cls:e.className,color:getComputedStyle(e).color,bg:getComputedStyle(e).backgroundColor,image:getComputedStyle(e).backgroundImage,width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height})))
    await p.keyboard.press('Escape');
    await p.goto(`${BASE}/app/cameras`);await p.waitForTimeout(200);
    r.nav=await p.locator('.nav').ariaSnapshot();
    r.table=await p.locator('.table-wrap').first().evaluate(e=>{
      const t=e.querySelector('table'),b=e.querySelector('button');const before=b.getBoundingClientRect().right;e.scrollLeft=e.scrollWidth;
      return {client:e.clientWidth,scroll:e.scrollWidth,overflow:getComputedStyle(e).overflowX,scrollLeft:e.scrollLeft,buttonRightBefore:before,buttonRightAfter:b.getBoundingClientRect().right,tableWidth:t.getBoundingClientRect().width};});
    await capture(p,`${theme}-${width}-cameras-scrolled`);
    await p.getByRole('button',{name:'新增监控',exact:true}).click();r.cameraModal=await capture(p,`${theme}-${width}-camera-form`);await p.keyboard.press('Escape');
    await p.goto(`${BASE}/app/settings`);await p.waitForTimeout(200);
    await p.getByRole('tab',{name:/大模型/}).click();
    r.llm=await capture(p,`${theme}-${width}-settings-llm`);
    r.events=s.events;await s.context.close();
   }
   const narrow=await setup(browser,theme,320,740);await narrow.page.goto(`${BASE}/app/login`);results.push({theme,width:320,login:await capture(narrow.page,`${theme}-320-login`)});await narrow.context.close();
   const s=await setup(browser,theme),p=s.page;
   await p.goto(`${BASE}/app/cameras`);const button=p.getByRole('button',{name:'新增监控',exact:true});
   const style=()=>button.evaluate(e=>{const s=getComputedStyle(e);return {bg:s.backgroundColor,image:s.backgroundImage,color:s.color,filter:s.filter,outline:s.outline,offset:s.outlineOffset}});
   const state={theme,states:{normal:await style()}};results.push(state);
   await button.hover();await p.waitForTimeout(200);state.states.hover=await style();
   await p.mouse.move(0,0);await p.keyboard.press('Tab');await button.focus();state.states.focus=await style();await capture(p,`${theme}-button-focus`);
   await p.goto(`${BASE}/app/train`);await p.waitForTimeout(200);
   state.disabled=await p.locator('button:disabled').evaluateAll(es=>es.map(e=>({text:e.innerText,disabled:e.disabled,opacity:getComputedStyle(e).opacity})));
   await s.context.close();
  }
 }finally{fs.writeFileSync(path.join(OUT,'deep-checks.json'),JSON.stringify(results,null,2));await browser.close();}
})();
