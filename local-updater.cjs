'use strict';
const fs = require('node:fs/promises');
const {createReadStream} = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const transport = require('./github-transport.cjs');

const REPO = 'hammersmithg1/codex-ide-local-fixes';
const RELEASES = `https://github.com/${REPO}/releases`;
const API = `https://api.github.com/repos/${REPO}/releases?per_page=30`;
const COMMAND = 'chatgpt.localCheckForUpdates';
const SIX_HOURS = 6 * 60 * 60 * 1000;
const RETRY = 15 * 60 * 1000;

function version(value) {
  if (typeof value !== 'string' || !/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value)) throw new Error('invalid_version');
  const numbers = value.split('.').map(Number);
  if (!numbers.every(Number.isSafeInteger)) throw new Error('invalid_version');
  return numbers;
}
function compare(left, right) {
  const a = version(left), b = version(right);
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] > b[i] ? 1 : -1;
  return 0;
}
function platform(os = process.platform, arch = process.arch) {
  return ['darwin', 'linux', 'win32'].includes(os) && ['x64', 'arm64'].includes(arch) ? `${os}-${arch}` : null;
}
function ownedAsset(asset, release) {
  if (!asset || typeof asset.browser_download_url !== 'string') throw new Error('invalid_asset');
  const url = new URL(asset.browser_download_url);
  const prefix = `/${REPO}/releases/download/${release.tag_name}/`;
  if (url.protocol !== 'https:' || url.hostname !== 'github.com' || url.username || url.password ||
      url.port || !url.pathname.startsWith(prefix) || url.search || url.hash ||
      decodeURIComponent(url.pathname.slice(prefix.length)) !== asset.name) throw new Error('invalid_asset');
  return url.href;
}

function validateMetadata(data, release, target) {
  if (!data || data.schema !== 1 || data.platform !== target ||
      release.tag_name !== `codex-${data.upstreamVersion}-${target}`) throw new Error('invalid_metadata');
  const [major, minor, patch] = version(data.upstreamVersion);
  if (!Number.isInteger(data.revision) || data.revision < 1 || data.revision > 999 ||
      data.version !== `${major}.${minor}.${patch * 1000 + data.revision}`) throw new Error('invalid_metadata');
  version(data.version);
  const name = `codex-local-fixes-${data.upstreamVersion}-${target}.vsix`;
  if (data.assetName !== name || !/^[a-f0-9]{64}$/.test(data.sha256) ||
      !Number.isSafeInteger(data.size) || data.size < 1 || data.size > 1024 ** 3) throw new Error('invalid_metadata');
  const asset = release.assets.find(value => value.name === name);
  const url = ownedAsset(asset, release);
  if (asset.size !== data.size || (asset.digest && asset.digest !== 'sha256:' + data.sha256)) throw new Error('metadata_package_mismatch');
  return {...data, url};
}

async function candidate(network, target, current, signal) {
  const releases = await network.json(API, signal);
  if (!Array.isArray(releases)) throw new Error('invalid_releases');
  const pattern = new RegExp(`^codex-(\\d+\\.\\d+\\.\\d+)-${target}$`);
  const eligible = releases.filter(release => !release.draft && !release.prerelease &&
    typeof release.tag_name === 'string' && pattern.test(release.tag_name) && Array.isArray(release.assets));
  eligible.sort((a, b) => compare(pattern.exec(b.tag_name)[1], pattern.exec(a.tag_name)[1]));
  for (const release of eligible.slice(0, 3)) {
    const asset = release.assets.find(value => value.name === `codex-update-${target}.json`);
    if (!asset) continue; // Releases from before updater support have no feed.
    const data = await network.json(ownedAsset(asset, release), signal, 64 * 1024);
    const update = validateMetadata(data, release, target);
    if (compare(update.version, current) > 0) return update;
  }
  return null;
}

