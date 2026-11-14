const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const preview = process.env.CODEX_TEST_HISTORY_PREVIEW;
const helpers = fs.readFileSync(require('node:path').join(__dirname, '..', 'history_filter.js'), 'utf8');
const source = preview ? fs.readFileSync(preview, 'utf8') : helpers;
const current = preview && source.includes('function Pt(e){let __historyRoots=');
const hookStart = current ? 'function Pt(' : 'function _n(';
const hookEnd = current ? 'var Lt,Y;' : 'var bn,xn;';
if (preview) {
  assert.ok(source.includes(helpers), 'tested helper must be embedded in the real bundle');
  assert.ok(source.includes(hookStart) && source.includes(hookEnd), 'known hook boundaries');
}
const mergeFunctions = preview ? source.slice(source.indexOf(hookStart), source.indexOf(hookEnd)) : '';
let roots = ['/work/rover'];
let memoIndex = 0, refIndex = 0;
const memos = [], refs = [];
const pendingWorktrees = [
  {hostId:'local', clientThreadId:'pending-rover', sourceWorkspaceRoot:'/work/rover', createdAt:10},
  {hostId:'local', clientThreadId:'pending-ios', sourceWorkspaceRoot:'/work/ios', createdAt:10},
];
const pendingStarts = [
  {hostId:'local', clientThreadId:'starting-rover', task:{cwd:'/work/rover', createdAt:10}},
  {hostId:'local', clientThreadId:'starting-ios', task:{cwd:'/work/ios', createdAt:10}},
];
const conversations = [
  {hostId:'local', conversationId:'rover', cwd:'/work/rover', createdAt:1, updatedAt:2},
  {hostId:'local', conversationId:'ios', cwd:'/work/ios', createdAt:1, updatedAt:3},
  {hostId:'local', conversationId:'child', cwd:'/work/rover/src', createdAt:1, updatedAt:3},
  {hostId:'another-host', conversationId:'other-host', cwd:'/work/rover', createdAt:1, updatedAt:3},
];
const context = {
  __codexHistoryRoots:{}, K:{}, ie:{}, le:'updated_at',
  te:signal=>signal === context.K ? pendingStarts : {data:roots === undefined ? undefined : {roots}},
  Ae:()=>pendingWorktrees, x:()=>new Map(), Je:()=>false,
  hn:()=>true, he:id=>id, me:id=>id, n:{error:message=>{throw Error(message)}},
  bn:{default:(items,key)=>[...new Map(items.map(item=>[item[key],item])).values()]},
  xn:{
    useRef(initial){const index=refIndex++; return refs[index] ??= {current:initial}},
    useMemo(fn,deps){
      const index=memoIndex++, old=memos[index];
      if(old && old.deps.length===deps.length && deps.every((dep,i)=>Object.is(dep,old.deps[i]))) return old.value;
      const value=fn(); memos[index]={deps,value}; return value;
    },
  },
};
if (current) Object.assign(context, {
  he: {}, Ie: {}, Ae: 'updated_at',
  g: signal => signal === context.he ? pendingStarts : {data: roots === undefined ? undefined : {roots}},
  Je: () => pendingWorktrees, C: () => new Map(), ye: () => false,
  Mt: () => true, Oe: id => id, Be: context.n, Lt: context.bn, Y: context.xn,
});
vm.createContext(context);
vm.runInContext(helpers + mergeFunctions,context);
const cloud = [{id:'cloud', created_at:20}];
const ids = items=>Array.from(items,item=>item.key ?? item.conversationId).sort();
function render(){memoIndex=refIndex=0; return current ? context.Pt(conversations) : context._n(cloud,conversations,null)}
if (preview) {
assert.deepEqual(ids(render()), ['pending-rover','rover','starting-rover']);
// Reuse the same chat arrays: a root change must invalidate the actual hook memo.
roots=['/work/ios'];
assert.deepEqual(ids(render()), ['ios','pending-ios','starting-ios']);
roots=['/work/rover','/work/ios'];
assert.deepEqual(ids(render()), ['ios','pending-ios','pending-rover','rover','starting-ios','starting-rover']);
roots=undefined;
assert.deepEqual(ids(render()), []);
roots=[];
assert.equal(render().length,current ? 8 : 9);
}
const filter=context.__codexHistoryFilter;
assert.deepEqual(ids(filter(conversations,['/work/rover/'],false)),['rover']);
assert.deepEqual(ids(filter(conversations,['/work/rover-other'],false)),[]);
assert.deepEqual(ids(filter([{hostId:'local',conversationId:'win',cwd:'C:\\Work\\Rover\\'}],['c:/work/rover'],false)),['win']);
assert.deepEqual(ids(filter([{hostId:'local',conversationId:'wrong-case',cwd:'/Work/Rover'}],['/work/rover'],false)),[]);
assert.deepEqual(ids(filter([{hostId:'local',conversationId:'no-cwd'}],['/work/rover'],false)),[]);
assert.deepEqual(ids(filter(conversations,undefined,false)),[]);
assert.equal(filter(conversations,[],false),conversations);
// Verify the separate Local chats path uses the same root-aware predicate.
if (preview && !current) assert.ok(source.includes('let D=__codexHistoryFilter(r,te(__codexHistoryRoots).data?.roots,false).filter(ne),O=_n(n.data,r,w)'));
if (preview && current) assert.ok((source.match(/\bPt\(/g) ?? []).length >= 3, 'main list and history menu share the filtered hook');
console.log(preview ? 'History bundle integration checks passed.' : 'History filter checks passed.');
