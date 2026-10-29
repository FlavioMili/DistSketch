// Dependency-free tests for the editor's export/controller flow.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const script = fs.readFileSync(path.join(__dirname, '../src/distribution_drawing/web/export.js'), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));

function editor(fetch, config) {
  const elements = new Map(), downloads = [], timers = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, {hidden: false, disabled: false, textContent: '', value: 0,
      removeAttribute(name) { delete this[name]; }});
    return elements.get(id);
  };
  const context = vm.createContext({
    Blob, console, window: {}, location: {protocol: 'http:'}, fetch,
    GENERATOR_TEMPLATE: 'CONFIG = __CONFIG__',
    URL: {createObjectURL(blob) {downloads.push(blob);return 'blob:test';},revokeObjectURL() {}},
    setTimeout(callback, delay) {if(delay<1000)timers.push(callback);},
    document: {getElementById: element, createElement() {return {click() {}};}},
    config,
  });
  vm.runInContext(script, context);
  return {context, element, downloads, timers,
    init: () => vm.runInContext('DatasetExport.init(() => config, () => {})', context)};
}
function response(body, status=200) {return {ok:status<400, status, json:async()=>body};}
const config = {counts:[7,13], min:0, max:255, format:'txt', order:'shuffled'};

test('direct generation polls progress, then exposes an existing file without a giant browser blob', async () => {
  let done=false, payload;
  const app=editor(async (url, options) => {
    if(url==='/api/session')return response({token:'session-token'});
    if(url==='/api/jobs'&&!options)return response({jobs:[]});
    const job={id:'job-id',status:done?'complete':'generating',count:20,bytes:80,progress:.5,elapsed:1.1,path:'/outputs/job/data.txt',download:'/api/jobs/job-id/file'};
    if(url==='/api/jobs'){
      assert.equal(options.headers['X-Distribution-Token'],'session-token');
      payload=JSON.parse(options.body);return response(job,201);
    }
    return response(job);
  },config);
  await app.init();
  await app.element('download').onclick();await settle();
  assert.deepEqual(payload.config,config);
  assert.equal(app.element('download').disabled,true);
  assert.equal(app.element('jobProgress').value,.5);
  assert.equal(app.downloads.length,0);
  done=true;await app.timers.shift()();await settle();
  assert.equal(app.element('download').disabled,false);
  assert.equal(app.element('jobTitle').textContent,'Last generated dataset');
  assert.equal(app.element('downloadResult').href,'/api/jobs/job-id/file');
  assert.equal(app.element('jobPath').textContent,'/outputs/job/data.txt');
});

test('cancel button calls the token-protected endpoint and returns the editor to idle', async () => {
  let cancelled=false;
  const app=editor(async (url,options)=>{
    if(url==='/api/session')return response({token:'token'});
    if(url==='/api/jobs'&&!options)return response({jobs:[]});
    if(url.endsWith('/cancel')){
      assert.equal(options.method,'POST');cancelled=true;
    }
    return response({id:'job-id',status:cancelled?'cancelled':'generating',count:20,bytes:80,progress:0,elapsed:0});
  },config);
  await app.init();await app.element('download').onclick();await settle();
  await app.element('cancelJob').onclick();
  assert.equal(cancelled,true);
  assert.equal(app.element('cancelJob').hidden,true);
  assert.equal(app.element('download').disabled,false);
  assert.equal(app.element('jobTitle').textContent,'Generation cancelled');
});

test('static mode exports a tiny generator for two billion values instead of allocating them', async () => {
  const app=editor(async()=>response({error:'not found'},404),{...config,counts:[2000000000]});
  await app.init();await app.element('download').onclick();
  assert.equal(app.element('engineBadge').textContent,'Browser mode');
  assert.equal(app.downloads.length,1);
  const exported=await app.downloads[0].text();
  assert.ok(exported.includes('2000000000'));
  assert.ok(exported.length<1000);
});

test('saved drawing is JSON and generation errors restore the primary action', async () => {
  const app=editor(async(url,options)=>{
    if(url==='/api/session')return response({token:'token'});
    if(!options)return response({jobs:[]});
    return response({error:'Not enough disk space.'},400);
  },config);
  await app.init();app.element('saveDrawing').onclick();
  assert.deepEqual(JSON.parse(await app.downloads[0].text()),config);
  await app.element('download').onclick();
  assert.equal(app.element('download').disabled,false);
  assert.equal(app.element('status').textContent,'Not enough disk space.');
});

