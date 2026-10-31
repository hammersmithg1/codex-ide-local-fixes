const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'recovery_host.js'), 'utf8'), context);

function setup(render) {
  const events = [], renders = [];
  let failures = 0, cancellations = 0;
  const recovery = context.__codexCreateRecovery({
    log:(event, fields)=>events.push({event, fields}),
    render:render ?? (async generation=>{renders.push(generation)}),
    failed:()=>failures++, cancelTimer:()=>cancellations++,
  });
  return {recovery, events, renders, failures:()=>failures, cancellations:()=>cancellations};
}

async function main() {
  const timeout = {elapsedMs:30000, receivedWebviewMessage:false};
  const one = setup();
  await one.recovery.timeout(timeout);
  assert.equal(one.renders.length, 1);
  await one.recovery.timeout(timeout);
  assert.equal(one.renders.length, 1, 'automatic retries are bounded');
  assert.equal(one.failures(), 1);
  await one.recovery.reload();
  await one.recovery.timeout(timeout);
  assert.equal(one.renders.length, 3, 'manual recovery starts a new bounded attempt');
  one.recovery.markReady();
  await one.recovery.timeout(timeout);
  assert.equal(one.failures(), 1, 'late timeout cannot replace a ready UI');
  one.recovery.dispose();
  await one.recovery.reload();
  assert.equal(one.renders.length, 3, 'disposed webview never restarts');

  let finish, count = 0;
  const pending = setup(async () => {count++;await new Promise(resolve=>{finish=resolve})});
  const first = pending.recovery.reload();
  const second = pending.recovery.reload();
  assert.equal(first, second, 'simultaneous retry clicks share one operation');
  await Promise.resolve();
  finish();
  await first;
  assert.equal(count,1);

  let allowRender;
  const stale = setup(async (_generation, isCurrent) => {
    await new Promise(resolve=>{allowRender=resolve});
    assert.equal(isCurrent(), false, 'async result cannot revive a disposed view');
  });
  const reload = stale.recovery.reload();
  await Promise.resolve();
  stale.recovery.dispose();
  allowRender();
  await reload;
  const broken = setup(async()=>{throw new TypeError('private source text')});
  await broken.recovery.reload();
  assert.equal(broken.failures(),1);
  assert.ok(!JSON.stringify(broken.events).includes('private source text'));

  const diagnostic = context.__codexLocalDiagnostic({stage:'resource_error', asset:'bundle-123.js',
    elapsedMs:15, message:'secret', url:'https://private.example?token=secret'});
  assert.deepEqual(JSON.parse(JSON.stringify(diagnostic)), {stage:'resource_error',elapsedMs:15,asset:'bundle-123.js'});
  assert.equal(context.__codexLocalDiagnostic({stage:'unknown'}),null);
  assert.ok(!JSON.stringify(context.__codexLocalDiagnostic({stage:'resource_error',asset:'https://secret'})).includes('secret'));
  const html=context.__codexRecoveryFallback('https://local/script.js?x="', 'https://safe');
  assert.ok(html.includes('&quot;'));
  assert.ok(html.includes('data-codex-recovery="retry"'));
  assert.ok(!html.includes('script-src \'unsafe-inline\''));

  // Bootstrap acquires the VS Code API once and passes that handle to the
  // unmodified official bus, even when the application bundle starts later.
  const messages=[], listeners={};
  let acquisitions=0;
  const api={postMessage:message=>messages.push(message)};
  const acquire=()=>{if(acquisitions++)throw Error('already acquired');return api};
  const window={acquireVsCodeApi:acquire, addEventListener:(type,fn)=>{listeners[type]=fn},setTimeout:()=>{}};
  const document={baseURI:'https://local/index.html',addEventListener:(type,fn)=>{listeners[type]=fn}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'..','bootstrap.js'),'utf8'),{
    window,document,performance:{now:()=>10,getEntriesByType:()=>[]},URL,
  });
  assert.equal(messages[0].stage,'bootstrap');
  assert.equal(window.acquireVsCodeApi(),api);
  assert.equal(acquisitions,1);
  assert.equal(window.acquireVsCodeApi,acquire);
  listeners.error({target:{tagName:'SCRIPT',src:'https://local/assets/broken.js?private=secret'}});
  assert.equal(messages.at(-1).asset,'broken.js');
  assert.ok(!JSON.stringify(messages).includes('secret'));
  console.log('Recovery and bootstrap checks passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1});
