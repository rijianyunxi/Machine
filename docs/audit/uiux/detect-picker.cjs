// Isolated UI regression: file picker, filename, keyboard, and both themes.
const assert = require('node:assert/strict');
const {setup,capture,BASE,OUT,chromium}=require('./audit.cjs');
const fs=require('node:fs'),path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.UI_AUDIT_BROWSER?{executablePath:process.env.UI_AUDIT_BROWSER}:{})});
 const passed=[];
 try {
  for(const theme of ['light','dark']) for(const width of [1440,390,320]) {
   const s=await setup(browser,theme,width,900),p=s.page;
   await p.goto(`${BASE}/app/detect`);
   const pick=p.getByRole('button',{name:'选择图片',exact:true});await pick.waitFor();
   assert.equal(await p.getByRole('button',{name:'开始检测'}).isDisabled(),true);
   await capture(p,`${theme}-${width}-picker-empty`);
   await pick.focus();const chooser=p.waitForEvent('filechooser');await p.keyboard.press('Enter');
   await (await chooser).setFiles({name:'车间安全检测_这是用于验证超长文件名不会撑破布局的图片.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j8E8AAAAASUVORK5CYII=','base64')});
   await p.getByRole('button',{name:'更换图片',exact:true}).waitFor();
   assert.equal(await p.getByRole('button',{name:'开始检测'}).isEnabled(),true);
   assert.match(await p.locator('#detect-file-status').innerText(),/车间安全检测/);
   assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth),width);
   await capture(p,`${theme}-${width}-picker-selected`);
   assert.equal(s.events.writes.length,0);assert.equal(s.events.errors.length,0);
   passed.push({theme,width,keyboardFileChooser:true,longFilename:true,noOverflow:true});
   await s.context.close();
  }
  fs.writeFileSync(path.join(OUT,'picker-regression.json'),JSON.stringify(passed,null,2));console.log(passed);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
