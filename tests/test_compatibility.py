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
        profile = compatibility.infer_profile(self.extension)
        assets = self.extension / 'webview/assets'
        header = next(path for path in assets.glob('header-*.js') if 'header.recentTasks.seeAll' in path.read_text())
        context = assets / profile['context_glob']
        new_context = assets / 'app-initial-simulated-update.js'

        def rename(source, pairs):
            for old, new in pairs:
                # A minified name can also appear in a Unicode regex property escape.
                pattern = r'(?<!\\p\{)(?<!\\P\{)(?<![\w$])' + re.escape(old) + r'(?![\w$])'
                source = re.sub(pattern, new, source)
            return source

        roots, roots_export = profile['context_anchors'][1].split(' as ')
        initializer, init_export = profile['context_anchors'][2].split(' as ')
        new_context.write_text(rename(context.read_text(), [
            (roots, 'FutureWorkspaceRoots'), (initializer, 'FutureWorkspaceInit'),
            (roots_export, 'futureRootsExport'), (init_export, 'futureInitExport'),
        ]))
        context.unlink()
        symbols = profile['probe']
        text = rename(header.read_text().replace(context.name, new_context.name), [
            (symbols['hook'], 'FutureHistoryHook'), (symbols['worktrees'], 'FutureWorktreeHook'),
            (symbols['react'], 'FutureReact'), (symbols['uniq'], 'FutureUniq'),
        ])
        new_header = assets / 'header-simulated-update.js'
        new_header.write_text(text)
        header.unlink()
        host = self.extension / 'out/extension.js'
        api, open_sidebar = re.search(r'([\w$]+)\.commands\.registerCommand\("chatgpt.openSidebar",([\w$]+)',
                                      profile['command_anchor']).groups()
        provider = re.search(r'await ([\w$]+)\.localReloadUI', profile['command_code'])[1]
        host.write_text(rename(host.read_text(), [
            (profile['watchdog'], 'FutureWatchdog'), (profile['vscode'], 'FutureVsCode'),
            (api, 'FutureCommands'), (provider, 'FutureProvider'), (open_sidebar, 'FutureOpen'),
        ]))
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

    def test_changed_history_memo_dependencies_are_rejected(self):
        profile = compatibility.infer_profile(self.extension)
        header = next(path for path in (self.extension / 'webview/assets').glob('header-*.js')
                      if 'header.recentTasks.seeAll' in path.read_text())
        hook = profile['history_changes'][0][0]
        self.assertTrue(hook.endswith(',t,n])}'))
        header.write_text(header.read_text().replace(hook, hook.removesuffix(',t,n])}') + ',t])}', 1))
        with self.assertRaisesRegex(ValueError, 'expected one local history hook, found 0'):
            compatibility.infer_profile(self.extension)

    def test_changed_watchdog_behavior_is_rejected_without_writes(self):
        path = self.extension / 'out/extension.js'
        source = path.read_text()
        watchdog = compatibility.infer_profile(self.extension)['watchdog']
        start = source.index(watchdog + '=class{')
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
