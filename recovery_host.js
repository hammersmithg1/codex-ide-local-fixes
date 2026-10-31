/* codex-local-recovery-v1: extension-host helpers, no app-server restart. */
var __codexLocalRecoveryByWebview = new WeakMap();

function __codexCreateRecovery(options) {
  let disposed = false;
  let ready = false;
  let automaticRetries = 0;
  let generation = 0;
  let pending = null;
  return {
    async timeout(details) {
      if (disposed || ready || pending) return;
      options.log("timeout", {
        elapsedMs: details.elapsedMs,
        receivedWebviewMessage: details.receivedWebviewMessage,
        automaticRetries,
      });
      if (automaticRetries < 1) {
        automaticRetries++;
        return this.reload(false);
      }
      options.failed();
    },
    reload(manual = true) {
      if (disposed) return Promise.resolve();
      if (pending) return pending;
      if (manual) automaticRetries = 0;
      ready = false;
      const current = ++generation;
      const isCurrent = () => !disposed && !ready && generation === current;
      options.cancelTimer();
      options.log(manual ? "manual_reload" : "automatic_reload", {generation: current});
      // Defer render until pending is assigned, including synchronous failures.
      pending = Promise.resolve().then(async () => {
        try {
          if (isCurrent()) await options.render(current, isCurrent);
        } catch (error) {
          options.log("reload_failed", {errorName: String(error?.name ?? "Error").slice(0, 80)});
          if (isCurrent()) options.failed();
        } finally {
          pending = null;
        }
      });
      return pending;
    },
    markReady() {
      if (disposed || ready) return;
      ready = true;
      options.cancelTimer();
      options.log("ready", {automaticRetries});
    },
    dispose() {
      disposed = true;
      generation++;
      options.cancelTimer();
    },
  };
}

function __codexLocalDiagnostic(message) {
  const allowed = new Set([
    "bootstrap", "dom_ready", "window_loaded", "resource_error",
    "javascript_error", "unhandled_rejection", "loading_snapshot",
  ]);
  if (!allowed.has(message.stage)) return null;
  const data = {stage: message.stage};
  for (const key of ["elapsedMs", "resourceCount"]) {
    if (Number.isFinite(message[key])) data[key] = Math.max(0, Math.min(3600000, message[key]));
  }
  // Only bundled asset names and error classes, never URLs, prompts or stacks.
  if (typeof message.asset === "string" && /^[A-Za-z0-9_.-]{1,180}$/.test(message.asset)) {
    data.asset = message.asset;
  }
  if (typeof message.errorName === "string" && /^[A-Za-z]{1,80}$/.test(message.errorName)) {
    data.errorName = message.errorName;
  }
  return data;
}

function __codexRecoveryFallback(scriptUrl, cspSource) {
  const escape = value => String(value).replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  return `<!doctype html><html lang="ru"><head><meta charset="UTF-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src ${escape(cspSource)}; style-src 'unsafe-inline';">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Codex: восстановление панели</title>
    <style>body{font-family:var(--vscode-font-family,sans-serif);color:var(--vscode-foreground);background:var(--vscode-editor-background);padding:24px;line-height:1.5}button{display:block;margin:12px 0;padding:8px 14px;color:var(--vscode-button-foreground);background:var(--vscode-button-background);border:0;cursor:pointer}button:disabled{opacity:.6}</style>
    <script defer src="${escape(scriptUrl)}"></script></head><body>
    <h2>Не удалось загрузить панель Codex</h2>
    <p>Повторный запуск интерфейса не завершился.</p>
    <button data-codex-recovery="retry">Перезапустить панель Codex</button>
    <button data-codex-recovery="window">Перезагрузить это окно VS Code</button>
    <p>Диагностика: Output → Codex. Команда перезапуска: Codex: Reload Codex UI (Local Fix).</p>
    </body></html>`;
}
