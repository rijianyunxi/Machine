// Real SPA with intercepted API: no model files are sent to the backend.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {setup, capture, BASE, OUT, chromium} = require('./audit.cjs');
(async () => {
 const browser = await chromium.launch({headless: true, ...(process.env.UI_AUDIT_BROWSER ? {executablePath: process.env.UI_AUDIT_BROWSER} : {})});
 const passed = [];
 try {
  for (const theme of ['light', 'dark']) for (const width of [1440, 768, 390, 320]) {
   const s = await setup(browser, theme, width, 900), p = s.page;
   await p.goto(`${BASE}/app/models`);
   const pick = p.getByRole('button', {name: '选择模型', exact: true});
   const upload = p.getByRole('button', {name: '上传模型', exact: true});
   await pick.waitFor();
   assert.equal(await upload.isDisabled(), true);
   await capture(p, `${theme}-${width}-model-empty`);
   await pick.scrollIntoViewIfNeeded();
   await pick.focus();
   const chooser = p.waitForEvent('filechooser');
   await pick.press('Enter');
   const name = '车间安全检测_用于验证超长文件名不会撑破上传区域布局的模型权重文件.pt';
   await (await chooser).setFiles({name, mimeType: 'application/octet-stream', buffer: Buffer.from('UI fixture only')});
   const replace = p.getByRole('button', {name: '更换模型', exact: true});
   await replace.waitFor();
   assert.equal(await upload.isEnabled(), true);
   assert.equal(await p.locator('#model-file-status').innerText(), name);
   assert.equal(await p.locator('#model-file-status').getAttribute('title'), name);
   assert.equal(await p.evaluate(() => document.documentElement.scrollWidth), width);
   if (width <= 390) assert.ok((await replace.boundingBox()).height >= 44);
   await capture(p, `${theme}-${width}-model-selected`);
   // Hold the mocked upload to verify pending, failure/retry, and success/reset states.
   let finish;
   await p.route('**/api/models/files', async route => {
    const success = await new Promise(resolve => { finish = resolve; });
    await route.fulfill({status: success ? 200 : 500, json: success ? {ok:true} : {detail:'模拟上传失败'}});
   });
   await upload.click();
   await p.getByRole('button', {name:'上传中…', exact:true}).waitFor();
   assert.equal(await replace.isDisabled(), true);
   await capture(p, `${theme}-${width}-model-uploading`);
   assert.equal(typeof finish, 'function');
   finish(false);
   await p.getByText('模拟上传失败', {exact:true}).waitFor();
   assert.equal(await upload.isEnabled(), true);
   assert.equal(await p.locator('#model-file-status').innerText(), name);
   await upload.click();
   await p.getByRole('button', {name:'上传中…', exact:true}).waitFor();
   finish(true);
   await pick.waitFor();
   assert.equal(await upload.isDisabled(), true);
   assert.equal(await p.locator('input[type=file]').inputValue(), '');
   assert.equal(s.events.writes.length, 0);
   assert.deepEqual(s.events.errors, []);
   passed.push({theme,width,keyboardFileChooser:true,longFilename:true,noOverflow:true,pending:true,failureRetry:true,successReset:true});
   await s.context.close();
  }
  fs.writeFileSync(path.join(OUT, 'model-picker-regression.json'), JSON.stringify(passed, null, 2));
  console.log(passed);
 } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exitCode = 1;});
