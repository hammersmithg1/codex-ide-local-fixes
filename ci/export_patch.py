#!/usr/bin/env python3
"""Export agent changes only from the explicitly allowed adapter/test files."""
from pathlib import Path
import subprocess
import sys

ALLOWED = {'patches.py', 'compatibility.py', 'history_filter.js', 'recovery_host.js',
           'bootstrap.js', 'verify_compatibility.cjs', 'install.py'}


def allowed(path):
    return path in ALLOWED or (path.startswith('tests/') and '/' not in path[6:]
                               and path.endswith(('.py', '.cjs')))


def main():
    if sys.argv[1] == 'export':
        entries = subprocess.check_output(['git', 'status', '--porcelain=v1', '-z', '--untracked-files=all']).decode().split('\0')
        for entry in filter(None, entries):
            if entry[:2].strip() in ('R', 'C') or not allowed(entry[3:]):
                raise SystemExit('Agent changed a file outside the adapter/test allowlist.')
            if entry[:2] == '??':
                subprocess.run(['git', 'add', '-N', '--', entry[3:]], check=True)
        subprocess.run(['git', 'diff', '--check'], check=True)
        Path(sys.argv[2]).write_bytes(subprocess.check_output(['git', 'diff', '--binary', 'HEAD']))
    else:
        patch = Path(sys.argv[2])
        if not patch.stat().st_size:
            return
        subprocess.run(['git', 'apply', '--index', str(patch)], check=True)
        paths = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '-z']).decode().split('\0')
        if not all(allowed(path) for path in filter(None, paths)):
            raise SystemExit('Patch contains a file outside the adapter/test allowlist.')


if __name__ == '__main__':
    main()
