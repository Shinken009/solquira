// Local-only UX verification. All environmental/GPS interactions are declared
// fixtures; no observations are persisted or imported and all model calls fail.
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import assert from 'node:assert/strict';
const { chromium } = createRequire(import.meta.url)('playwright');
const target = process.argv[2] || 'http://127.0.0.1:8047';
const origin = new URL(target).origin;
if (!['127.0.0.1', 'localhost'].includes(new URL(target).hostname)) throw Error('Loopback QA only');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const output = resolve(process.env.SOLQUIRA_QA_OUTPUT || 'output/playwright/territorial-journey');
mkdirSync(output, {recursive:true});
const generated = spawnSync(process.env.PYTHON || 'python', ['-c', `
import sys,os,json,socket
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
sys.path.insert(0,'artifacts/solquira/backend')
def deny(*a,**k): raise AssertionError('No network in fixture generator')
socket.socket.connect=deny
import studio_geo
today=datetime.now(timezone.utc).date()
source={'timezone':'GMT','utc_offset_seconds':0,'latitude':52.52,'longitude':13.405,
'daily_units':{'time':'iso8601','temperature_2m_max':'°C','temperature_2m_min':'°C','precipitation_sum':'mm','et0_fao_evapotranspiration':'mm'},
'daily':{'time':[(today+timedelta(days=i)).isoformat() for i in range(7)],'temperature_2m_max':[25.]*7,'temperature_2m_min':[12.]*7,'precipitation_sum':[2,3,0,4,0,1,1],'et0_fao_evapotranspiration':[3.2]*7}}
with patch.dict(os.environ,{'SOLQUIRA_STUDIO_GEODATA':'1'}),patch('studio_geo._fetch',return_value=studio_geo.parse_weather(source)):
 result=studio_geo.location_context({'latitude':52.52,'longitude':13.405})
result['context_ref']=None
print(json.dumps(result))
`], {cwd:root,encoding:'utf8',env:{...process.env,PYTHONUTF8:'1',SOLQUIRA_STUDIO_PROVIDER:'disabled'}});
if (generated.status !== 0) throw Error(`Offline fixture failed: ${generated.stderr}`);
const forecast = JSON.parse(generated.stdout);
const browser = await chromium.launch({args:['--enable-webgl','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const checks = [], errors = [];
let activePage;
try {
  for (const width of [1440,390,320]) {
    const context = await browser.newContext({viewport:{width,height:width===1440?1000:900},reducedMotion:'reduce'});
    await context.addInitScript(() => {
      window.__qaGpsRequests = 0;
      Object.defineProperty(navigator,'geolocation',{value:{getCurrentPosition(_success,error){
        window.__qaGpsRequests++;
        error({code:1,message:'Declared permission-denied fixture'});
      }}});
    });
    const calls = {forecast:0,history:0,model:0,blockedExternal:0};
    let providerDisabled = false;
    await context.route('**/*',async route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin && !['data:','blob:'].includes(url.protocol)) {calls.blockedExternal++;return route.abort();}
      if (url.pathname.endsWith('/studio/agent') || url.pathname.endsWith('/v1/field-brief')) {calls.model++;return route.abort();}
      if (url.pathname.endsWith('/studio/history')) {calls.history++;return route.abort();}
      if (url.pathname.endsWith('/studio/bootstrap')) {
        const response=await route.fetch(),value=await response.json();
        providerDisabled=value.inference?.mode==='disabled' && value.inference?.available===false;
        value.geodata={enabled:true};
        return route.fulfill({response,json:value});
      }
      if (url.pathname.endsWith('/studio/location')) {
        calls.forecast++;
        const value=structuredClone(forecast);
        value.location={...route.request().postDataJSON(),precision_degrees:.001};
        return route.fulfill({status:200,contentType:'application/json',json:value});
      }
      return route.continue();
    });
    const page=await context.newPage(); activePage=page; page.setDefaultTimeout(20000);
    page.on('pageerror',error=>errors.push({width,message:error.message}));
    page.on('console',message=>{
      if (message.type()==='error' && /same key|unique.*key|duplicate.*key/i.test(message.text()))
        errors.push({width,message:message.text(),kind:'react-key-collision'});
    });
    await page.goto(target,{waitUntil:'networkidle'});
    await page.getByTestId('studio-ready').waitFor();
    assert.equal(providerDisabled,true,'Local bootstrap must show inference disabled');
    assert.equal(await page.evaluate(()=>window.__qaGpsRequests),0,'No location permission requested on mounting');
    assert.equal(calls.forecast,0);
    await page.getByTestId('area-tab-guidance').click();
    await page.getByTestId('guided-no-point').waitFor();
    assert.equal(await page.getByTestId('guided-prepare').count(),0,'No situated orientation before a confirmed point');
    await page.getByTestId('guided-no-point').getByRole('button',{name:/Choose a place/}).click();
    await page.locator('.qs-studio[data-active-area="territory"]').waitFor();
    await page.getByTestId('geo-use-location').click();
    await page.getByRole('alert').filter({hasText:'Location was not shared'}).waitFor();
    assert.equal(calls.forecast,0,'Denied GPS must not select a fallback city');
    assert.match(await page.getByTestId('geo-selected-point').innerText(),/No point confirmed/);
    await page.getByTestId('geo-manual').locator('summary').click();
    await page.getByTestId('geo-lat').fill('52.520008');
    await page.getByTestId('geo-lon').fill('13.404954');
    await page.getByTestId('geo-load-context').click();
    await page.locator('[data-testid="geo-point-review"][data-confirmed="true"]').waitFor();
    await page.waitForFunction(()=>document.querySelector('[data-testid="geo-weather"]')?.getAttribute('data-state')==='ready');
    assert.equal(calls.forecast,1);
    await page.waitForFunction(()=>document.querySelector('[data-testid="geo-map-state"]')?.getAttribute('data-state')==='ready');
    const canvas=await page.locator('.qg-map canvas').first().elementHandle();
    assert(canvas,'Cesium must be rendered for the visual checks');
    await page.getByTestId('geo-section').scrollIntoViewIfNeeded();
    await page.screenshot({path:`${output}/territory-${width}.png`,fullPage:true,animations:'disabled'});
    await page.screenshot({path:`${output}/territory-viewport-${width}.png`,animations:'disabled'});
    await page.getByTestId('geo-next-guidance').click();
    const wizard=page.getByTestId('guided-exploration');
    await wizard.locator('input[name="guided-land-use"][value="restoration"]').check();
    await wizard.locator('input[name="guided-concern"][value="soil"]').check();
    await wizard.locator('input[name="guided-signal"][value="soil_unknown"]').check();
    await wizard.locator('input[name="guided-duration"][value="weeks"]').check();
    await wizard.locator('input[name="guided-barrier"][value="resources"]').check();
    assert.match(await wizard.innerText(),/6 of 6 choices/);
    await page.getByTestId('guided-prepare').click();
    const result=page.getByTestId('guided-result'); await result.waitFor();
    assert.match(await result.innerText(),/Keep the check short/);
    assert.match(await result.innerText(),/not a forecast or a diagnosis/);
    assert.equal(calls.forecast,1,'Answering questions must not query a weather source');
    assert.equal(calls.history,0);
    for (const area of ['territory','anticipation','evidence','guidance','territory','guidance','anticipation','guidance','territory','guidance']) {
      await page.getByTestId(`area-tab-${area}`).click();
      await page.locator(`.qs-studio[data-active-area="${area}"]`).waitFor();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`No overflow ${width}/${area}`);
    }
    await result.waitFor();
    assert.equal(await wizard.locator('input[name="guided-land-use"]:checked').inputValue(),'restoration');
    assert(await canvas.evaluate(el=>el.isConnected && el===document.querySelector('.qg-map canvas')),'Navigation must retain the Cesium canvas');
    await result.scrollIntoViewIfNeeded();
    await page.screenshot({path:`${output}/guided-result-${width}.png`,fullPage:true,animations:'disabled'});
    await page.screenshot({path:`${output}/guided-result-viewport-${width}.png`,animations:'disabled'});
    await page.getByTestId('guided-to-record').click();
    const note=await page.getByTestId('land-record-note').inputValue();
    assert.match(note,/Self-reported; not a field measurement/);
    assert.match(note,/I am not sure/);
    assert.equal(await page.getByTestId('land-record-cover').inputValue(),'','Guided choices must not fabricate a measured cover band');
    await page.getByTestId('area-tab-guidance').click();
    await wizard.getByRole('button',{name:'Change place',exact:true}).click();
    await page.getByTestId('geo-lat').fill('-1.286389');
    await page.getByTestId('area-tab-guidance').click();
    await page.getByTestId('guided-no-point').waitFor();
    assert.equal(await result.count(),0,'Changing point invalidates the previous situated orientation');
    assert.equal(await wizard.count(),1,'Exactly one wizard must remain after a point change; sibling keys must not collide');
    await page.getByTestId('guided-no-point').getByRole('button',{name:/Choose a place/}).click();
    await page.getByTestId('geo-lon').fill('36.817223');
    await page.getByTestId('geo-load-context').click();
    await page.waitForFunction(()=>document.querySelector('[data-testid="geo-weather"]')?.getAttribute('data-state')==='ready');
    await page.getByTestId('geo-next-guidance').click();
    assert.equal(await wizard.locator('input[name="guided-land-use"]:checked').count(),0,'The new place must start without old answers');
    assert.equal(await result.count(),0);
    await wizard.locator('input[name="guided-land-use"][value="natural"]').check();
    await wizard.locator('input[name="guided-concern"][value="explore"]').check();
    await wizard.locator('input[name="guided-signal"][value="explore_history"]').check();
    assert.equal(await wizard.locator('input[name="guided-duration"]').count(),0);
    assert.equal(await wizard.locator('input[name="guided-barrier"]').count(),0);
    assert.match(await wizard.innerText(),/4 of 4 choices/);
    await page.getByTestId('guided-prepare').click();
    await result.waitFor();
    await result.getByRole('button',{name:/Review climate & request history/}).click();
    await page.locator('.qs-studio[data-active-area="anticipation"]').waitFor();
    assert.equal(calls.history,0,'Exploration CTA only opens climate; history remains a separate explicit request');
    assert.equal(calls.forecast,2,'Only the two explicit confirmations may request a forecast');
    assert.equal(calls.model,0,'No brief or agent endpoint may be called');
    checks.push({width,noGpsOnMount:true,gpsDeniedWithoutFallback:true,internationalPoints:true,sixChoiceConcernRoute:true,fourChoiceExploreRoute:true,
      noFabricatedMeasurements:true,existingLandRecordReused:true,pointChangeResetsWizard:true,sameCesiumCanvas:true,tenAreaChangesPreserveWizard:true,
      noHorizontalOverflow:true,forecastRequests:calls.forecast,historyRequests:calls.history,modelRequests:calls.model,blockedExternalRequests:calls.blockedExternal});
    await context.close();
  }
  assert.equal(errors.length,0,JSON.stringify(errors));
  const result={status:'PASS',target,scope:'Declared loopback browser fixtures. No real GPS, field observations, external data, saved records, model requests or Kobo access.',checks,errors};
  writeFileSync(`${output}/checks.json`,JSON.stringify(result,null,2));
  console.log(JSON.stringify(result,null,2));
} catch(error) {
  if(activePage&&!activePage.isClosed()) await activePage.screenshot({path:`${output}/failure.png`,fullPage:true,animations:'disabled'}).catch(()=>{});
  writeFileSync(`${output}/failure.json`,JSON.stringify({error:String(error),checks,errors},null,2));
  console.error(error);process.exitCode=1;
} finally {await browser.close();}
