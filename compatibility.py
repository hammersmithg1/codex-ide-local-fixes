"""Recognize the known local-history architecture after a bundle rename.

This is deliberately structural: changing control flow or data shape requires a
new reviewed adapter. Identifier names, asset hashes and version numbers may vary.
"""
import json
from pathlib import Path
import re
import subprocess
import tempfile


IDENTIFIER = r'[A-Za-z_$][\w$]*'
HOOK = ('function @hook@(e){let t=@worktrees@(),n=@signal@(@pending@),'
        'r=(0,@react@.useMemo)(()=>e.map(e=>e.conversationId),[e]),'
        'i=@family@(@ids@,r),a=@feature@(),o=(0,@react@.useRef)(new Map);'
        'return(0,@react@.useMemo)(()=>{let r=@reuse@(@merge@({'
        'localConversations:e,pendingWorktrees:t,pendingThreadStarts:n,'
        'threadSortKey:@sort@,isBackgroundSubagentsEnabled:a,'
        'clientThreadIdsByConversationId:i}),o.current);'
        'return o.current=new Map(r.map(e=>[e.key,e])),r},[i,a,e,t,n])}')


def template_pattern(template):
    seen = set()
    parts = []
    for index, part in enumerate(re.split(r'@([a-z]+)@', template)):
        if index % 2 == 0:
            parts.append(re.escape(part))
        elif part in seen:
            parts.append(f'(?P={part})')
        else:
            seen.add(part)
            parts.append(f'(?P<{part}>{IDENTIFIER})')
    return ''.join(parts)


def one(pattern, source, description):
    matches = list(re.finditer(pattern, source))
    if len(matches) != 1:
        raise ValueError(f'Compatibility check: expected one {description}, found {len(matches)}')
    return matches[0]


def export_name(source, internal):
    exports = source[source.rfind('export{'):]
    return one(r'(?<![\w$])' + re.escape(internal) + r' as (' + IDENTIFIER + ')',
               exports, 'export for ' + internal).group(1)


