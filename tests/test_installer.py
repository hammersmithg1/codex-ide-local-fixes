import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer', ROOT / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.extension = self.root / 'extension'
        self.extension.mkdir()
        self.state = self.root / 'state'
        (self.extension / 'a.js').write_bytes(b'original')
        self.plan = {'a.js': b'patched', 'new.js': b'new'}

    def test_apply_restore_exact_bytes_and_idempotency(self):
        installer.apply(self.extension, self.plan, self.state)
        installer.apply(self.extension, self.plan, self.state)
        self.assertEqual((self.extension / 'a.js').read_bytes(), b'patched')
        installer.restore(self.extension, self.state)
        self.assertEqual((self.extension / 'a.js').read_bytes(), b'original')
        self.assertFalse((self.extension / 'new.js').exists())
        installer.apply(self.extension, self.plan, self.state)
        installer.restore(self.extension, self.state)
        self.assertEqual((self.extension / 'a.js').read_bytes(), b'original')

    def test_restore_refuses_changed_file_without_partial_restore(self):
        installer.apply(self.extension, self.plan, self.state)
        (self.extension / 'new.js').write_bytes(b'user modification')
        with self.assertRaisesRegex(ValueError, 'File changed'):
            installer.restore(self.extension, self.state)
        self.assertEqual((self.extension / 'a.js').read_bytes(), b'patched')

    def test_restore_refuses_damaged_backup(self):
        installer.apply(self.extension, self.plan, self.state)
        (self.state / 'original/a.js').write_bytes(b'damaged')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            installer.restore(self.extension, self.state)
        self.assertEqual((self.extension / 'a.js').read_bytes(), b'patched')

    def test_write_failure_rolls_back_previous_writes(self):
        write = installer.atomic_write
        failed = False

        def fail_once(path, data):
            nonlocal failed
            if path.name == 'new.js' and not failed:
                failed = True
                raise OSError('simulated disk failure')
            return write(path, data)

        with patch.object(installer, 'atomic_write', fail_once):
            with self.assertRaises(OSError):
                installer.apply(self.extension, self.plan, self.state)
        self.assertEqual((self.extension / 'a.js').read_bytes(), b'original')
        self.assertFalse((self.extension / 'new.js').exists())
        self.assertFalse((self.state / 'record.json').exists())

    def test_unknown_version_is_rejected(self):
        (self.extension / 'package.json').write_text(json.dumps({
            'publisher':'openai', 'name':'chatgpt', 'version':'unknown',
        }))
        with self.assertRaisesRegex(ValueError, 'Supported version'):
            installer.select_extension(self.extension)

    def test_discover_uses_active_installation(self):
        root = self.root / '.vscode/extensions'
        root.mkdir(parents=True)
        for name in ['openai.chatgpt-old', 'openai.chatgpt-current']:
            (root / name).mkdir()
            (root / name / 'package.json').write_text('{}')
        (root / 'extensions.json').write_text(json.dumps([{
            'identifier':{'id':'openai.chatgpt'}, 'relativeLocation':'openai.chatgpt-current',
        }]))
        self.assertEqual(installer.discover(self.root), [(root / 'openai.chatgpt-current').resolve()])


if __name__ == '__main__':
    unittest.main()
