// Real local API and forecast; no paid model calls, no external browser origins.
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
const { chromium }=createRequire(import.meta.url)('playwright');
const target='http://127.0.0.1:8036',output=resolve(process.env.SOLQUIRA_QA_OUTPUT || 'output/field-agent-integration-2026-10-25');
mkdirSync(output,{recursive:true});
const browser=await chromium.launch(), checks=[],errors=[];
try {
  for(const width of [1440,390,320]){
    const context=await browser.newContext({viewport:{width,height:1000},reducedMotion:'reduce',acceptDownloads:true});
    await context.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(url.origin!==target&&!['data:','blob:'].includes(url.protocol)){await route.abort();return;}
      await route.continue();
    });
    const page=await context.newPage();page.setDefaultTimeout(20000);page.on('pageerror',e=>errors.push(e.message));
    let locationCalls=0,agentCalls=0;
    page.on('request',r=>{if(r.url().endsWith('/studio/location'))locationCalls++;if(r.url().endsWith('/studio/agent'))agentCalls++;});
    await page.goto(target,{waitUntil:'networkidle'});await page.getByTestId('studio-ready').waitFor();
    if(locationCalls||agentCalls)throw Error('Data or model requested automatically');
    await page.getByRole('button',{name:'Mediterranean farmland',exact:true}).click();
    await page.getByTestId('geo-load-context').click();await page.getByTestId('geo-attach').waitFor();
    if(agentCalls)throw Error('Loading a forecast called the agent');
    await page.getByTestId('geo-attach').click();await page.getByTestId('attached-context').waitFor();
    if(agentCalls)throw Error('Attaching a forecast called the agent');
    await page.getByTestId('agent-query').fill('What should I check about soil cover?');
    const responsePromise=page.waitForResponse(r=>r.url().endsWith('/studio/agent'));
    await page.getByTestId('agent-submit').click();const response=await responsePromise;
    if(!response.ok())throw Error(`Agent returned ${response.status()}`);
    const data=await response.json(),brief=data.report.field_brief;
    if(data.run.inference_mode!=='local_fallback'||data.run.model!==null)throw Error('Unexpected model activation');
    if(!brief||brief.location.latitude!==37.1||brief.location.longitude!==-4.6||brief.focus!=='resilience')throw Error('Wrong context/focus');
    if(!data.run.trace.some(t=>t.tool==='read_landscape_context')||data.run.trace.length!==6)throw Error('Actual geographic tool missing');
    await page.getByTestId('field-brief').waitFor();
    await page.locator('.qf-actions input').first().check();
    if(!(await page.locator('.qf-checklist-heading').innerText()).includes('1 / 3'))throw Error('Checklist state failed');
    await page.getByRole('button',{name:'Open supporting soil for step 1',exact:true}).click();
    if(await page.locator('#qs-tab-soil').getAttribute('aria-selected')!=='true')throw Error('Soil action did not open the soil review');
    await page.getByRole('button',{name:'Open supporting weather for step 2',exact:true}).click();
    if(await page.evaluate(()=>document.activeElement?.textContent)!=='The next seven days')throw Error('Weather action did not focus its evidence');
    await page.getByRole('button',{name:'Open supporting question for step 3',exact:true}).click();
    if(!(await page.getByTestId('agent-query').inputValue()).includes('field inputs'))throw Error('Question action did not prepare a useful follow-up');
    const downloadPromise=page.waitForEvent('download');await page.getByRole('button',{name:'Save brief',exact:true}).click();
    const download=await downloadPromise;await download.saveAs(resolve(output,`brief-${width}.txt`));
    await page.getByTestId('field-brief').scrollIntoViewIfNeeded();
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
    if(overflow)throw Error(`Overflow at ${width}`);
    await page.screenshot({path:resolve(output,`field-brief-${width}.png`),fullPage:false});
    await page.getByRole('button',{name:'Remove',exact:true}).click();
    if(await page.getByTestId('field-brief').count())throw Error('Removing attachment left an old brief');
    await page.getByTestId('geo-attach').click();
    // Fault injection: wrong source link must be rejected before rendering.
    await page.route('**/studio/agent',async route=>{const result=structuredClone(data);result.report.field_brief.sources[0].url='https://untrusted.invalid/';await route.fulfill({json:result});});
    await page.getByTestId('agent-submit').click();
    await page.getByRole('alert').filter({hasText:'field brief could not be verified'}).waitFor();
    if(await page.getByTestId('field-brief').count())throw Error('Malformed brief accepted');
    await page.unroute('**/studio/agent');
    // Keep a real server response in flight, then change the selected point.
    let release;const gate=new Promise(r=>{release=r;});let intercepted;const seen=new Promise(r=>{intercepted=r;});
    await page.route('**/studio/agent',async route=>{await route.fetch().then(async result=>{intercepted();await gate;try{await route.fulfill({response:result});}catch{}});});
    await page.getByTestId('agent-submit').click();await seen;
    await page.getByTestId('geo-lat').fill('37.2');release();
    await page.waitForTimeout(200);
    if(await page.getByTestId('attached-context').count()||await page.getByTestId('field-brief').count())throw Error('Coordinate edit retained stale context');
    await page.unroute('**/studio/agent');
    checks.push({width,forecast:'real Open-Meteo via local API',tools:data.run.trace.length,modelCalls:0,explicitAttach:true,checklist:true,actionNavigation:true,download:true,malformedRejected:true,staleDiscarded:true,overflow});
    await context.close();
  }
  if(errors.length)throw Error(errors.join('\n'));
  writeFileSync(resolve(output,'browser.json'),JSON.stringify({status:'PASS',checks,pageErrors:errors},null,2));
  console.log(JSON.stringify({status:'PASS',checks},null,2));
}catch(error){writeFileSync(resolve(output,'browser.json'),JSON.stringify({status:'FAIL',error:String(error),checks,pageErrors:errors},null,2));throw error;}
finally{await browser.close();}
