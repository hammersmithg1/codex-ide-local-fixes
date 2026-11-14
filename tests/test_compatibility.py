import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import auto_apply
import compatibility
import install


class RecognitionTests(unittest.TestCase):
    def test_repeated_identifiers_must_match(self):
        pattern = compatibility.template_pattern('@a@(@b@,@a@)')
        self.assertIsNotNone(re.fullmatch(pattern, 'renamed(other,renamed)'))
        self.assertIsNone(re.fullmatch(pattern, 'renamed(other,different)'))

    def test_ambiguous_match_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'expected one'):
            compatibility.one('hook', 'hook hook', 'hook')


@unittest.skipUnless(os.environ.get('CODEX_TEST_EXTENSION_DIR'), 'Requires a local unpatched extension fixture')
class ActualBundleCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.extension = self.root / 'extension'
        shutil.copytree(os.environ['CODEX_TEST_EXTENSION_DIR'], self.extension)

    def test_future_version_and_renamed_assets_and_symbols_install_and_restore(self):
        assets = self.extension / 'webview/assets'
        header = next(path for path in assets.glob('header-*.js') if 'header.recentTasks.seeAll' in path.read_text())
        context = next(path for path in assets.glob('app-initial-*.js') if '`workspace-root-options`' in path.read_text())
        new_context = assets / 'app-initial-simulated-update.js'
        text = context.read_text()
        for old, new in [('Ay','FutureWorkspaceRoots'),('Py','FutureWorkspaceInit'),
                         ('byn','futureRootsExport'),('gyn','futureInitExport')]:
            text = re.sub(r'\b' + old + r'\b', new, text)
        new_context.write_text(text)
        context.unlink()
        text = header.read_text().replace(context.name, new_context.name)
        for old, new in [('Pt','FutureHistoryHook'),('Je','FutureWorktreeHook'),
                         ('Y','FutureReact'),('Lt','FutureUniq')]:
            text = re.sub(r'\b' + old + r'\b', new, text)
        new_header = assets / 'header-simulated-update.js'
        new_header.write_text(text)
        header.unlink()
        host = self.extension / 'out/extension.js'
        text = host.read_text()
        for old, new in [('E2','FutureWatchdog'),('ze','FutureVsCode'),
                         ('Et','FutureCommands'),('Ct','FutureProvider'),('vi','FutureOpen')]:
            text = re.sub(r'\b' + old + r'\b', new, text)
        host.write_text(text)
        package = json.loads((self.extension / 'package.json').read_text())
        package['version'] = '99.1231.99999'
        (self.extension / 'package.json').write_text(json.dumps(package))
        original = {str(path.relative_to(self.extension)):path.read_bytes() for path in self.extension.rglob('*') if path.is_file()}
        selected, package = install.select_extension(self.extension, allow_compatible=True)
        plan = install.plan_changes(selected, package, allow_compatible=True)
        state = self.root / 'backup'
        install.apply(selected, plan, state, package['version'])
        install.restore(selected, state)
        restored = {str(path.relative_to(self.extension)):path.read_bytes() for path in self.extension.rglob('*') if path.is_file()}
        self.assertEqual(original, restored)

    def test_changed_watchdog_behavior_is_rejected_without_writes(self):
        path = self.extension / 'out/extension.js'
        source = path.read_text()
        start = source.index('E2=class{')
        end = source.index('};', start)+2
        source = source[:start] + source[start:end].replace('3e4', '4e4') + source[end:]
        path.write_text(source)
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'watchdog behavior'):
            compatibility.infer_profile(self.extension)
        self.assertEqual(path.read_bytes(), before)

    def test_worker_applies_real_installer_to_new_version_and_can_restore(self):
        package_path = self.extension / 'package.json'
        package = json.loads(package_path.read_text())
        package['version'] = '99.1231.99998'
        package_path.write_text(json.dumps(package))
        original = {str(path.relative_to(self.extension)): install.digest(path.read_bytes())
                    for path in self.extension.rglob('*') if path.is_file()}
        messages = []
        home = self.root / 'home'
        home.mkdir()
        with patch.dict(os.environ, {'HOME': str(home)}), patch.object(auto_apply, 'log'):
            changes = auto_apply.run_once(self.root / 'worker', [self.extension], 0, messages.append)
            self.assertEqual([item['state'] for item in changes], ['applied'])
            self.assertEqual(auto_apply.run_once(self.root / 'worker', [self.extension], 0, messages.append), [])
            self.assertEqual(len(messages), 1)
            result = subprocess.run([sys.executable, str(auto_apply.ROOT / 'install.py'),
                                     '--compatible', '--extension-dir', str(self.extension), '--restore'],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
        restored = {str(path.relative_to(self.extension)): install.digest(path.read_bytes())
                    for path in self.extension.rglob('*') if path.is_file()}
        self.assertEqual(original, restored)


if __name__ == '__main__':
    unittest.main()
