#!/usr/bin/env python3
"""Install reversible local fixes into an existing official Codex IDE extension."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import patches


def digest(data):
    return hashlib.sha256(data).hexdigest()


def discover(home=None):
    home = home or Path.home()
    roots = [home / name / 'extensions' for name in (
        '.vscode', '.vscode-insiders', '.cursor', '.windsurf',
        '.vscode-server', '.vscode-server-insiders',
    )]
    found = []
    for root in roots:
        active = None
        index = root / 'extensions.json'
        if index.exists():
            entries = json.loads(index.read_text(encoding='utf-8'))
            active = {entry.get('relativeLocation') for entry in entries
                      if entry.get('identifier', {}).get('id') == 'openai.chatgpt'}
        for extension in root.glob('openai.chatgpt-*'):
            if not (extension / 'package.json').is_file():
                continue
            if active is None or extension.name in active:
                found.append(extension.resolve())
    return sorted(set(found))


def select_extension(explicit, allow_compatible=False):
    if explicit:
        extension = explicit.expanduser().resolve()
    else:
        found = discover()
        if len(found) != 1:
            listing = '\n'.join(str(path) for path in found) or '(none found)'
            raise ValueError('Specify --extension-dir; found:\n' + listing)
        extension = found[0]
    package = json.loads((extension / 'package.json').read_text(encoding='utf-8'))
    if package.get('publisher') != 'openai' or package.get('name') != 'chatgpt':
        raise ValueError('This is not the official openai.chatgpt extension')
    if package.get('version') not in patches.PROFILES and not allow_compatible:
        raise ValueError(f"Supported versions: {', '.join(patches.PROFILES)}; found: {package.get('version')}. Nothing changed.")
    return extension, package


def syntax_check(plan):
    node = shutil.which('node')
    if node is None:
        raise ValueError('Node.js is required for JavaScript syntax checks')
    with tempfile.TemporaryDirectory(prefix='codex-local-check-') as temp:
        for index, (name, data) in enumerate(plan.items()):
            if not name.endswith('.js'):
                continue
            suffix = '.mjs' if name.startswith('webview/assets/header-') else '.cjs'
            path = Path(temp) / (str(index) + suffix)
            path.write_bytes(data)
            subprocess.run([node, '--check', str(path)], check=True, capture_output=True)


def plan_changes(extension, package, allow_compatible=False):
    version = package['version']
    if allow_compatible and version != patches.LEGACY_VERSION:
        import compatibility
        profile = compatibility.infer_profile(extension)
    else:
        profile = patches.PROFILES[version]
    assets = extension / 'webview/assets'
    headers = [path for path in assets.glob('header-*.js')
               if 'header.recentTasks.seeAll' in path.read_text(encoding='utf-8')]
    contexts = list(assets.glob(profile['context_glob']))
    if len(headers) != 1 or len(contexts) != 1:
        raise ValueError('Expected exactly one history module and one workspace-context module')
    context = contexts[0].read_text(encoding='utf-8')
    if any(anchor not in context for anchor in profile['context_anchors']):
        raise ValueError('Unsupported workspace-context exports')
    header = headers[0]
    new_package = json.loads(json.dumps(package))
    commands = new_package['contributes']['commands']
    if not any(command['command'] == patches.COMMAND for command in commands):
        commands.append({'command': patches.COMMAND, 'title': 'Reload Codex UI (Local Fix)', 'category': 'Codex'})
    plan = {
        str(header.relative_to(extension)).replace(os.sep, '/'):
            patches.history(header.read_text(encoding='utf-8'), contexts[0].name, version, profile).encode(),
        'out/extension.js': patches.recovery((extension / 'out/extension.js').read_text(encoding='utf-8'), version, profile).encode(),
        'webview/index.html': patches.bootstrap_html((extension / 'webview/index.html').read_text(encoding='utf-8')).encode(),
        'webview/assets/codex-local-bootstrap.js': (patches.ROOT / 'bootstrap.js').read_bytes(),
        'package.json': (json.dumps(new_package, ensure_ascii=False, indent=2) + '\n').encode(),
    }
    syntax_check(plan)
    if profile.get('inferred'):
        compatibility.verify_plan(plan, profile, patches.ROOT)
    return plan


def original_bytes(extension, name):
    path = extension / name
    current = path.read_bytes() if path.exists() else None
    if current is None or patches.HISTORY_MARKER.encode() not in current:
        return current
    # Adopt the previous one-machine patch only with a verified original backup.
    legacy = Path.home() / f'.agents/local/backups/codex-workspace-history-{patches.LEGACY_VERSION}'
    record_path = legacy / 'record.json'
    if record_path.exists():
        record = json.loads(record_path.read_text(encoding='utf-8'))
        saved = legacy / path.name
        if (Path(record['target']).resolve() == path.resolve()
                and digest(current) == record['patched_sha256']
                and saved.exists() and digest(saved.read_bytes()) == record['original_sha256']):
            return saved.read_bytes()
    raise ValueError('Found a prior history patch without a verified original backup. Restore it first.')


def atomic_write(path, data):
    # os.replace avoids leaving a truncated JavaScript bundle on interruption.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.codex-local-', delete=False) as temp:
        temporary = Path(temp.name)
        try:
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if path.exists():
            temporary.chmod(path.stat().st_mode & 0o777)
        else:
            temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_installed(extension, record):
    if Path(record['extension']).resolve() != extension.resolve():
        raise ValueError('Backup belongs to another extension directory')
    for name, item in record['files'].items():
        path = extension / name
        if not path.exists() or digest(path.read_bytes()) != item['patched_sha256']:
            raise ValueError(f'File changed since installation: {name}. Refusing to overwrite it.')


def apply(extension, plan, state, version=patches.VERSION):
    record_path = state / 'record.json'
    if record_path.exists():
        verify_installed(extension, json.loads(record_path.read_text(encoding='utf-8')))
        print('Already installed; files verified.')
        return
    # Validate every original before creating backups or writing any target file.
    originals = {name: original_bytes(extension, name) for name in plan}
    before = {name: (extension / name).read_bytes() if (extension / name).exists() else None for name in plan}
    record = {'extension': str(extension), 'version': version, 'files': {}}
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, data in originals.items():
        saved = state / 'original' / name
        if data is not None:
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(data)
        record['files'][name] = {'original_sha256': None if data is None else digest(data),
                                 'patched_sha256': digest(plan[name])}
    written = []
    try:
        for name, data in plan.items():
            written.append(name)
            atomic_write(extension / name, data)
        verify_installed(extension, record)
        atomic_write(record_path, (json.dumps(record, indent=2) + '\n').encode())
    except BaseException:
        for name in reversed(written):
            path = extension / name
            if before[name] is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, before[name])
        raise
    print(f'Installed. Backup: {state}')
    print('Activate: Developer: Reload Window. Recovery: Codex: Reload Codex UI (Local Fix).')


def restore(extension, state):
    record_path = state / 'record.json'
    record = json.loads(record_path.read_text(encoding='utf-8'))
    verify_installed(extension, record)
    originals = {}
    for name, item in record['files'].items():
        data = None if item['original_sha256'] is None else (state / 'original' / name).read_bytes()
        if data is not None and digest(data) != item['original_sha256']:
            raise ValueError('Backup checksum mismatch: ' + name)
        originals[name] = data
    before = {name: (extension / name).read_bytes() for name in originals}
    written = []
    try:
        for name, data in originals.items():
            written.append(name)
            if data is None:
                (extension / name).unlink()
            else:
                atomic_write(extension / name, data)
    except BaseException:
        for name in written:
            atomic_write(extension / name, before[name])
        raise
    record_path.replace(state / 'restored-record.json')
    print('Restored original extension. Run Developer: Reload Window.')


def diagnose():
    home = Path.home()
    if sys.platform == 'darwin':
        root = home / 'Library/Application Support/Code/logs'
    elif sys.platform == 'win32':
        root = Path(os.environ.get('APPDATA', str(home / 'AppData/Roaming'))) / 'Code/logs'
    else:
        root = Path(os.environ.get('XDG_CONFIG_HOME', str(home / '.config'))) / 'Code/logs'
    needles = ['Webview renderer did not become ready', 'Local webview recovery', 'Local webview bootstrap']
    print('Log root:', root)
    for session in sorted(root.glob('*'), reverse=True)[:5]:
        for path in sorted(session.glob('window*/exthost/openai.chatgpt/Codex.log')):
            lines = [line for line in path.read_text(encoding='utf-8', errors='replace').splitlines()
                     if any(needle in line for needle in needles)]
            if lines:
                print('\n' + str(path))
                print('\n'.join(line[:700] for line in lines[-12:]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extension-dir', type=Path)
    parser.add_argument('--state-dir', type=Path, help='Override the local backup directory')
    parser.add_argument('--compatible', action='store_true',
                        help='Recognize the known architecture even if version/asset/identifier names changed; run runtime checks')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Validate compatibility without modifying the extension')
    mode.add_argument('--restore', action='store_true')
    mode.add_argument('--diagnose', action='store_true', help='Read only startup/recovery log entries')
    args = parser.parse_args()
    try:
        if args.diagnose:
            diagnose()
            return 0
        extension, package = select_extension(args.extension_dir, args.compatible)
        state = args.state_dir or (Path.home() / '.agents/local/backups/codex-ide-local-fixes' / digest(str(extension).encode())[:16])
        if args.restore:
            restore(extension, state)
        elif (state / 'record.json').exists():
            verify_installed(extension, json.loads((state / 'record.json').read_text(encoding='utf-8')))
            print('Already installed; files verified.')
        else:
            plan = plan_changes(extension, package, args.compatible)
            if args.check:
                print(f'Compatible: {extension}. JavaScript syntax checks passed; no extension files changed.')
            else:
                apply(extension, plan, state, package['version'])
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
