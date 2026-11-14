'use strict';
const https = require('node:https');
const fs = require('node:fs');
const {pipeline} = require('node:stream/promises');
const {Transform} = require('node:stream');

const HOSTS = new Set(['api.github.com', 'github.com', 'objects.githubusercontent.com',
  'release-assets.githubusercontent.com']);

function safeUrl(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password ||
      (url.port && url.port !== '443') || !HOSTS.has(url.hostname)) {
    throw new Error('untrusted_url');
  }
  return url;
}

function responseFor(value, signal, redirects = 0) {
  const url = safeUrl(value);
  if (redirects > 5) throw new Error('too_many_redirects');
  return new Promise((resolve, reject) => {
    const request = https.get(url, {signal, headers: {
      'User-Agent': 'codex-local-fixes-updater',
      'Accept': url.hostname === 'api.github.com' ? 'application/vnd.github+json' : 'application/octet-stream',
    }}, response => {
      if ([301, 302, 303, 307, 308].includes(response.statusCode)) {
        response.resume();
        try {
          resolve(responseFor(new URL(response.headers.location, url).href, signal, redirects + 1));
        } catch (error) { reject(error); }
      } else if (response.statusCode !== 200) {
        response.resume();
        reject(new Error('http_' + response.statusCode));
      } else { resolve(response); }
    });
    request.on('error', () => reject(new Error(signal?.aborted ? 'cancelled' : 'network_error')));
    request.setTimeout(30_000, () => request.destroy());
  });
}

async function json(url, signal, limit = 2 * 1024 * 1024) {
  const response = await responseFor(url, signal);
  const chunks = [];
  let size = 0;
  for await (const chunk of response) {
    size += chunk.length;
    if (size > limit) { response.destroy(); throw new Error('metadata_too_large'); }
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

async function download(url, destination, expectedSize, signal, progress) {
  const response = await responseFor(url, signal);
  let size = 0;
  const count = new Transform({transform(chunk, encoding, done) {
    size += chunk.length;
    if (size > expectedSize) return done(new Error('package_too_large'));
    progress(size / expectedSize);
    done(null, chunk);
  }});
  await pipeline(response, count, fs.createWriteStream(destination, {flags: 'wx', mode: 0o600}), {signal});
}

module.exports = {json, download, safeUrl};
