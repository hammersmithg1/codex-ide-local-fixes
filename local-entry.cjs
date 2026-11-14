'use strict';
const upstream = require('./extension.js');
module.exports = {
  ...upstream,
  activate(context, ...args) {
    // A failed update check must never prevent the Codex UI from starting.
    try { require('./local-updater.cjs').activate(context); }
    catch { console.warn('Codex update checker could not start.'); }
    return upstream.activate.call(upstream, context, ...args);
  },
};
