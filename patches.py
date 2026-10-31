"""Guarded edits for Codex IDE 26.1021.22027; no upstream bundles are stored here."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = '26.1021.22027'
HISTORY_MARKER = '/* codex-workspace-history-v1 */'
RECOVERY_MARKER = '/* codex-local-recovery-v1'
COMMAND = 'chatgpt.localReloadUI'


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('Unsupported bundle: expected one anchor: ' + old[:90])
    return source.replace(old, new, 1)


def history(source, context_filename):
    if HISTORY_MARKER in source:
        return source
    changes = [
        ('function _n(e,t,n){let r=Ae()',
         'function _n(e,t,n){let __historyRoots=te(__codexHistoryRoots).data?.roots;let r=Ae()'),
        ('let a=yn(vn({tasks:e,localConversations:t,pendingWorktrees:r,pendingThreadStarts:i,envForFilter:n,threadSortKey:le,isBackgroundSubagentsEnabled:s,clientThreadIdsByConversationId:o}),c.current)',
         'let a=yn(__codexHistoryFilter(vn({tasks:e,localConversations:t,pendingWorktrees:r,pendingThreadStarts:i,envForFilter:n,threadSortKey:le,isBackgroundSubagentsEnabled:s,clientThreadIdsByConversationId:o}),__historyRoots,true),c.current)'),
        ('},[e,n,o,s,t,r,i])}function vn(',
         '},[e,n,o,s,t,r,i,__historyRoots])}function vn('),
        ('let D=r.filter(ne),O=_n(n.data,r,w)',
         'let D=__codexHistoryFilter(r,te(__codexHistoryRoots).data?.roots,false).filter(ne),O=_n(n.data,r,w)'),
        ('bn=t(h(),1),w(),xn=u(),L()',
         'bn=t(h(),1),__codexHistoryRootsInit(),w(),xn=u(),L()'),
    ]
    for old, new in changes:
        source = replace_once(source, old, new)
    return ('import{d as __codexHistoryRoots,s as __codexHistoryRootsInit}'
            f'from"./{context_filename}";\n' + (ROOT / 'history_filter.js').read_text() + source)


def recovery(source):
    if RECOVERY_MARKER in source:
        return source
    start = source.index('let s=new K2(u=>{this.logger.error("Webview renderer did not become ready"')
    end = source.index('this.registerClientCoordinationForWebview(e,n,s)', start)
    replacement = '''let __localRecovery;
let s=new K2(u=>{void __localRecovery.timeout(u)});
__localRecovery=__codexCreateRecovery({
  log:(event,fields)=>this.logger.warning("Local webview recovery",{safe:{event,role:r,extensionVersion:this.extensionVersion,...fields},sensitive:{}}),
  cancelTimer:()=>{if(s.timeout!=null)clearTimeout(s.timeout);s.timeout=void 0},
  failed:()=>{e.html=__codexRecoveryFallback(e.asWebviewUri(Be.Uri.joinPath(i,"assets","codex-local-bootstrap.js")).toString(),e.cspSource)},
  render:async(generation,isCurrent)=>{
    const content=await this.getWebviewContent(e);
    if(!isCurrent())return;
    this.disposeClientCoordinationForWebview(e);
    this.registerClientCoordinationForWebview(e,n,s);
    const sidebar=Array.from(this.sidebarViews).find(view=>view.webview===e);
    if(sidebar){this.readySidebarViews.delete(sidebar);if(this.sidebarView===sidebar)this.sidebarWebviewReady=false;}
    const panel=this.findPanelByWebview(e),panelState=this.editorPanels.get(panel);
    if(panelState)panelState.ready=false;
    s.disposed=false;s.receivedWebviewMessage=false;
    e.html=content+"<!-- codex-local-reload:"+generation+":"+Date.now()+" -->";
    s.start();
  }
});
__codexLocalRecoveryByWebview.set(e,__localRecovery);
'''
    source = source[:start] + replacement + source[end:]
    source = replace_once(source, 'if(s.markMessageReceived(),u.type==="chunked-message-ack")', '''
if(u.type==="codex-local-diagnostic"){
 const diagnostic=__codexLocalDiagnostic(u);
 if(diagnostic)this.logger.info("Local webview bootstrap",{safe:{role:r,...diagnostic},sensitive:{}});
 return;
}
if(u.type==="codex-local-recovery-action"){
 if(u.action==="retry")void __localRecovery.reload();
 else if(u.action==="window")void Be.commands.executeCommand("workbench.action.reloadWindow");
 return;
}
if(s.markMessageReceived(),u.type==="chunked-message-ack")''')
    source = replace_once(source, 'if(u.type==="ready"){if(r==="sidebar")',
                          'if(u.type==="ready"){s.handleStartupPhase("renderer_ready");__localRecovery.markReady();if(r==="sidebar")')
    source = replace_once(source, 's.dispose(),this.chunkedMessageSender.dispose(e)',
                          '__localRecovery.dispose(),__codexLocalRecoveryByWebview.delete(e),s.dispose(),this.chunkedMessageSender.dispose(e)')
    source = replace_once(source, 'async initializeWebview(e,r,n,o){', '''
async localReloadUI(){
 const panel=this.getActivePanelFocusedView()?.panel;
 const webview=this.focusedView?.kind==="panel"?this.getWebviewForPanel(panel):this.sidebarView?.webview??this.getWebviewForPanel(panel);
 if(webview)await __codexLocalRecoveryByWebview.get(webview)?.reload();
}
async initializeWebview(e,r,n,o){''')
    source = replace_once(source, 'e.push(Ot.commands.registerCommand("chatgpt.openSidebar",ru)),',
                          'e.push(Ot.commands.registerCommand("chatgpt.openSidebar",ru)),e.push(Ot.commands.registerCommand("chatgpt.localReloadUI",async()=>{await ru();await $e.localReloadUI()})),')
    return source + '\n' + (ROOT / 'recovery_host.js').read_text()


def bootstrap_html(source):
    script = '<script src="./assets/codex-local-bootstrap.js"></script>'
    if script in source:
        return source
    return replace_once(source, '<!-- PROD_CSP_TAG_HERE -->', '<!-- PROD_CSP_TAG_HERE -->\n    ' + script)
