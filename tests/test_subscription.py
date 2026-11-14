import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('subscription', Path(__file__).parents[1] / 'ci/subscription.py')
subscription = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subscription)


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'auth'
        self.auth = {'auth_mode': 'chatgpt', 'tokens': {
            'access_token': 'test-access', 'id_token': 'test-id', 'refresh_token': 'test-refresh'},
            'OPENAI_API_KEY': None}
        self.data = json.dumps(self.auth).encode()
        self.response = {'Value': base64.b64encode(self.data).decode(), 'Version': 7}

    def restore(self):
        with patch.object(subscription, 'request', return_value=self.response):
            subscription.restore('https://secrets.example', 'ci/auth', self.home)

    def test_subscription_only_and_private_files(self):
        self.restore()
        self.assertEqual((self.home/'auth.json').read_bytes(), self.data)
        if os.name != 'nt':
            self.assertEqual((self.home/'auth.json').stat().st_mode & 0o777, 0o600)
        with self.assertRaises(subscription.AuthError):
            subscription.validate_auth(json.dumps({**self.auth, 'auth_mode':'apikey'}))
        with self.assertRaises(subscription.AuthError):
            subscription.validate_auth(json.dumps({**self.auth, 'OPENAI_API_KEY':'test-key'}))

    def test_no_write_when_session_unchanged(self):
        self.restore()
        with patch.object(subscription, 'request') as request:
            subscription.persist('https://secrets.example', 'ci/auth', self.home)
            request.assert_not_called()

    def test_refreshed_session_is_written_back(self):
        self.restore()
        self.auth['tokens']['refresh_token'] = 'test-rotated'
        (self.home/'auth.json').write_text(json.dumps(self.auth))
        with patch.object(subscription, 'request', side_effect=[self.response,8,None]) as request:
            subscription.persist('https://secrets.example', 'ci/auth', self.home)
            self.assertEqual([call.args[1] for call in request.call_args_list], ['get','put','activate'])
            payload = request.call_args_list[1].args[2]
            self.assertEqual(json.loads(base64.b64decode(payload['Value'])), self.auth)

    def test_external_reseed_is_not_overwritten(self):
        self.restore()
        self.auth['tokens']['refresh_token'] = 'test-rotated'
        (self.home/'auth.json').write_text(json.dumps(self.auth))
        with patch.object(subscription, 'request', return_value={**self.response,'Version':9}) as request:
            with self.assertRaises(subscription.AuthError):
                subscription.persist('https://secrets.example', 'ci/auth', self.home)
            self.assertEqual(request.call_count, 1)

    def test_bad_origin_fails_before_network(self):
        for origin in ['http://secrets.example', 'https://user:pass@secrets.example', 'https://secrets.example/?key=bad']:
            with patch.object(subscription.subprocess, 'run') as run:
                with self.assertRaises(subscription.AuthError):
                    subscription.request(origin, 'get', {})
                run.assert_not_called()

    def test_run_removes_api_keys_and_github_token(self):
        self.restore()
        with patch.dict(os.environ, {'OPENAI_API_KEY':'test-key','CODEX_API_KEY':'test-key','GH_TOKEN':'test-gh'}):
            with patch.object(subscription.subprocess, 'run') as run:
                run.return_value.returncode = 0
                subscription.run_codex(self.home, Path.cwd(), 'test', Path('output.txt'))
                environment = run.call_args.kwargs['env']
                self.assertFalse(any(key in environment for key in ['OPENAI_API_KEY','CODEX_API_KEY','GH_TOKEN']))
                self.assertEqual(environment['CODEX_HOME'], str(self.home))


if __name__ == '__main__':
    unittest.main()