async function readState(file) {
  try { return JSON.parse(await fs.readFile(file, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT' || error instanceof SyntaxError) return {}; throw error; }
}
async function writeState(file, state) {
  const temporary = file + '.' + crypto.randomUUID();
  try {
    await fs.writeFile(temporary, JSON.stringify(state), {mode: 0o600});
    await fs.rename(temporary, file);
  } finally { await fs.rm(temporary, {force: true}); }
}
async function lock(file, ttl) {
  const id = crypto.randomUUID();
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const handle = await fs.open(file, 'wx', 0o600);
      try { await handle.writeFile(JSON.stringify({id, at: Date.now()})); }
      finally { await handle.close(); }
      return async () => { if ((await readState(file)).id === id) await fs.unlink(file); };
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      const stat = await fs.stat(file).catch(() => null);
      if (!stat || Date.now() - stat.mtimeMs < ttl) return null;
      await fs.unlink(file).catch(() => {});
    }
  }
  return null;
}
async function verifyFile(file, update) {
  if ((await fs.stat(file)).size !== update.size) throw new Error('incorrect_package_size');
  const hash = crypto.createHash('sha256');
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  if (hash.digest('hex') !== update.sha256) throw new Error('incorrect_package_hash');
}

function createUpdater(context, vscode, options = {}) {
  const network = options.network || transport;
  const target = options.platform || platform();
  const current = context.extension.packageJSON.version;
  const root = context.globalStorageUri.fsPath;
  const stateFile = path.join(root, 'github-updates.json');
  const log = options.log || (() => {});
  let disposed = false, checking = false, prompting = false;
  const controllers = new Set();
  function controller(timeout) {
    const abort = new AbortController();
    controllers.add(abort);
    const timer = setTimeout(() => abort.abort(), timeout);
    return {abort, done() { clearTimeout(timer); controllers.delete(abort); }};
  }
  async function install(update) {
    await fs.mkdir(root, {recursive: true});
    const release = await lock(path.join(root, 'github-install.lock'), 30 * 60 * 1000);
    if (!release) { await vscode.window.showInformationMessage('Обновление Codex уже устанавливается в другом окне.'); return; }
    const active = controller(10 * 60 * 1000);
    let folder;
    try {
      await vscode.window.withProgress({location: vscode.ProgressLocation.Notification,
        title: 'Обновление Codex', cancellable: true}, async (progress, token) => {
        const cancellation = token.onCancellationRequested(() => active.abort.abort());
        try {
          folder = await fs.mkdtemp(path.join(root, 'github-update-'));
          const file = path.join(folder, 'update.vsix');
          let previous = 0;
          await network.download(update.url, file, update.size, active.abort.signal, fraction => {
            const percent = Math.min(100, Math.floor(fraction * 100));
            if (percent > previous) { progress.report({increment: percent - previous, message: `Загрузка: ${percent}%`}); previous = percent; }
          });
          await verifyFile(file, update);
          if (active.abort.signal.aborted || disposed) throw new Error('cancelled');
          // Preserve the context URI's scheme so remote-native extensions update
          // in their own extension host rather than on the attached desktop.
          const uri = vscode.Uri.joinPath(context.globalStorageUri, path.basename(folder), 'update.vsix');
          await vscode.commands.executeCommand('workbench.extensions.installExtension', uri);
          log('installed', update.version);
        } finally { cancellation.dispose(); }
      });
      if (!disposed && await vscode.window.showInformationMessage('Обновление Codex установлено. Перезагрузить окно?',
          'Перезагрузить окно', 'Позже') === 'Перезагрузить окно') {
        await vscode.commands.executeCommand('workbench.action.reloadWindow');
      }
    } catch (error) {
      log(active.abort.signal.aborted ? 'installation_cancelled' : 'installation_failed');
      if (!active.abort.signal.aborted && !disposed) await vscode.window.showErrorMessage('Не удалось установить обновление Codex. Попробуйте проверить обновления ещё раз.');
    } finally {
      active.done();
      if (folder) await fs.rm(folder, {recursive: true, force: true});
      await release();
    }
  }
  async function offer(update) {
    if (disposed || prompting) return;
    prompting = true;
    try {
      const choice = await vscode.window.showInformationMessage(`Доступно обновление Codex: ${update.upstreamVersion}, сборка ${update.revision}.`,
        'Обновить', 'Открыть релиз', 'Позже');
      if (choice === 'Обновить' && !disposed) await install(update);
      if (choice === 'Открыть релиз') await vscode.env.openExternal(vscode.Uri.parse(RELEASES));
    } finally { prompting = false; }
  }
  async function check(manual = false) {
    if (disposed || checking || !target) return;
    if (!manual && !vscode.workspace.getConfiguration('codexLocalFixes').get('checkForUpdates', true)) return;
    checking = true;
    let release, active, offerUpdate, message;
    try {
      await fs.mkdir(root, {recursive: true});
      release = await lock(path.join(root, 'github-check.lock'), 2 * 60 * 1000);
      if (!release) return;
      const state = await readState(stateFile);
      if (!manual && Date.now() < (state.nextCheck || 0)) return;
      active = controller(30_000);
      let update;
      try {
        update = await candidate(network, target, current, active.abort.signal);
        state.nextCheck = Date.now() + SIX_HOURS;
      } catch (error) {
        state.nextCheck = Date.now() + RETRY;
        await writeState(stateFile, state);
        throw error;
      } finally { active.done(); active = null; }
      const notify = update && (manual || state.notified !== update.version);
      if (notify) state.notified = update.version;
      await writeState(stateFile, state);
      await release(); release = null;
      log(update ? 'update_available' : 'up_to_date', update?.version || current);
      if (notify) offerUpdate = update;
      else if (manual) message = update ? 'Уведомление об обновлении уже открыто.' : 'Установлена последняя сборка Codex для этой платформы.';
    } catch {
      log('check_failed');
      if (manual && !disposed) await vscode.window.showWarningMessage('Не удалось проверить обновления Codex. Проверьте подключение к GitHub.');
    } finally {
      active?.done();
      if (release) await release();
      checking = false;
    }
    // An unanswered notification must not keep the network check running.
    if (!disposed && offerUpdate) await offer(offerUpdate);
    else if (!disposed && message) await vscode.window.showInformationMessage(message);
  }
  return {check, dispose() { disposed = true; for (const abort of controllers) abort.abort(); }};
}

function activate(context) {
  const vscode = require('vscode');
  const output = vscode.window.createOutputChannel('Codex Updates');
  const updater = createUpdater(context, vscode, {log: (event, version) => output.appendLine(`[updates] ${event}${version ? ' ' + version : ''}`)});
  const run = manual => { updater.check(manual).catch(() => output.appendLine('[updates] check_failed')); };
  const startup = setTimeout(() => run(false), 10_000);
  const interval = setInterval(() => run(false), RETRY);
  context.subscriptions.push(output, updater,
    vscode.commands.registerCommand(COMMAND, () => updater.check(true)),
    vscode.window.onDidChangeWindowState(state => { if (state.focused) run(false); }),
    {dispose() { clearTimeout(startup); clearInterval(interval); }});
}

module.exports = {activate, createUpdater, candidate, validateMetadata, compare, platform, verifyFile, COMMAND};