def infer_profile(extension):
    assets = extension / 'webview/assets'
    headers = [path for path in assets.glob('header-*.js')
               if 'header.recentTasks.seeAll' in path.read_text(encoding='utf-8')]
    if len(headers) != 1:
        raise ValueError('Compatibility check: history module is ambiguous')
    header = headers[0].read_text(encoding='utf-8')
    hook = one(template_pattern(HOOK), header, 'local history hook')
    symbols = hook.groupdict()
    merge_start = header.index('function ' + symbols['merge'] + '(')
    reuse_start = header.index('function ' + symbols['reuse'] + '(', merge_start)
    functions_end = header.index('var ', reuse_start)
    merge = header[merge_start:reuse_start]
    symbols.update({
        'predicate': one(r'e\.filter\(e=>(' + IDENTIFIER + r')\(\{conversation:e,isBackgroundSubagentsEnabled:i\}\)\)', merge, 'conversation predicate').group(1),
        'key': one(r'key:(' + IDENTIFIER + r')\(a\.get\(e\.conversationId\)', merge, 'conversation key').group(1),
        'logger': one(r'(' + IDENTIFIER + r')\.error\(`local conversation has invalid createdAt or updatedAt`\)', merge, 'history logger').group(1),
        'uniq': one(r'return\(0,(' + IDENTIFIER + r')\.default\)\(\[\.\.\.c,\.\.\.o,\.\.\.s\],`key`\)', merge, 'local item collection').group(1),
    })
    # The result must be a local-only list with unchanged identity and cwd data.
    for anchor in ['conversation:e', 'pendingWorktree:e', 'pendingThreadStart:e',
                   'clientThreadId', 'conversationId', '.sort((e,t)=>t.at-e.at)']:
        if anchor not in merge:
            raise ValueError('Compatibility check: unexpected history data shape')

    context_candidates = []
    for path in list(assets.glob('app-initial-*.js')) + list(assets.glob('thread-context-inputs-*.js')):
        source = path.read_text(encoding='utf-8')
        if '`workspace-root-options`' in source and '?.workspaceRoots' in source:
            context_candidates.append((path, source))
    if len(context_candidates) != 1:
        raise ValueError('Compatibility check: workspace-root module is ambiguous')
    context_path, context = context_candidates[0]
    key_function = one(r'function (' + IDENTIFIER + r')\(e\)\{return e==null\?\[`vscode`,`workspace-root-options`\]',
                       context, 'workspace query key').group(1)
    query = one(r'(?P<root>' + IDENTIFIER + r')=' + IDENTIFIER + r'\(' + IDENTIFIER
                + r',\(\)=>\(\{queryKey:' + re.escape(key_function)
                + r'\(\{hostId:(?P<host>' + IDENTIFIER + r')\}\),queryFn:\(\{signal:e\}\)=>'
                + IDENTIFIER + r'\(\{hostId:(?P=host)\},e\),placeholderData:' + IDENTIFIER
                + r',staleTime:' + IDENTIFIER + r'\.INFINITE\}\)\)', context, 'fixed-host workspace query')
    initializers = list(re.finditer(r'function (?P<init>' + IDENTIFIER
                                   + r')\(\)\{return\((?P=init)=' + IDENTIFIER + r'\(\(\(\)=>\{', context[:query.start()]))
    if not initializers:
        raise ValueError('Compatibility check: workspace initializer was not found')
    root_export = export_name(context, query['root'])
    init_export = export_name(context, initializers[-1]['init'])
    if './' + context_path.name not in header:
        raise ValueError('Compatibility check: unexpected workspace dependency graph')

    original_hook = hook.group(0)
    patched_hook = original_hook.replace(
        'let t=', f"let __historyRoots={symbols['signal']}(__codexHistoryRoots).data?.roots;let t=", 1)
    call = (f"{symbols['reuse']}({symbols['merge']}({{localConversations:e,pendingWorktrees:t,"
            f"pendingThreadStarts:n,threadSortKey:{symbols['sort']},isBackgroundSubagentsEnabled:a,"
            'clientThreadIdsByConversationId:i}),o.current)')
    filtered_call = call.replace(symbols['reuse'] + '(', symbols['reuse'] + '(__codexHistoryFilter(', 1)
    filtered_call = filtered_call.replace('),o.current)', '),__historyRoots,true),o.current)', 1)
    if patched_hook.count(call) != 1:
        raise ValueError('Compatibility check: unexpected merge call')
    patched_hook = patched_hook.replace(call, filtered_call).replace(
        '[i,a,e,t,n])}', '[i,a,e,t,n,__historyRoots])}', 1)
    initializer = one(r'function ' + IDENTIFIER + r'\(\)\{return\(' + IDENTIFIER + '=' + IDENTIFIER
                      + r'\(\(\(\)=>\{' + re.escape(symbols['uniq']) + r'=', header, 'history initializer')
    init_anchor = initializer.group(0)

    host_source = (extension / 'out/extension.js').read_text(encoding='utf-8')
    init_start = host_source.index('async initializeWebview(e,r,n,o){')
    init_end = host_source.index('onPanelReady(e){', init_start)
    init = host_source[init_start:init_end]
    vscode = one(r'let i=(' + IDENTIFIER + r')\.Uri\.joinPath\(this.extensionUri,"webview"\)', init, 'VS Code API alias').group(1)
    watchdog = one(r'let s=new (?P<class>' + IDENTIFIER + r')\((?P<message>' + IDENTIFIER
                   + r')=>\{this.logger.error\("Webview renderer did not become ready"', init, 'watchdog creation')
    message = one(r'e\.onDidReceiveMessage\((' + IDENTIFIER + r')=>\{if\(s.markMessageReceived\(\),', init, 'webview message handler').group(1)
    if watchdog['message'] != message:
        raise ValueError('Compatibility check: unexpected timeout callback shape')
    watchdog_class = one(re.escape(watchdog['class']) + r'=class\{constructor\(e\)\{this.onTimeout=e\}'
                         r'timeout;disposed=!1;receivedWebviewMessage=!1;start\(\)\{if\(this.disposed\|\|this.timeout!=null\)return;'
                         r'let e=Date.now\(\);this.timeout=setTimeout\(\(\)=>\{this.timeout=void 0,this.onTimeout\(\{'
                         r'elapsedMs:Date.now\(\)-e,receivedWebviewMessage:this.receivedWebviewMessage,timeoutMs:3e4\}\)\},3e4\)\}'
                         r'markMessageReceived\(\)\{this.receivedWebviewMessage=!0\}handleStartupPhase\(e\)\{e==="renderer_ready"&&this.dispose\(\)\}'
                         r'dispose\(\)\{this.disposed=!0,this.timeout!=null&&\(clearTimeout\(this.timeout\),this.timeout=void 0\)\}\}',
                         host_source, 'known watchdog behavior')
    reader = one(r'e\.html=(await (?:this\.getWebviewContent\(e\)|' + IDENTIFIER
                 + r'\(this.extensionUri,e,this.webviewMetaTags\(e\)\))),s.start\(\)', init, 'HTML reader').group(1)
    command = one(r'e\.push\((?P<api>' + IDENTIFIER + r')\.commands.registerCommand\("chatgpt.openSidebar",'
                  r'(?P<open>' + IDENTIFIER + r')\)\),', host_source, 'sidebar command')
    provider = one(r'async\(\)=>\{await ' + re.escape(command['open']) + r'\(\),('
                   + IDENTIFIER + r')\.triggerNewChatViaWebview\(\)\}', host_source, 'provider instance').group(1)

    return {
        'inferred': True, 'context_glob': context_path.name,
        'context_anchors': (query.group(0), query['root'] + ' as ' + root_export,
                            initializers[-1]['init'] + ' as ' + init_export),
        'history_changes': [(original_hook, patched_hook),
                            (init_anchor, init_anchor.replace('()=>{', '()=>{__codexHistoryRootsInit();', 1))],
        'root_export': root_export, 'init_export': init_export,
        'watchdog': watchdog['class'], 'message': message, 'vscode': vscode,
        'read_content': reader,
        'command_anchor': command.group(0),
        'command_code': (f'e.push({command["api"]}.commands.registerCommand("chatgpt.localReloadUI",'
                         f'async()=>{{await {command["open"]}();await {provider}.localReloadUI()}})),') ,
        'probe': {**symbols, 'functions_end': 'var '},
    }


def verify_plan(plan, profile, root):
    """Exercise the actual generated hooks/host method before touching the extension."""
    import os
    import shutil
    with tempfile.TemporaryDirectory(prefix='codex-compatible-') as temp:
        temp = Path(temp)
        header = next(data for name, data in plan.items() if '/header-' in name)
        (temp / 'header.mjs').write_bytes(header)
        (temp / 'extension.js').write_bytes(plan['out/extension.js'])
        (temp / 'probe.json').write_text(json.dumps(profile['probe']))
        env = {**os.environ, 'CODEX_TEST_EXTENSION_PREVIEW': str(temp / 'extension.js')}
        commands = [[shutil.which('node') or 'node', str(root / 'verify_compatibility.cjs'),
                     str(temp / 'header.mjs'), str(temp / 'probe.json')],
                    [shutil.which('node') or 'node', str(root / 'tests/host_integration.cjs')]]
        for command in commands:
            result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise ValueError('Runtime compatibility check failed: ' + result.stderr[-1500:])
