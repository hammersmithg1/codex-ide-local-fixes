'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const vm = require('node:vm');
const updater = require('../local-updater.cjs');
const transport = require('../github-transport.cjs');

const payload = Buffer.from('PK mock verified extension package');
function fixture(target = 'darwin-arm64') {
  const tag = `codex-26.2.10-${target}`;
  const base = `https://github.com/hammersmithg1/codex-ide-local-fixes/releases/download/${tag}/`;
  const data = {schema:1,platform:target,version:'26.2.10001',upstreamVersion:'26.2.10',revision:1,
    assetName:`codex-local-fixes-26.2.10-${target}.vsix`,size:payload.length,
    sha256:crypto.createHash('sha256').update(payload).digest('hex')};
  const release = {tag_name:tag,draft:false,prerelease:false,assets:[
    {name:`codex-update-${target}.json`,browser_download_url:base+`codex-update-${target}.json`},
    {name:data.assetName,browser_download_url:base+data.assetName,size:data.size,digest:'sha256:'+data.sha256},
  ]};
  return {data,release};
}

async function harness(choices = [], options = {}) {
  const root = options.root || await fs.mkdtemp(path.join(os.tmpdir(),'codex-updater-test-'));
  const f = options.fixture || fixture(options.target);
  const context = {extension:{packageJSON:{version:options.version || '26.2.10'}},
    globalStorageUri:{fsPath:root,scheme:options.scheme || 'file'}};
  const calls = {json:0,downloads:0,commands:[],info:[],warnings:[],errors:[],logs:[]};
  const network = {
    async json(url) {calls.json++; if(options.offline) throw Error('network_error'); return url.includes('api.github.com')?[f.release]:f.data;},
    async download(url,file,size,signal,progress) {
      calls.downloads++;
      if(signal.aborted) throw Error('cancelled');
      await fs.writeFile(file,options.corrupt?Buffer.alloc(payload.length):payload);
      progress(1);
    },
  };
  const vscode = {
    workspace:{getConfiguration:()=>({get:()=>options.enabled !== false})},
    ProgressLocation:{Notification:15},
    Uri:{joinPath:(base,...parts)=>({...base,fsPath:path.join(base.fsPath,...parts)}),parse:value=>value},
    env:{openExternal:async uri=>calls.commands.push(['open',uri])},
    commands:{async executeCommand(name,uri) {
      if(name==='workbench.extensions.installExtension') {
        assert.deepEqual(await fs.readFile(uri.fsPath),payload);
        assert.equal(uri.scheme,context.globalStorageUri.scheme);
      }
      calls.commands.push([name,uri]);
    }},
    window:{
      async showInformationMessage(message,...buttons) {calls.info.push(message);return choices.shift();},
      async showWarningMessage(message) {calls.warnings.push(message);},
      async showErrorMessage(message) {calls.errors.push(message);},
      withProgress:async (_options,task)=>task({report(){}},{onCancellationRequested(callback){
        if(options.cancel)callback();return {dispose(){}};
      }}),
    },
  };
  const instance = updater.createUpdater(context,vscode,{platform:options.target || 'darwin-arm64',network,
    log:(...values)=>calls.logs.push(values)});
  return {root,calls,instance,context,vscode,network,fixture:f,
    async close() {instance.dispose();if(!options.root)await fs.rm(root,{recursive:true,force:true});}};
}

