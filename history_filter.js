/* codex-workspace-history-v1 */
function __codexHistoryPath(path) {
  if (typeof path !== "string" || path.length === 0) return null;
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/, "") || "/";
  return /^[A-Za-z]:\//.test(normalized) ? normalized.toLowerCase() : normalized;
}
function __codexHistoryFilter(items, roots, merged) {
  // Do not briefly show chats from other folders while roots are loading.
  if (!Array.isArray(roots)) return [];
  // A window without an open folder keeps the original history behavior.
  if (roots.length === 0) return items;
  const paths = new Set(roots.map(__codexHistoryPath).filter(Boolean));
  return items.filter(item => {
    let cwd, hostId;
    if (merged) {
      // Cloud chats have no local working directory to match.
      if (item.kind !== "local") return false;
      const source = item.conversation ?? item.pendingThreadStart ?? item.pendingWorktree;
      hostId = source?.hostId;
      cwd = item.conversation?.cwd ?? item.pendingThreadStart?.task?.cwd
        ?? item.pendingWorktree?.sourceWorkspaceRoot;
    } else {
      cwd = item.cwd;
      hostId = item.hostId;
    }
    // The local execution host also represents a VS Code Remote window.
    return hostId === "local" && paths.has(__codexHistoryPath(cwd));
  });
}
