import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
const {chromium}=createRequire(import.meta.url)('playwright');
const target='http://127.0.0.1:8036', output=resolve(process.env.SOLQUIRA_QA_OUTPUT || 'output/geospatial-integration-2026-10-25');
mkdirSync(output,{recursive:true});
const browser=await chromium.launch({args:['--enable-webgl','--use-angle=swiftshader','--enable-unsafe-swiftshader']}), checks=[],errors=[],unexpected=[],nasaUrls=[];
let activePage;
try {
  for(const width of [1440,390,320]) {
    const context=await browser.newContext({viewport:{width,height:1000},reducedMotion:'reduce'});
    let allowNasa=false, modelCalls=0,locationCalls=0,tiles=0,localTiles=0;
    await context.route('**/*',async route=>{
      const u=new URL(route.request().url());
      if(u.pathname.endsWith('/studio/agent')){modelCalls++;await route.abort();return;}
      if(u.origin!==target&&!['data:','blob:'].includes(u.protocol)&&!(allowNasa&&u.origin==='https://gibs.earthdata.nasa.gov')){unexpected.push(u.origin);await route.abort();return;}
      if(u.pathname.endsWith('/studio/location'))locationCalls++;
      await route.continue();
    });
    const page=await context.newPage();activePage=page;page.setDefaultTimeout(25000);page.on('pageerror',e=>errors.push(e.message));
    page.on('response',r=>{if(r.status()===200&&r.url().includes('gibs.earthdata.nasa.gov')&&r.url().endsWith('.jpg')){tiles++;nasaUrls.push(r.url());}if(r.status()===200&&r.url().includes('NaturalEarthII')&&r.url().endsWith('.jpg'))localTiles++;});
    await page.goto(target,{waitUntil:'networkidle'});await page.getByTestId('studio-ready').waitFor();
    if(locationCalls)throw new Error('Location data was sent without user action');
    if(await page.locator('.qg-map canvas').count())throw new Error('Cesium was loaded before opening the landscape');
    await page.getByRole('button',{name:'Mediterranean farmland',exact:true}).click();
    await page.getByTestId('geo-load-context').click();
    await page.waitForFunction(()=>['ready','cached'].includes(document.querySelector('[data-testid="geo-weather"]')?.getAttribute('data-state')));
    if(await page.locator('.qg-chart-day').count()!==7)throw new Error('Expected seven real forecast days');
    await page.getByTestId('geo-open-map').click();
    await page.waitForFunction(()=>['ready','unavailable'].includes(document.querySelector('[data-testid="geo-map-state"]')?.getAttribute('data-state')));
    const state=await page.getByTestId('geo-map-state').getAttribute('data-state');
    if(state!=='ready')throw new Error('A real WebGL Cesium viewer was not created');
    await page.getByRole('button',{name:'2D view',exact:true}).click();
    await page.getByRole('button',{name:'3D view',exact:true}).click();
    const centerTile=page.waitForResponse(response=>{
      const match=response.url().match(/GoogleMapsCompatible_Level9\/(\d+)\/(\d+)\/(\d+)\.jpg/);
      if(!match||response.status()!==200)return false;
      const [z,y,x]=match.slice(1).map(Number),n=2**z;
      const expectedX=Math.floor((-4.6+180)/360*n),expectedY=Math.floor((1-Math.asinh(Math.tan(37.1*Math.PI/180))/Math.PI)/2*n);
      // Render-on-demand selects its own LOD. A regional tile is sufficient to
      // verify grid alignment; forcing z>=5 would fail valid mobile/low-GPU views.
      return z>=2&&x===expectedX&&y===expectedY;
    },{timeout:30000});
    allowNasa=true;await page.getByTestId('geo-nasa-toggle').check();
    await page.locator('.qg-source-note').filter({hasText:'Imagery received'}).waitFor({timeout:30000});
    await centerTile;
    await page.waitForLoadState('networkidle',{timeout:30000});
    if(!tiles||!localTiles)throw new Error('Actual local and NASA imagery tiles were not received');
    if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw new Error(`Geo overflow at ${width}`);
    await page.getByTestId('geo-section').screenshot({path:`${output}/landscape-${width}.png`,animations:'disabled'});
    await page.getByTestId('geo-lat').fill('0.5');
    if(await page.locator('.qg-chart-day').count())throw new Error('Forecast stayed visible after coordinate edits');
    if(await page.getByTestId('geo-nasa-toggle').isChecked())throw new Error('Old imagery context remained enabled');
    if(modelCalls)throw new Error('Geographic workflow made a model request');
    checks.push({width,forecastDays:7,viewer:'Cesium WebGL',nasaTilesReceived:tiles,localTilesReceived:localTiles,mercatorCenterTileVerified:true,locationCalls,modelCalls,overflow:false,staleForecastCleared:true});
    await context.close();
  }
  const fallback=await browser.newContext({viewport:{width:390,height:900},reducedMotion:'reduce'});
  await fallback.addInitScript(()=>{
    const original=HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext=function(kind,...args){return String(kind).includes('webgl')?null:original.call(this,kind,...args);};
  });
  await fallback.route('**/*',route=>new URL(route.request().url()).origin===target?route.continue():route.abort());
  const fallbackPage=await fallback.newPage();fallbackPage.on('pageerror',e=>errors.push(e.message));
  await fallbackPage.goto(target,{waitUntil:'networkidle'});await fallbackPage.getByTestId('studio-ready').waitFor();
  await fallbackPage.getByTestId('geo-open-map').click();
  await fallbackPage.waitForFunction(()=>document.querySelector('[data-testid="geo-map-state"]')?.getAttribute('data-state')==='unavailable');
  await fallbackPage.getByRole('button',{name:'Mediterranean farmland',exact:true}).click();
  await fallbackPage.getByTestId('geo-load-context').click();
  await fallbackPage.waitForFunction(()=>['ready','cached'].includes(document.querySelector('[data-testid="geo-weather"]')?.getAttribute('data-state')));
  if(await fallbackPage.locator('.qg-chart-day').count()!==7)throw new Error('Weather is unusable without WebGL');
  await fallbackPage.getByTestId('geo-section').screenshot({path:`${output}/no-webgl-390.png`,animations:'disabled'});
  // Labelled browser-only fault injection: reject impossible provider values.
  await fallbackPage.route('**/studio/location',async route=>{
    const upstream=await route.fetch();const body=await upstream.json();body.weather.days[0].precipitation_mm=-1;
    await route.fulfill({response:upstream,json:body});
  },{times:1});
  await fallbackPage.getByTestId('geo-load-context').click();
  await fallbackPage.getByRole('alert').filter({hasText:'forecast value'}).waitFor();
  if(await fallbackPage.locator('.qg-chart-day').count())throw new Error('Invalid source data was accepted');
  checks.push({case:'WebGL unavailable',forecastUsable:true,invalidForecastRejected:true,scope:'Browser-only fault injection for invalid forecast'});
  await fallback.close();
  const staticContext=await browser.newContext({viewport:{width:390,height:900},reducedMotion:'reduce'});
  let staticApiCalls=0;
  await staticContext.addInitScript(()=>Object.defineProperty(navigator,'geolocation',{value:{getCurrentPosition:callback=>{window.__testGpsCallback=callback;}}}));
  await staticContext.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.pathname.includes('/studio/'))staticApiCalls++;
    if(url.origin!=='http://127.0.0.1:8037'&&!['blob:','data:'].includes(url.protocol)){unexpected.push(url.origin);await route.abort();return;}
    await route.continue();
  });
  const staticPage=await staticContext.newPage();staticPage.on('pageerror',e=>errors.push(e.message));
  await staticPage.goto('http://127.0.0.1:8037',{waitUntil:'networkidle'});await staticPage.getByTestId('studio-ready').waitFor();
  await staticPage.getByRole('button',{name:'Use my location',exact:true}).click();
  await staticPage.getByTestId('geo-lat').fill('12.3');
  await staticPage.evaluate(()=>window.__testGpsCallback({coords:{latitude:9,longitude:8}}));
  if(await staticPage.getByTestId('geo-lat').inputValue()!=='12.3')throw new Error('Late device-location callback overwrote manual edits');
  await staticPage.getByTestId('geo-load-context').click();await staticPage.getByTestId('geo-open-map').click();
  await staticPage.waitForFunction(()=>document.querySelector('[data-testid="geo-map-state"]')?.getAttribute('data-state')==='ready');
  if(staticApiCalls||!await staticPage.getByTestId('geo-nasa-toggle').isDisabled()||await staticPage.locator('.qg-chart-day').count())throw new Error('Static map mode invented or fetched remote context');
  checks.push({case:'Static local globe',realCesiumViewer:true,studioApiCalls:0,remoteImageryDisabled:true,lateGpsCallbackIgnored:true,scope:'Synthetic deferred device-location callback, not real device capture'});
  await staticContext.close();
  if(errors.length||unexpected.length)throw new Error(JSON.stringify({errors,unexpected}));
  const result={status:'PASS',checks,pageErrors:errors,unexpectedOrigins:unexpected};
  writeFileSync(`${output}/browser.json`,JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
}catch(error){if(activePage&&!activePage.isClosed())await activePage.getByTestId('geo-section').screenshot({path:`${output}/failure.png`}).catch(()=>{});console.error(error);writeFileSync(`${output}/browser-failure.json`,JSON.stringify({error:String(error),errors,unexpected,nasaUrls},null,2));process.exitCode=1;}
finally{await browser.close();}