test('sand display stays flat for uniform data when display columns cover unequal bin counts', () => {
  const lines=[];
  const draw = new Proxy({}, {get:(_,name)=>name==='lineTo'?((x,y)=>lines.push([x,y])):(()=>{})});
  const canvas=()=>({getContext:()=>draw,getBoundingClientRect:()=>({width:900,height:600}),addEventListener(){}});
  const plot=canvas(),elements=new Map();
  const context=vm.createContext({
    console,
    document:{getElementById(id){
      if(id==='plot')return plot;
      if(!elements.has(id))elements.set(id,{value:'0',classList:{toggle(){}},addEventListener(){},setAttribute(){}});
      return elements.get(id);
    },createElement:canvas},
    window:{devicePixelRatio:1,addEventListener(){}},
    DatasetExport:{init(){}},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/distribution_drawing/web/app.js'),'utf8'),context);
  lines.length=0;
  vm.runInContext("mode='sand';values.fill(1);renderBase()",context);
  // Four grid lines precede the 180 plotted density samples.
  const heights=lines.slice(4,184).map(point=>point[1]);
  assert.equal(heights.length,180);
  assert.equal(new Set(heights).size,1);
});


test('summary shows output values independently of the 4096-point drawing resolution', () => {
  const noop = () => {};
  const ctx = new Proxy({}, {get: () => noop});
  const elements = new Map();
  const canvas = () => ({getContext: () => ctx, getBoundingClientRect: () => ({width:900,height:600}), addEventListener:noop});
  const plot = canvas();
  const context = vm.createContext({document:{getElementById(id){
    if(id==='plot')return plot;
    if(!elements.has(id))elements.set(id,{value:'10000',classList:{toggle:noop},addEventListener:noop,setAttribute:noop});
    return elements.get(id);
  },createElement:canvas},window:{devicePixelRatio:1,addEventListener:noop},DatasetExport:{init:noop}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/distribution_drawing/web/app.js'),'utf8'),context);
  vm.runInContext('values=new Float64Array(4096).fill(1);updateSummary()',context);
  assert.equal(elements.get('datasetSummary').textContent,Number(10000).toLocaleString()+' values');
  elements.get('total').value='2000000000';
  vm.runInContext('updateSummary()',context);
  assert.equal(elements.get('datasetSummary').textContent,Number(2000000000).toLocaleString()+' values');
});


test('stream worker preserves counts and sorted/shuffled multisets in bounded chunks', () => {
  const workerScript = fs.readFileSync(path.join(__dirname, '../src/distribution_drawing/web/stream-worker.js'), 'utf8');
  function generate(order, format, min = 0, max = 255) {
    const batches = [];
    const context = vm.createContext({TextEncoder, BigInt, ArrayBuffer, DataView, Uint32Array, Math,
      crypto:{getRandomValues(a){a.set([123,456,789]);}}, postMessage(message){batches.push(message);}});
    vm.runInContext(workerScript, context);
    context.onmessage({data:{config:{counts:[15000,30000], min, max, format, order}}});
    while (batches.at(-1).position < 45000) context.onmessage({data:{}});
    assert.equal(batches.length, 2);
    if (format === 'binary') return batches.flatMap(b => {
      const v = new DataView(b.buffer), stride = min >= 0 && max <= 4294967295 ? 4 : 8;
      return Array.from({length:b.buffer.byteLength / stride}, (_,i) => stride === 4 ? v.getUint32(i * stride,true) : Number(v.getBigInt64(i * stride,true)));
    });
    return batches.flatMap(b => new TextDecoder().decode(b.buffer).trim().split('\n').map(Number));
  }
  for (const [format, min, max] of [['txt',0,255], ['binary',-500,500]]) {
    const sorted = generate('sorted',format,min,max), shuffled = generate('shuffled',format,min,max);
    assert.equal(sorted.length,45000);
    assert.deepEqual([...shuffled].sort((a,b)=>a-b),sorted);
    assert.equal(sorted.filter(x => x < min + Math.floor((max-min+1)/2)).length,15000);
  }
});
