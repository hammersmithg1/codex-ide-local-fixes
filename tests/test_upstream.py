import importlib.util
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

spec = importlib.util.spec_from_file_location('upstream', Path(__file__).parents[1] / 'ci/upstream.py')
upstream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upstream)


class UpstreamTests(unittest.TestCase):
    def test_latest_selects_stable_version_for_requested_platform(self):
        versions = [
            {'version':'26.2.10','targetPlatform':'darwin-arm64'},
            {'version':'26.2.11','targetPlatform':'darwin-arm64','properties':[
                {'key':'Microsoft.VisualStudio.Code.PreRelease','value':'true'}]},
            {'version':'26.3.1','targetPlatform':'linux-x64'},
            {'version':'26.2.9','targetPlatform':'darwin-arm64'},
        ]
        data = {'results':[{'extensions':[{'extensionName':'chatgpt',
                'publisher':{'publisherName':'openai'},'versions':versions}]}]}
        with patch.object(upstream.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(data).encode())):
            self.assertEqual(upstream.latest('darwin-arm64'), '26.2.10')

    def test_version_cannot_be_a_path_or_command(self):
        for version in ['../26.2.10','26.2.10;id','26.2.10\nother']:
            with self.assertRaises(ValueError):
                upstream.valid_version(version)

    def test_modified_vsix_has_own_identity_and_preserves_notices(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root/'upstream'
            (target/'patched/out').mkdir(parents=True)
            (target/'patched/webview/assets').mkdir(parents=True)
            package = {'name':'chatgpt','publisher':'openai','version':'26.2.10','__metadata':{'id':'upstream-id'}}
            (target/'patched/package.json').write_text(json.dumps(package))
            (target/'patched/out/extension.js').write_text('const id="openai.chatgpt";')
            (target/'patched/LICENSE.md').write_text('Original notice retained.\n')
            (target/'patched/webview/assets/codex-local-bootstrap.js').write_text('// local helper')
            (target/'unpacked').mkdir()
            manifest = ('<PackageManifest xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">'
                        '<Metadata><Identity Id="chatgpt" Publisher="openai" Version="26.2.10"/>'
                        '<DisplayName>Codex</DisplayName></Metadata></PackageManifest>')
            (target/'unpacked/extension.vsixmanifest').write_text(manifest)
            with zipfile.ZipFile(target/'upstream.vsix','w') as archive:
                archive.writestr('extension.vsixmanifest',manifest)
                archive.writestr('extension/package.json',json.dumps(package))
                archive.writestr('extension/out/extension.js','upstream')
                archive.writestr('extension/LICENSE.md','Original notice retained.\n')
                archive.writestr('.signature.p7s','signature of unmodified upstream')
            result = upstream.package(target,'darwin-arm64',root/'output')
            with zipfile.ZipFile(result) as archive:
                package = json.loads(archive.read('extension/package.json'))
                self.assertEqual(package['publisher']+'.'+package['name'],upstream.LOCAL_ID)
                self.assertNotIn('__metadata',package)
                self.assertNotIn('.signature.p7s',archive.namelist())
                self.assertIn('extension/webview/assets/codex-local-bootstrap.js',archive.namelist())
                self.assertEqual(archive.read('extension/LICENSE.md'),b'Original notice retained.\n')
                identity=next(e for e in ET.fromstring(archive.read('extension.vsixmanifest')).iter()
                              if e.tag.split('}')[-1]=='Identity')
                self.assertEqual(identity.attrib['Id'],'codex-local-fixes')
                self.assertEqual(identity.attrib['Publisher'],'hammersmithg1')
                self.assertEqual(identity.attrib['TargetPlatform'],'darwin-arm64')
                manifest_bytes=archive.read('extension.vsixmanifest')
                self.assertIn(b'<PackageManifest ',manifest_bytes)
                self.assertIn(b'<Metadata>',manifest_bytes)
                self.assertIn(b'<Identity ',manifest_bytes)
                self.assertNotIn(b'<ns0:',manifest_bytes)
                self.assertNotIn(b'openai.chatgpt',archive.read('extension/out/extension.js'))

    def test_archive_paths_are_checked_before_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def download(command, **kwargs):
                archive_path=Path(command[command.index('--output')+1])
                with zipfile.ZipFile(archive_path,'w') as archive:
                    archive.writestr('../../escaped','bad')
            with patch.object(upstream.subprocess,'run',side_effect=download):
                with self.assertRaisesRegex(ValueError,'Unsafe archive'):
                    upstream.fetch('26.2.10','darwin-arm64',root/'upstream')
            self.assertFalse((root/'escaped').exists())

    def test_gzipped_marketplace_response_is_decoded_before_opening_vsix(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            body = io.BytesIO()
            with zipfile.ZipFile(body,'w') as archive:
                archive.writestr('extension/package.json',json.dumps({
                    'name':'chatgpt','publisher':'openai','version':'26.2.10'}))
            def download(command, **kwargs):
                Path(command[command.index('--output')+1]).write_bytes(gzip.compress(body.getvalue()))
            with patch.object(upstream.subprocess,'run',side_effect=download):
                upstream.fetch('26.2.10','darwin-arm64',root/'upstream')
            self.assertTrue(zipfile.is_zipfile(root/'upstream/upstream.vsix'))
            self.assertTrue((root/'upstream/unpacked/extension/package.json').is_file())


if __name__ == '__main__':
    unittest.main()
