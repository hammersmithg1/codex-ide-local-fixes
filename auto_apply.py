#!/usr/bin/env python3
"""Watch official Codex IDE updates and reapply structurally compatible fixes."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

import install


ROOT = Path(__file__).resolve().parent
LABEL = 'com.hms.codex-ide-local-fixes'
STATE = Path.home() / '.agents/local/state/codex-ide-local-fixes'
LOG = Path.home() / '.agents/local/logs/codex-ide-local-fixes.log'


def read_json(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    install.atomic_write(path, (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode())


def log(event, **fields):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if LOG.exists() and LOG.stat().st_size > 1024 * 1024:
        LOG.replace(LOG.with_suffix('.previous.log'))
    with LOG.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'time': datetime.now(timezone.utc).isoformat(),
                                 'event': event, **fields}, ensure_ascii=False) + '\n')


@contextmanager
def worker_lock(state):
    """Non-blocking OS lock, automatically released if the worker exits."""
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / 'worker.lock').open('a+b') as stream:
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            stream.write(b'0')
            stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
        else:
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
        try:
            yield True
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def notify(message):
    if sys.platform != 'darwin':
        return
    # Text is local fixed notification copy, not a shell command or user content.
    escaped = message.replace('\\', '\\\\').replace('"', '\\"')
    try:
        subprocess.run(['/usr/bin/osascript', '-e',
                        f'display notification "{escaped}" with title "Codex Local Fixes"'],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def fingerprint(extension):
    paths = [extension / name for name in ['package.json', 'out/extension.js', 'webview/index.html']]
    paths += list((extension / 'webview/assets').glob('header-*.js'))
    paths += [ROOT / name for name in ['patches.py', 'compatibility.py', 'install.py',
                                     'history_filter.js', 'recovery_host.js', 'bootstrap.js',
                                     'verify_compatibility.cjs', 'tests/host_integration.cjs']]
    stats = []
    newest = 0
    for path in paths:
        value = path.stat()
        stats.append((str(path), value.st_size, value.st_mtime_ns))
        if path.is_relative_to(extension):
            newest = max(newest, value.st_mtime)
    return hashlib.sha256(json.dumps(stats).encode()).hexdigest(), newest


def run_installer(extension):
    return subprocess.run([sys.executable, str(ROOT / 'install.py'), '--compatible',
                           '--extension-dir', str(extension)],
                          capture_output=True, text=True, timeout=90, cwd=ROOT)


def run_once(state=None, extensions=None, stable_seconds=10, notify_fn=None):
    state = state or STATE
    notify_fn = notify_fn or notify
    with worker_lock(state) as locked:
        if not locked:
            return []
        if not read_json(state / 'config.json', {}).get('enabled', True):
            return []
        status = read_json(state / 'status.json', {'entries': {}})
        entries = status['entries']
        changed = []
        for extension in extensions if extensions is not None else install.discover():
            extension = extension.resolve()
            package = read_json(extension / 'package.json', {})
            if package.get('publisher') != 'openai' or package.get('name') != 'chatgpt':
                continue
            signature, newest = fingerprint(extension)
            previous = entries.get(str(extension), {})
            if previous.get('fingerprint') == signature:
                continue
            # Avoid applying while VS Code is still extracting a new extension.
            if time.time() - newest < stable_seconds:
                continue
            version = package.get('version', 'unknown')
            log('checking', version=version, extension=str(extension))
            try:
                result = run_installer(extension)
                error = result.stderr.strip()[-1500:] if result.returncode else None
            except (OSError, subprocess.TimeoutExpired) as exception:
                error = type(exception).__name__ + ': ' + str(exception)[:500]
                result = None
            if result is not None and result.returncode == 0:
                outcome = 'verified' if 'Already installed' in result.stdout else 'applied'
                signature, _ = fingerprint(extension)
                if outcome == 'applied':
                    notify_fn(f'Патчи для Codex {version} применены. Перезагрузите окно VS Code, если оно уже открыто.')
            else:
                outcome = 'needs-adaptation'
                notify_fn(f'Codex {version}: структура изменилась, патчи не применены. Подробности: auto_apply.py --status.')
            entry = {'version': version, 'state': outcome, 'fingerprint': signature,
                     'checked_at': datetime.now(timezone.utc).isoformat()}
            if error:
                entry['error'] = error
            entries[str(extension)] = entry
            changed.append(entry)
            log(outcome, version=version, extension=str(extension), error=error)
        # Keep status small even after years of extension updates.
        status['entries'] = dict(sorted(entries.items(), key=lambda item: item[1]['checked_at'])[-40:])
        if changed or not (state / 'status.json').exists():
            save_json(state / 'status.json', status)
        return changed


def launchagent_definition(python, node, root, home):
    return {
        'Label': LABEL,
        'ProgramArguments': [str(python), str(root / 'auto_apply.py'), '--once'],
        'RunAtLoad': True,
        'WatchPaths': [str(home / '.vscode/extensions'), str(home / '.vscode/extensions/extensions.json'),
                       str(home / '.cursor/extensions'), str(home / '.vscode-insiders/extensions')],
        'StartInterval': 60,
        'ThrottleInterval': 10,
        'ProcessType': 'Background',
        'EnvironmentVariables': {'PATH': os.pathsep.join(dict.fromkeys([
            str(Path(node).parent), str(Path(python).parent), '/opt/homebrew/bin',
            '/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin']))},
        'StandardOutPath': '/dev/null',
        'StandardErrorPath': '/dev/null',
    }


def service_paths():
    stored = Path.home() / '.agents/local/launchagents' / (LABEL + '.plist')
    registration = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    return stored, registration


def enable():
    if sys.platform != 'darwin':
        raise ValueError('Automatic service installation currently supports macOS. --once can be scheduled elsewhere.')
    node = shutil.which('node')
    if not node:
        raise ValueError('Node.js was not found')
    stored, registration = service_paths()
    if registration.exists() or registration.is_symlink():
        if not registration.is_symlink() or registration.resolve() != stored.resolve():
            raise ValueError('An unmanaged LaunchAgent already uses this name')
    stored.parent.mkdir(parents=True, exist_ok=True)
    definition = launchagent_definition(sys.executable, node, ROOT, Path.home())
    install.atomic_write(stored, plistlib.dumps(definition))
    registration.parent.mkdir(parents=True, exist_ok=True)
    if not registration.is_symlink():
        registration.symlink_to(stored)
    save_json(STATE / 'config.json', {'enabled': True})
    target = f'gui/{os.getuid()}/{LABEL}'
    loaded = subprocess.run(['/bin/launchctl', 'print', target], capture_output=True).returncode == 0
    command = ['/bin/launchctl', 'kickstart', target] if loaded else [
        '/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(registration)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise ValueError('LaunchAgent registration failed: ' + result.stderr.strip())
    log('enabled', interval_seconds=60)
    print('Enabled: checks after extension changes, every 60 seconds and at login.')


def disable():
    # First prevent any new worker from modifying files, including next login.
    save_json(STATE / 'config.json', {'enabled': False})
    if sys.platform == 'darwin':
        stored, registration = service_paths()
        if registration.is_symlink() and registration.resolve() == stored.resolve():
            registration.unlink()
        # Do not kill a worker during its multi-file transaction. Removing the
        # registration and disabling config lets the current short run finish.
        with worker_lock(STATE) as locked:
            if locked:
                subprocess.run(['/bin/launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'], capture_output=True)
    log('disabled')
    print('Disabled. Installed fixes remain; use install.py --restore to remove them.')


def show_status():
    config = read_json(STATE / 'config.json', {'enabled': False})
    print('Automatic application:', 'enabled' if config['enabled'] else 'disabled')
    if sys.platform == 'darwin':
        loaded = subprocess.run(['/bin/launchctl', 'print', f'gui/{os.getuid()}/{LABEL}'], capture_output=True)
        print('LaunchAgent:', 'loaded' if loaded.returncode == 0 else 'not loaded')
    for extension, entry in read_json(STATE / 'status.json', {'entries': {}})['entries'].items():
        print(f"{entry['version']}: {entry['state']} ({extension})")
        if entry.get('error'):
            print(entry['error'])
    print('Log:', LOG)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--enable', action='store_true')
    modes.add_argument('--disable', action='store_true')
    modes.add_argument('--status', action='store_true')
    modes.add_argument('--once', action='store_true')
    args = parser.parse_args()
    try:
        if args.enable:
            enable()
        elif args.disable:
            disable()
        elif args.status:
            show_status()
        else:
            for entry in run_once():
                print(entry['version'] + ': ' + entry['state'])
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        log('worker-error', error=str(error)[:1000])
        print('Error:', error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
