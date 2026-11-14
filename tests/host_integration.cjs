const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const preview=process.env.CODEX_TEST_EXTENSION_PREVIEW;
if(!preview){console.log('Set CODEX_TEST_EXTENSION_PREVIEW for actual bundle integration checks.');process.exit(0)}
const source=fs.readFileSync(preview,'utf8');
const start=source.indexOf('async initializeWebview(e,r,n,o){');
const end=source.indexOf('onPanelReady(e){',start);
const initializer=source.slice(start,end);
const watchdogName=initializer.match(/let s=new ([\w$]+)\(/)?.[1];
assert.ok(watchdogName,'known watchdog creation');
const watchdogStart=source.indexOf(watchdogName+'=class{');
const watchdogEnd=source.indexOf('};',watchdogStart)+1;
assert.ok(watchdogStart>=0 && watchdogEnd>watchdogStart,'known watchdog class boundaries');
const vscodeName=initializer.match(/let i=([\w$]+)\.Uri\.joinPath/)?.[1];
const schemaName=initializer.match(/let c=([\w$]+)\.safeParse/)?.[1];
const snapshotName=initializer.match(/\.\.\.([\w$]+)\("local","stdio"/)?.[1];
const htmlReader=initializer.match(/e\.html=await ([\w$]+)\(this\.extensionUri,e,this\.webviewMetaTags\(e\)\)/)?.[1];
const helpers=fs.readFileSync(require('node:path').join(__dirname,'..','recovery_host.js'),'utf8');
const timers=new Map(),events=[],logs=[],disposers=[];
let timerId=0;
const context={
  setTimeout:fn=>{const id=++timerId;timers.set(id,fn);return id},clearTimeout:id=>timers.delete(id),
  Date,Be:{Uri:{joinPath:(...args)=>args.join('/')},commands:{executeCommand:(...args)=>events.push(args)}},
  g4t:{safeParse:()=>({success:false})},Bx:()=>({type:'snapshot'}),
};
context[vscodeName]=context.Be;
context[schemaName]=context.g4t;
context[snapshotName]=context.Bx;
if(htmlReader)context[htmlReader]=async()=>'<head></head><body>healthy-ui</body>';
vm.createContext(context);
vm.runInContext(helpers+';var '+source.slice(watchdogStart,watchdogEnd)+';class Provider{'+source.slice(start,end)+'};globalThis.Provider=Provider;',context);
const provider=new context.Provider();
let html='',listener,sessions=0,sessionDisposals=0;
const webview={
  asWebviewUri:value=>({toString:()=>String(value)}),cspSource:'https://local',
  onDidReceiveMessage:fn=>{listener=fn;return{dispose(){}}},
  get html(){return html},set html(value){html=value},
};
const sidebar={webview};
Object.assign(provider,{
  extensionUri:'extension',extensionVersion:'test',subscriptions:[],logger:{warning:(...args)=>logs.push(args),info:(...args)=>logs.push(args)},
  registerClientCoordinationForWebview:()=>sessions++,disposeClientCoordinationForWebview:()=>sessionDisposals++,
  workerBusMessageHandler:{registerWebview(){}},chunkedMessageSender:{dispose(){}},
  getWebviewContent:async()=>'<head></head><body>healthy-ui</body>',
  webviewMetaTags:()=>'',
  sidebarViews:new Set([sidebar]),readySidebarViews:new Set(),sidebarView:sidebar,
  editorPanels:new Map(),findPanelByWebview:()=>undefined,
  pendingContextFiles:[],pendingNavigationRoutes:[],pendingSidebarMessages:[],
  getChatFontSettings:()=>({}),sendChatFontSettings(){},postMessageToWebview(){},
  sendCustomPrompts(){},sendPersistedAtomState(){},handleMessage(){},
  codexMcpConnection:{getAppServerVersion:()=>null,getRequestUserInputAutoResolutionSnapshot:()=>[]},
});
async function flush(){for(let i=0;i<8;i++)await Promise.resolve()}
async function timeout(){const [id,fn]=timers.entries().next().value;timers.delete(id);fn();await flush()}
async function main(){
  await provider.initializeWebview(webview,'sidebar',fn=>{disposers.push(fn);return{dispose(){}}});
  assert.equal(sessions,1);
  assert.equal(timers.size,1);
  listener({type:'codex-local-diagnostic',stage:'bootstrap',elapsedMs:1});
  assert.ok(logs.some(([title])=>title==='Local webview bootstrap'));
  await timeout();
  assert.equal(sessions,2);
  assert.equal(sessionDisposals,1);
  assert.ok(html.includes('codex-local-reload:1:'));
  assert.equal(timers.size,1);
  await timeout();
  assert.ok(html.includes('data-codex-recovery="retry"'));
  listener({type:'codex-local-recovery-action',action:'retry'});
  await flush();
  assert.ok(html.includes('healthy-ui'));
  listener({type:'ready'});
  assert.equal(timers.size,0);
  assert.equal(provider.sidebarWebviewReady,true);
  assert.ok(logs.some(([title,entry])=>title==='Local webview recovery'&&entry.safe.event==='ready'));
  for(const dispose of disposers)dispose();
  console.log('Actual patched initializeWebview passed: timeout → reload → fallback → manual retry → ready → disposal.');
}
main().catch(error=>{console.error(error);process.exitCode=1});
