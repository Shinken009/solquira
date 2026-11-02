// Loopback-only regression: geographic picking after user zoom must not reset
// the camera. Tile images below are declared repeated local fixtures, not a map
// data assessment. No environmental, model or device-location request is allowed.
import {createRequire} from 'node:module';
import {readFileSync,mkdirSync,writeFileSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const {chromium}=createRequire(import.meta.url)('playwright');
const target=process.argv[2]||'http://127.0.0.1:8054',origin=new URL(target).origin;
if(!['localhost','127.0.0.1'].includes(new URL(target).hostname))throw Error('Loopback QA only');
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const output=resolve(process.env.SOLQUIRA_QA_OUTPUT||'output/playwright/cesium-selection');mkdirSync(output,{recursive:true});
const raster=readFileSync(resolve(root,'artifacts/solquira/dist/public/cesium/Assets/Textures/NaturalEarthII/0/0/0.jpg'));
const browser=await chromium.launch({args:['--enable-webgl','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const checks=[],errors=[];let activePage;
try{
 for(const width of [390,1440]){
  const context=await browser.newContext({viewport:{width,height:1000},reducedMotion:'reduce'});
  const calls={forecast:0,history:0,model:0,gps:0},levels=[];
  await context.addInitScript(()=>Object.defineProperty(navigator,'geolocation',{value:{getCurrentPosition(){throw Error('Device GPS forbidden in camera test');}}}));
  await context.route('**/*',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname.endsWith('/studio/location')){calls.forecast++;return route.abort();}
   if(url.pathname.endsWith('/studio/history')){calls.history++;return route.abort();}
   if(url.pathname.endsWith('/studio/agent')||url.pathname.endsWith('/v1/field-brief')){calls.model++;return route.abort();}
   if(url.hostname==='tile.openstreetmap.org'){levels.push(Number(url.pathname.split('/')[1]));return route.fulfill({status:200,contentType:'image/jpeg',body:raster});}
   if(url.origin!==origin&&!['data:','blob:'].includes(url.protocol))return route.abort();
   if(url.pathname.endsWith('/studio/bootstrap')){const response=await route.fetch(),value=await response.json();assert(value.inference.mode==='disabled'&&!value.inference.available&&!value.geodata.enabled);return route.fulfill({response});}
   return route.continue();
  });
  const page=await context.newPage();activePage=page;page.setDefaultTimeout(25000);page.on('pageerror',e=>errors.push({width,message:e.message}));
  await page.goto(target,{waitUntil:'networkidle'});await page.getByTestId('studio-ready').waitFor();
  await page.getByTestId('geo-manual').locator('summary').click();
  const coordinates=async()=>({latitude:Number(await page.getByTestId('geo-lat').inputValue()),longitude:Number(await page.getByTestId('geo-lon').inputValue())});
  const setManual=async(latitude,longitude)=>{await page.getByTestId('geo-lat').fill(String(latitude));await page.getByTestId('geo-lon').fill(String(longitude));await page.getByTestId('geo-load-context').click();};
  await setManual(52.520008,13.404954);
  await page.waitForFunction(()=>document.querySelector('[data-testid="geo-map-state"]')?.getAttribute('data-state')==='ready');
  const canvas=page.locator('.qg-map canvas').first(),identity=await canvas.elementHandle();
  await page.getByTestId('geo-select-map').click();await canvas.scrollIntoViewIfNeeded();
  const box=await canvas.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
  for(let i=0;i<4;i++){await page.mouse.wheel(0,-500);await page.waitForTimeout(600);}
  await page.waitForTimeout(1000);
  const fineZoomLevel=Math.max(...levels);assert(fineZoomLevel>=12,'The browser must actually zoom before picking');
  await canvas.click({position:{x:box.width/2+25,y:box.height/2}});await page.waitForTimeout(600);const first=await coordinates();
  await canvas.click({position:{x:box.width/2+35,y:box.height/2}});await page.waitForTimeout(600);const second=await coordinates();
  const delta=Math.abs(first.longitude-second.longitude),initialOffset=Math.abs(first.longitude-13.404954);
  // Native wheel sensitivity differs between 2D and 3D. Compare equal-view
  // geometry instead of assuming a universal metres-per-pixel: a ten-pixel
  // refinement must remain smaller than the initial 25-pixel center offset.
  assert(initialOffset>1e-10&&delta<=initialOffset*1.25+1e-8,`A ten-pixel refinement must retain the user's scale (${delta} degrees vs initial 25-pixel offset ${initialOffset})`);
  await page.getByTestId('geo-confirm-point').click();
  assert.equal(await page.getByTestId('geo-selected-point').getAttribute('data-source'),'map');
  await page.screenshot({path:`${output}/refined-selection-${width}.png`,animations:'disabled'});
  // External/manual selection still centers the map. The center-pick resolves
  // that external point rather than the old European view.
  await setManual(-33.8688,151.2093);await page.getByTestId('geo-select-map').click();await canvas.scrollIntoViewIfNeeded();
  const newBox=await canvas.boundingBox();await canvas.click({position:{x:newBox.width/2,y:newBox.height/2}});await page.waitForTimeout(500);
  const external=await coordinates();assert(Math.abs(external.latitude+33.8688)<.001&&Math.abs(external.longitude-151.2093)<.001,'External coordinates must still recenter the map');
  for(const area of ['anticipation','guidance','evidence','territory'])await page.getByTestId(`area-tab-${area}`).click();
  assert(await identity.evaluate(el=>el.isConnected&&el===document.querySelector('.qg-map canvas')),'The same Cesium canvas must survive navigation');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  assert.deepEqual(calls,{forecast:0,history:0,model:0,gps:0});
  checks.push({width,scene:await page.getByTestId('geo-map-state').getAttribute('data-scene-mode'),fineZoomLevel,refinementDeltaDegrees:delta,initial25PixelOffsetDegrees:initialOffset,zoomPreserved:true,externalManualStillCenters:true,sameCesiumCanvas:true,noOverflow:true,...calls});
  await context.close();
 }
 assert.equal(errors.length,0,JSON.stringify(errors));const result={status:'PASS',target,scope:'Declared loopback camera/raster fixtures; no real environmental data, GPS, sources or inference.',checks,errors};writeFileSync(`${output}/checks.json`,JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
}catch(error){if(activePage&&!activePage.isClosed())await activePage.screenshot({path:`${output}/failure.png`,animations:'disabled'}).catch(()=>{});writeFileSync(`${output}/failure.json`,JSON.stringify({error:String(error),checks,errors},null,2));console.error(error);process.exitCode=1;}finally{await browser.close();}
