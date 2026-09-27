import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { diagnose, proposePatch } from '../src/diagnose.js';
import { startApp } from '../src/server.js';
import { createFixPullRequest } from '../src/github.js';

test('Node and Python traces identify the cause and produce reviewable demo patches', async () => {
  const jsSource = await readFile(new URL('../demo/target-api.js', import.meta.url), 'utf8');
  const jsLine = jsSource.split('\n').findIndex(line => line.includes('student.progress.completed')) + 1;
  const nodeLog = `TypeError: Cannot read properties of undefined (reading 'completed')\n    at getStudentProgress (/app/target-api.js:${jsLine}:62)`;
  const nodeDiagnosis = diagnose(nodeLog);
  assert.equal(nodeDiagnosis.kind, 'null-property');
  assert.equal(nodeDiagnosis.location.line, jsLine);
  assert.match(proposePatch(nodeDiagnosis, jsSource, jsLine).after, /progress\?\.completed \?\? 0/);

  const pySource = await readFile(new URL('../demo/python-api.py', import.meta.url), 'utf8');
  const pyLog = 'Traceback (most recent call last):\n  File "/app/main.py", line 2, in get_progress\n    return {"completed": student["progress"]["completed"]}\nKeyError: \'progress\'';
  const pyDiagnosis = diagnose(pyLog);
  assert.equal(pyDiagnosis.kind, 'python-missing-value');
  assert.equal(pyDiagnosis.location.line, 2);
  assert.match(proposePatch(pyDiagnosis, pySource, 2).after, /student\.get\("progress"\)/);
});

test('HTTP demo catches the crash and exposes Node and Python incidents', async () => {
  const app = await startApp({ port: 0, demoPort: 0 });
  const base = `http://127.0.0.1:${app.address.port}`;
  try {
    const check = await fetch(`${base}/api/check`, { method: 'POST' });
    assert.equal(check.status, 200);
    const checkResult = await check.json();
    assert.equal(checkResult.results[0].healthy, false);
    assert.equal(checkResult.results[0].statusCode, 500);

    const python = await fetch(`${base}/api/demo/python`, { method: 'POST' });
    assert.equal(python.status, 202);

    const state = await (await fetch(`${base}/api/state`)).json();
    assert.equal(state.incidents.length, 2);
    assert.ok(state.incidents.every(incident => incident.patch && incident.state === 'patch-ready'));
    assert.deepEqual(new Set(state.incidents.map(incident => incident.diagnosis.kind)), new Set(['null-property', 'python-missing-value']));
  } finally {
    await app.close();
  }
});

test('draft PR flow creates a branch, writes the repair, and opens a reviewable PR', async () => {
  const source = await readFile(new URL('../demo/target-api.js', import.meta.url), 'utf8');
  const line = source.split('\n').findIndex(item => item.includes('student.progress.completed')) + 1;
  const diagnosis = diagnose(`TypeError: Cannot read properties of undefined (reading 'completed')\n    at getStudentProgress (/app/target-api.js:${line}:62)`);
  const requests = [];
  const replies = [
    { default_branch: 'main' },
    { type: 'file', size: source.length, encoding: 'base64', content: Buffer.from(source).toString('base64'), sha: 'file-sha' },
    { object: { sha: 'base-sha' } },
    { ref: 'refs/heads/api-doctor/fix-123' },
    { content: { sha: 'new-sha' } },
    { html_url: 'https://github.com/team/repo/pull/7', number: 7 }
  ];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    requests.push({ url, method: options.method, body: options.body ? JSON.parse(options.body) : null });
    return new Response(JSON.stringify(replies.shift()), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  try {
    const result = await createFixPullRequest({ token: 'test-token', repository: 'team/repo', filePath: 'demo/target-api.js', diagnosis, id: '123' });
    assert.equal(result.url, 'https://github.com/team/repo/pull/7');
    assert.equal(requests.length, 6);
    assert.equal(requests[3].body.ref, 'refs/heads/api-doctor/fix-123');
    assert.match(Buffer.from(requests[4].body.content, 'base64').toString('utf8'), /progress\?\.completed \?\? 0/);
    assert.equal(requests[5].body.draft, true);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('diagnostic evidence redacts common credential formats', () => {
  const diagnosis = diagnose('Request failed api_key=abc123 Bearer top-secret');
  assert.doesNotMatch(diagnosis.evidence, /abc123|top-secret/);
});
