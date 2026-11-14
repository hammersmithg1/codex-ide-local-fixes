// Print only public identity claims for diagnosing federation rules, never the JWT.
try {
  const url = new URL(process.env.ACTIONS_ID_TOKEN_REQUEST_URL);
  url.searchParams.set('audience', process.env.TS_CI_AUDIENCE);
  const response = await fetch(url, {headers: {
    Authorization: `Bearer ${process.env.ACTIONS_ID_TOKEN_REQUEST_TOKEN}`,
  }});
  if (!response.ok) throw new Error();
  const {value} = await response.json();
  const claims = JSON.parse(Buffer.from(value.split('.')[1], 'base64url').toString());
  const fields = ['iss', 'aud', 'sub', 'repository', 'repository_owner', 'ref',
                  'event_name', 'repository_visibility', 'workflow_ref'];
  console.log(JSON.stringify(Object.fromEntries(fields.map(key => [key, claims[key]])), null, 2));
} catch {
  console.error('Could not inspect public OIDC identity claims; token not printed.');
  process.exitCode = 1;
}
