#!/usr/bin/env python3
"""Round-trip a dedicated ChatGPT Codex session through setec; never log tokens."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit


class AuthError(RuntimeError):
    pass


def validate_auth(data):
    try:
        value = json.loads(data)
        tokens = value['tokens']
        if value.get('auth_mode') != 'chatgpt' or value.get('OPENAI_API_KEY'):
            raise ValueError()
        if not all(isinstance(tokens.get(key), str) and tokens[key]
                   for key in ('access_token', 'id_token', 'refresh_token')):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise AuthError('Expected a dedicated ChatGPT login with refresh credentials.') from None
    return value


def mask_auth(value):
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        for token in value['tokens'].values():
            if isinstance(token, str) and token:
                escaped = token.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
                print('::add-mask::' + escaped, flush=True)


def request(server, method, payload):
    url = urlsplit(server)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in ('', '/')):
        raise AuthError('setec must be an HTTPS origin.')
    config = ['silent', 'show-error', 'max-time = 45', 'connect-timeout = 10',
              'noproxy = "*"', 'request = "POST"',
              'url = ' + json.dumps(server.rstrip('/') + '/api/' + method),
              'header = "Content-Type: application/json"',
              'header = "Sec-X-Tailscale-No-Browsers: setec"',
              'write-out = "\\n%{http_code}"',
              'data-binary = ' + json.dumps(json.dumps(payload))]
    try:
        result = subprocess.run(['curl', '--config', '-'], input='\n'.join(config) + '\n',
                                text=True, capture_output=True, timeout=50)
        body, _, status = result.stdout.rpartition('\n')
        if result.returncode or status != '200':
            raise AuthError('setec ' + method + ' failed (HTTP ' + status + ').')
        return json.loads(body)
    except (OSError, subprocess.SubprocessError, ValueError):
        raise AuthError('Cannot complete the setec request; no credentials printed.') from None


def private_write(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)


def restore(server, name, home):
    if home.exists():
        raise AuthError('Use a fresh private CODEX_HOME for each job.')
    response = request(server, 'get', {'Name': name})
    try:
        data = base64.b64decode(response['Value'], validate=True)
        auth = validate_auth(data)
        version = int(response['Version'])
    except (KeyError, ValueError, TypeError):
        raise AuthError('Invalid setec auth response.') from None
    mask_auth(auth)
    home.mkdir(parents=True, mode=0o700)
    private_write(home / 'auth.json', data)
    private_write(home / '.setec-state.json', json.dumps({
        'version': version, 'sha256': hashlib.sha256(data).hexdigest(), 'name': name,
    }).encode())
    private_write(home / 'config.toml',
                  b'forced_login_method = "chatgpt"\ncli_auth_credentials_store = "file"\n')
    print('Restored dedicated ChatGPT session from setec; API key authentication disabled.')


def persist(server, name, home):
    baseline = home / '.setec-state.json'
    if not baseline.exists():
        return
    state = json.loads(baseline.read_text())
    if state['name'] != name:
        raise AuthError('Refusing to save credentials to a different secret.')
    data = (home / 'auth.json').read_bytes()
    auth = validate_auth(data)
    mask_auth(auth)
    if hashlib.sha256(data).hexdigest() == state['sha256']:
        print('Session unchanged; setec remains current.')
        return
    current = request(server, 'get', {'Name': name})
    if current['Version'] != state['version']:
        raise AuthError('Session changed in setec during this job; refusing stale overwrite.')
    version = request(server, 'put', {'Name': name, 'Value': base64.b64encode(data).decode()})
    request(server, 'activate', {'Name': name, 'Version': version})
    print('Saved refreshed ChatGPT session to setec.')


def run_codex(home, workspace, prompt, output, sandbox='workspace-write', schema=None):
    auth = validate_auth((home / 'auth.json').read_bytes())
    mask_auth(auth)
    environment = {k: v for k, v in os.environ.items() if k not in (
        'OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN', 'GH_TOKEN', 'GITHUB_TOKEN')}
    environment['CODEX_HOME'] = str(home)
    command = ['codex', 'exec', '--ephemeral', '--sandbox', sandbox, '--color', 'never',
               '--cd', str(workspace), '-o', str(output)]
    if schema:
        command.extend(['--output-schema', str(schema)])
    command.append('-')
    return subprocess.run(command, input=prompt, text=True, env=environment, timeout=1200).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['restore', 'persist', 'smoke', 'adapt', 'clean'])
    parser.add_argument('--server', default=os.environ.get('SETEC_SERVER'))
    parser.add_argument('--name', default=os.environ.get('SETEC_CODEX_AUTH_NAME'))
    parser.add_argument('--home', type=Path, default=os.environ.get('CODEX_HOME'))
    parser.add_argument('--workspace', type=Path, default=Path.cwd())
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if not args.home or not args.home.is_absolute():
            raise AuthError('An absolute private CODEX_HOME is required.')
        if args.mode in ('restore', 'persist') and (not args.server or not args.name):
            raise AuthError('SETEC_SERVER and SETEC_CODEX_AUTH_NAME are required.')
        if args.mode == 'restore':
            restore(args.server, args.name, args.home)
        elif args.mode == 'persist':
            persist(args.server, args.name, args.home)
        elif args.mode == 'clean':
            for name in ('auth.json', '.setec-state.json'):
                (args.home / name).unlink(missing_ok=True)
        else:
            if not args.output:
                raise AuthError('--output is required.')
            if args.mode == 'smoke':
                prompt = 'Reply with exactly CODEX_SUBSCRIPTION_OK. Do not use tools.'
                result = run_codex(args.home, args.workspace, prompt, args.output, 'read-only')
                if result or args.output.read_text().strip() != 'CODEX_SUBSCRIPTION_OK':
                    raise AuthError('The Codex subscription smoke check failed.')
                print('Subscription smoke check passed.')
            else:
                prompt = (Path(__file__).parent / 'prompts/adapt.md').read_text()
                result = run_codex(args.home, args.workspace, prompt, args.output,
                                   schema=Path(__file__).parent / 'adaptation.schema.json')
                if result:
                    return result
                report = json.loads(args.output.read_text())
                if report.get('status') not in ('verified', 'adapted') or not report.get('checks'):
                    raise AuthError('Codex could not complete required checks; adaptation is blocked.')
                print('Codex adaptation result:', report['status'])
        return 0
    except (AuthError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        # Exception payloads from credentials/remote responses must never enter logs.
        print(str(error) if isinstance(error, AuthError) else 'Subscription operation failed; credentials not printed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
