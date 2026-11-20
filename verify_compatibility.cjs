// Run the generated history hook with deterministic dependencies and real merge code.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
const names = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const helpers = fs.readFileSync(path.join(__dirname, 'history_filter.js'), 'utf8');
assert.ok(source.includes(helpers));
const hookStart = source.indexOf('function ' + names.hook + '(');
const mergeStart = source.indexOf('function ' + names.merge + '(', hookStart);
const reuseStart = source.indexOf('function ' + names.reuse + '(', mergeStart);
const end = source.indexOf('var ', reuseStart);
assert.ok(hookStart >= 0 && mergeStart > hookStart && reuseStart > mergeStart && end > reuseStart);
let roots = ['/work/rover'], cursor = 0, refCursor = 0;
const memos = [], refs = [];
const items = [
  {hostId:'local',conversationId:'rover',cwd:'/work/rover',createdAt:1,updatedAt:2},
  {hostId:'local',conversationId:'ios',cwd:'/work/ios',createdAt:1,updatedAt:3},
  {hostId:'local',conversationId:'child',cwd:'/work/rover/src',createdAt:1,updatedAt:3},
  {hostId:'other',conversationId:'remote',cwd:'/work/rover',createdAt:1,updatedAt:3},
];
const worktrees = [
  {hostId:'local',clientThreadId:'pending-rover',sourceWorkspaceRoot:'/work/rover',createdAt:3},
  {hostId:'local',clientThreadId:'pending-ios',sourceWorkspaceRoot:'/work/ios',createdAt:3},
  {hostId:'other',clientThreadId:'pending-remote',sourceWorkspaceRoot:'/work/rover',createdAt:3},
];
const starts = [
  {hostId:'local',clientThreadId:'starting-rover',task:{cwd:'/work/rover',createdAt:3}},
  {hostId:'local',clientThreadId:'starting-ios',task:{cwd:'/work/ios',createdAt:3}},
  {hostId:'other',clientThreadId:'starting-remote',task:{cwd:'/work/rover',createdAt:3}},
];
const context = {__codexHistoryRoots:{}};
context[names.pending] = {};
context[names.ids] = {};
context[names.worktrees] = () => worktrees;
context[names.signal] = signal => signal === context[names.pending] ? starts :
  {data: roots === undefined ? undefined : {roots}};
context[names.family] = () => new Map();
context[names.feature] = () => false;
context[names.sort] = 'updated_at';
context[names.predicate] = () => true;
context[names.key] = id => id;
context[names.logger] = {error: message => {throw Error(message)}};
context[names.uniq] = {default: (values,key) => [...new Map(values.map(value=>[value[key],value])).values()]};
context[names.react] = {
  useRef: initial => refs[refCursor++] ??= {current:initial},
  useMemo: (fn,deps) => {
    const index=cursor++,old=memos[index];
    if(old&&deps.every((dep,i)=>Object.is(dep,old.deps[i])))return old.value;
    const value=fn();memos[index]={deps,value};return value;
  },
};
vm.createContext(context);
vm.runInContext(helpers+source.slice(hookStart,end),context);
function render(){cursor=refCursor=0;return Array.from(context[names.hook](items),item=>item.key).sort()}
assert.deepEqual(render(),['pending-rover','rover','starting-rover']);
roots=['/work/ios'];
assert.deepEqual(render(),['ios','pending-ios','starting-ios']);
roots=['/work/rover','/work/ios'];
assert.deepEqual(render(),['ios','pending-ios','pending-rover','rover','starting-ios','starting-rover']);
roots=undefined;assert.deepEqual(render(),[]);
roots=[];assert.equal(render().length,items.length+worktrees.length+starts.length);
console.log('Generated history hook compatibility checks passed.');
