#!/usr/bin/env python3
"""Prepare and verify a locally branded personal VSIX from official upstream."""
import argparse
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import install

LOCAL_ID = 'hammersmithg1.codex-local-fixes'
PLATFORMS = ('darwin-arm64', 'darwin-x64', 'linux-arm64', 'linux-x64', 'win32-x64', 'win32-arm64')


def valid_version(version):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid upstream version')
    return version


def latest(platform):
    body = {'filters': [{'criteria': [{'filterType': 7, 'value': 'openai.chatgpt'}]}],
            'flags': 147}
    request = urllib.request.Request(
        'https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery',
        data=json.dumps(body).encode(), headers={
            'Content-Type': 'application/json', 'Accept': 'application/json;api-version=7.2-preview.1'})
    with urllib.request.urlopen(request, timeout=45) as response:
        data = json.load(response)
    extensions = data['results'][0]['extensions']
    official = [e for e in extensions if e['extensionName'] == 'chatgpt'
                and e['publisher']['publisherName'] == 'openai']
    if len(official) != 1:
        raise ValueError('Official upstream entry is ambiguous')
    versions = []
    for item in official[0]['versions']:
        if item.get('targetPlatform') != platform:
            continue
        if any(p['key'] == 'Microsoft.VisualStudio.Code.PreRelease' and p['value'] == 'true'
               for p in item.get('properties', [])):
            continue
        versions.append(valid_version(item['version']))
    if not versions:
        raise ValueError('No stable upstream package for this platform')
    return max(versions, key=lambda version: tuple(map(int, version.split('.'))))


def fetch(version, platform, target):
    valid_version(version)
    if target.exists():
        raise ValueError('Use a fresh upstream directory')
    target.mkdir(parents=True)
    archive = target / 'upstream.vsix'
    url = ('https://marketplace.visualstudio.com/_apis/public/gallery/publishers/openai/'
           f'vsextensions/chatgpt/{version}/vspackage?targetPlatform={platform}')
    subprocess.run(['curl', '--fail', '--silent', '--show-error', '--location', '--retry', '2',
                    '--max-time', '600', '--output', str(archive), url], check=True)
    # Marketplace may gzip the HTTP response even though the package is a ZIP.
    # Decode by magic as well as headers so mislabeled responses work too.
    with archive.open('rb') as stream:
        compressed = stream.read(2) == b'\x1f\x8b'
    if compressed:
        decoded = target / 'upstream.decoded'
        try:
            with gzip.open(archive, 'rb') as source, decoded.open('wb') as destination:
                shutil.copyfileobj(source, destination)
            decoded.replace(archive)
        finally:
            decoded.unlink(missing_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename:
                raise ValueError('Unsafe archive path')
        bundle.extractall(target / 'unpacked')
    package = json.loads((target / 'unpacked/extension/package.json').read_text())
    if package.get('name') != 'chatgpt' or package.get('publisher') != 'openai' or package.get('version') != version:
        raise ValueError('Downloaded package does not match official upstream')
    print('Prepared official Codex', version, platform)


def verify_and_patch(target):
    original = target / 'unpacked/extension'
    patched = target / 'patched'
    if patched.exists():
        shutil.rmtree(patched)
    shutil.copytree(original, patched)
    package = json.loads((patched / 'package.json').read_text())
    plan = install.plan_changes(patched, package, allow_compatible=True)
    before = {str(path.relative_to(patched)): install.digest(path.read_bytes())
              for path in patched.rglob('*') if path.is_file()}
    with tempfile.TemporaryDirectory(prefix='codex-ci-rollback-') as temp:
        state = Path(temp) / 'backup'
        install.apply(patched, plan, state, package['version'])
        install.restore(patched, state)
        after = {str(path.relative_to(patched)): install.digest(path.read_bytes())
                 for path in patched.rglob('*') if path.is_file()}
        if before != after:
            raise ValueError('Upstream rollback was not byte-for-byte reversible')
        install.apply(patched, plan, state, package['version'])
    print('Verified actual generated hooks, host recovery, syntax and exact rollback.')


def package(target, platform, output):
    patched = target / 'patched'
    package_path = patched / 'package.json'
    data = json.loads(package_path.read_text())
    version = valid_version(data['version'])
    data.update(name='codex-local-fixes', publisher='hammersmithg1', displayName='Codex (Local Fixes)')
    data.pop('__metadata', None)
    package_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    # This ID is also used for the installed version, Settings link, and OAuth URI.
    # Commands/views keep their IDs; disable official Codex before installing.
    host = patched / 'out/extension.js'
    source = host.read_text()
    if 'openai.chatgpt' not in source:
        raise ValueError('Expected upstream extension identity was not found')
    source = source.replace('openai.chatgpt', LOCAL_ID)
    install.syntax_check({'out/extension.js': source.encode()})
    host.write_text(source)
    manifest = ET.parse(target / 'unpacked/extension.vsixmanifest')
    # VS Code reads unprefixed PackageManifest/Metadata/Identity keys. ElementTree
    # otherwise emits ns0:* tags and VS Code silently treats the package as universal.
    namespace = manifest.getroot().tag.partition('}')[0].removeprefix('{')
    if manifest.getroot().tag.startswith('{'):
        ET.register_namespace('', namespace)
    identity = next(element for element in manifest.iter() if element.tag.split('}')[-1] == 'Identity')
    identity.set('Id', data['name'])
    identity.set('Publisher', data['publisher'])
    identity.set('TargetPlatform', platform)
    for element in manifest.iter():
        if element.tag.split('}')[-1] == 'DisplayName':
            element.text = data['displayName']
    manifest_bytes = ET.tostring(manifest.getroot(), encoding='utf-8', xml_declaration=True)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f'codex-local-fixes-{version}-{platform}.vsix'
    written = set()
    with zipfile.ZipFile(target / 'upstream.vsix') as original, zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as result:
        for entry in original.infolist():
            name = entry.filename
            if name.lower().endswith(('.p7s', '.vsixsignature')):
                continue  # The upstream signature cannot sign a modified build.
            if name == 'extension.vsixmanifest':
                content = manifest_bytes
            elif name.startswith('extension/') and not entry.is_dir():
                content = (patched / name[len('extension/'):]).read_bytes()
            else:
                content = original.read(entry)
            result.writestr(entry, content)
            written.add(name)
        for path in patched.rglob('*'):
            name = 'extension/' + path.relative_to(patched).as_posix()
            if path.is_file() and name not in written:
                result.write(path, name)
    print('Built personal VSIX:', destination.name)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['latest', 'fetch', 'verify', 'package'])
    parser.add_argument('--platform', choices=PLATFORMS, default='darwin-arm64')
    parser.add_argument('--version')
    parser.add_argument('--target', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.mode == 'latest':
            print(latest(args.platform))
        elif args.mode == 'fetch':
            fetch(args.version, args.platform, args.target)
        elif args.mode == 'verify':
            verify_and_patch(args.target)
        else:
            package(args.target, args.platform, args.output)
        return 0
    except (ValueError, OSError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print('Upstream operation failed: ' + str(error)[:1500], file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
