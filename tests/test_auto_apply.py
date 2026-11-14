import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import auto_apply


class AutoApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.messages = []
        self.log_patch = patch.object(auto_apply, 'log')
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)

    def extension(self, name='one', version='26.9999.1'):
        root = self.root / name
        (root / 'out').mkdir(parents=True)
        (root / 'webview/assets').mkdir(parents=True)
        (root / 'package.json').write_text(json.dumps({
            'publisher':'openai', 'name':'chatgpt', 'version':version,
        }))
        (root / 'out/extension.js').write_text('host')
        (root / 'webview/index.html').write_text('html')
        (root / 'webview/assets/header-test.js').write_text('header')
        return root

    def run_once(self, extensions, stable_seconds=0):
        return auto_apply.run_once(self.state, extensions, stable_seconds, self.messages.append)

    def test_update_applies_once_and_unchanged_install_is_skipped(self):
        first = self.extension()
        second = self.extension('two', '26.9999.2')
        success = subprocess.CompletedProcess([], 0, 'Installed.', '')
        with patch.object(auto_apply, 'run_installer', return_value=success) as run:
            self.assertEqual(self.run_once([first])[0]['state'], 'applied')
            self.assertEqual(self.run_once([first]), [])
            self.assertEqual(self.run_once([second])[0]['state'], 'applied')
            self.assertEqual(run.call_count, 2)
        self.assertEqual(len(self.messages), 2)

    def test_verified_existing_patches_do_not_notify(self):
        extension = self.extension()
        success = subprocess.CompletedProcess([], 0, 'Already installed; files verified.', '')
        with patch.object(auto_apply, 'run_installer', return_value=success):
            self.assertEqual(self.run_once([extension])[0]['state'], 'verified')
        self.assertEqual(self.messages, [])

    def test_incompatible_build_is_not_modified_and_notifies_once(self):
        extension = self.extension()
        original = (extension / 'out/extension.js').read_bytes()
        failure = subprocess.CompletedProcess([], 1, '', 'Unsupported architecture')
        with patch.object(auto_apply, 'run_installer', return_value=failure) as run:
            self.assertEqual(self.run_once([extension])[0]['state'], 'needs-adaptation')
            self.assertEqual(self.run_once([extension]), [])
            self.assertEqual(run.call_count, 1)
        self.assertEqual((extension / 'out/extension.js').read_bytes(), original)
        self.assertEqual(len(self.messages), 1)

    def test_files_still_being_extracted_are_deferred(self):
        extension = self.extension()
        with patch.object(auto_apply, 'run_installer') as run:
            self.assertEqual(self.run_once([extension], stable_seconds=3600), [])
            run.assert_not_called()

    def test_disabled_worker_and_concurrent_worker_do_not_apply(self):
        extension = self.extension()
        auto_apply.save_json(self.state / 'config.json', {'enabled': False})
        with patch.object(auto_apply, 'run_installer') as run:
            self.assertEqual(self.run_once([extension]), [])
            auto_apply.save_json(self.state / 'config.json', {'enabled': True})
            with auto_apply.worker_lock(self.state) as locked:
                self.assertTrue(locked)
                self.assertEqual(self.run_once([extension]), [])
            run.assert_not_called()

    def test_launchagent_uses_absolute_programs_and_no_network_credentials(self):
        plist = auto_apply.launchagent_definition('/opt/bin/python3', '/opt/node/bin/node', self.root, self.root)
        self.assertEqual(plist['StartInterval'], 60)
        self.assertTrue(plist['RunAtLoad'])
        self.assertEqual(plist['ProgramArguments'], ['/opt/bin/python3', str(self.root/'auto_apply.py'), '--once'])
        self.assertEqual(set(plist['EnvironmentVariables']), {'PATH'})
        self.assertIn('/opt/node/bin', plist['EnvironmentVariables']['PATH'])


if __name__ == '__main__':
    unittest.main()
