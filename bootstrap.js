/* Small classic script: runs before the application's module dependency graph. */
(() => {
  const acquire = window.acquireVsCodeApi;
  if (typeof acquire !== "function") return;
  const api = acquire();
  // The official message bus gets this same handle once. Keep the API object
  // inside this closure instead of exposing it as a property on window.
  window.acquireVsCodeApi = () => {
    window.acquireVsCodeApi = acquire;
    return api;
  };
  const started = performance.now();
  let count = 0;
  function report(stage, fields = {}) {
    if (count++ >= 20) return;
    api.postMessage({type: "codex-local-diagnostic", stage,
      elapsedMs: Math.round(performance.now() - started), ...fields});
  }
  function assetName(value) {
    try { return new URL(value, document.baseURI).pathname.split("/").pop(); }
    catch { return undefined; }
  }
  report("bootstrap");
  document.addEventListener("DOMContentLoaded", () => report("dom_ready"), {once: true});
  window.addEventListener("load", () => report("window_loaded"), {once: true});
  window.addEventListener("error", event => {
    if (event.target !== window) {
      const target = event.target;
      if (target?.tagName === "SCRIPT" || target?.tagName === "LINK") {
        report("resource_error", {asset: assetName(target.src || target.href)});
      }
    } else {
      report("javascript_error", {errorName: event.error?.name, asset: assetName(event.filename)});
    }
  }, true);
  window.addEventListener("unhandledrejection", event => {
    report("unhandled_rejection", {errorName: event.reason?.name});
  });
  window.setTimeout(() => report("loading_snapshot", {
    resourceCount: performance.getEntriesByType("resource").length,
  }), 15000);
  document.addEventListener("click", event => {
    const button = event.target.closest?.("[data-codex-recovery]");
    if (!button) return;
    const action = button.dataset.codexRecovery;
    if (action !== "retry" && action !== "window") return;
    button.disabled = true;
    api.postMessage({type: "codex-local-recovery-action", action});
  });
})();