async function main() {
  assert.ok(updater.compare('26.2.10001','26.2.10')>0);
  assert.ok(updater.compare('26.2.10002','26.2.10001')>0);
  assert.ok(updater.compare('26.2.11001','26.2.10999')>0);
  assert.equal(updater.platform('darwin','arm64'),'darwin-arm64');
  assert.equal(updater.platform('linux','x64'),'linux-x64');
  assert.equal(updater.platform('linux','mips'),null);
  for (const url of ['http://github.com/x','https://evil.example/x','https://token@github.com/x','https://github.com:444/x']) {
    assert.throws(()=>transport.safeUrl(url));
  }
  assert.ok(transport.safeUrl('https://release-assets.githubusercontent.com/file?signature=test'));
  const f=fixture();
  assert.equal(updater.validateMetadata(f.data,f.release,'darwin-arm64').version,'26.2.10001');
  assert.throws(()=>updater.validateMetadata(f.data,f.release,'linux-arm64'));
  assert.throws(()=>updater.validateMetadata({...f.data,version:'26.2.10002'},f.release,'darwin-arm64'));
  assert.throws(()=>updater.validateMetadata({...f.data,size:2**40},f.release,'darwin-arm64'));
  assert.throws(()=>updater.validateMetadata({...f.data,sha256:'0'.repeat(64)},f.release,'darwin-arm64'));
  const evil=structuredClone(f.release);evil.assets[1].browser_download_url='https://evil.example/'+f.data.assetName;
  assert.throws(()=>updater.validateMetadata(f.data,evil,'darwin-arm64'));

  let h=await harness(['Позже']);
  try {
    await h.instance.check();await h.instance.check();
    assert.equal(h.calls.info.length,1);assert.equal(h.calls.downloads,0);assert.equal(h.calls.commands.length,0);
    assert.equal(h.calls.json,2);
    await h.instance.check(true);assert.equal(h.calls.info.length,2);
  } finally {await h.close();}

  h=await harness(['Обновить','Позже']);
  try {
    await h.instance.check(true);
    assert.deepEqual(h.calls.commands.map(x=>x[0]),['workbench.extensions.installExtension']);
    assert.deepEqual((await fs.readdir(h.root)).sort(),['github-updates.json']);
  } finally {await h.close();}

  h=await harness(['Обновить','Перезагрузить окно'],{target:'linux-arm64',scheme:'vscode-remote'});
  try {
    await h.instance.check(true);
    assert.deepEqual(h.calls.commands.map(x=>x[0]),['workbench.extensions.installExtension','workbench.action.reloadWindow']);
  } finally {await h.close();}

  h=await harness(['Обновить'],{corrupt:true});
  try {
    await h.instance.check(true);
    assert.equal(h.calls.commands.length,0);assert.equal(h.calls.errors.length,1);
    assert.deepEqual((await fs.readdir(h.root)).sort(),['github-updates.json']);
  } finally {await h.close();}

  h=await harness(['Обновить'],{cancel:true});
  try {
    await h.instance.check(true);
    assert.equal(h.calls.commands.length,0);assert.equal(h.calls.errors.length,0);
    assert.ok(h.calls.logs.some(values=>values[0]==='installation_cancelled'));
    assert.deepEqual((await fs.readdir(h.root)).sort(),['github-updates.json']);
  } finally {await h.close();}

  h=await harness([],{enabled:false});
  try {
    await h.instance.check();assert.equal(h.calls.json,0);
    await h.instance.check(true);assert.equal(h.calls.json,2);
  } finally {await h.close();}

  h=await harness([],{offline:true});
  try {
    await h.instance.check();assert.equal(h.calls.warnings.length,0);
    await h.instance.check();assert.equal(h.calls.json,1);
    await h.instance.check(true);assert.equal(h.calls.warnings.length,1);
  } finally {await h.close();}

  h=await harness([],{version:'26.2.10001'});
  try {
    await h.instance.check(true);assert.match(h.calls.info[0],/последняя сборка/);
    assert.equal(h.calls.downloads,0);
  } finally {await h.close();}

  h=await harness(['Позже']);
  const second=await harness(['Позже'],{root:h.root});
  try {
    await Promise.all([h.instance.check(),second.instance.check()]);
    assert.equal(h.calls.info.length+second.calls.info.length,1);
    assert.equal(h.calls.json+second.calls.json,2);
  } finally {await second.close();await h.close();}

  // Wrapping upstream activation preserves its return value and deactivation,
  // and an updater startup failure cannot break the upstream extension.
  const entry=await fs.readFile(path.join(__dirname,'../local-entry.cjs'),'utf8');
  for (const fail of [false,true]) {
    const context={};const result=Promise.resolve({api:true});let activated=0;
    const original={activate(value){assert.equal(value,context);activated++;return result;},deactivate(){}};
    const box={module:{exports:{}},console:{warn(){}},require:name=>{
      if(name==='./extension.js')return original;
      if(name==='./local-updater.cjs')return {activate(){if(fail)throw Error('test');}};
      throw Error('Unexpected import');
    }};
    vm.runInNewContext(entry,box);
    assert.equal(box.module.exports.activate(context),result);
    assert.equal(box.module.exports.deactivate,original.deactivate);assert.equal(activated,1);
  }
  console.log('GitHub updater checks passed: platform, notifications, installation, hash rejection, locks and activation.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
