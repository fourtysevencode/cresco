import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { parseArgs, probeLiveApi, runCli } from '../src/cli.js';
import { analyzeRepositoryFiles, parseRepositoryUrl, validateLiveUrl } from '../src/repository.js';
import { startDemoApi } from '../demo/target-api.js';
import { reviewWithCodex } from '../src/codex.js';
import { createFindingPullRequest } from '../src/github.js';

test('CLI accepts repo-only and repo plus live URL, and rejects unsafe URLs', () => {
  assert.equal(parseRepositoryUrl('https://github.com/team/campus-api.git'), 'team/campus-api');
  assert.equal(parseArgs(['inspect', 'https://github.com/team/campus-api']).liveUrl, null);
  assert.equal(parseArgs(['inspect', 'https://github.com/team/campus-api']).noAi, false);
  assert.equal(parseArgs(['inspect', 'https://github.com/team/campus-api', '--dry-run']).dryRun, true);
  assert.equal(parseArgs(['inspect', 'https://github.com/team/campus-api', '--live', 'https://api.example.com/health']).liveUrl, 'https://api.example.com/health');
  assert.throws(() => parseArgs(['inspect', 'https://github.com/team/campus-api', '--watch']), /requires --live/);
  assert.throws(() => parseRepositoryUrl('https://github.com/team/repo/tree/main'), /GitHub URL/);
  assert.throws(() => validateLiveUrl('http://127.0.0.1:4101/api'), /public HTTPS/);
  assert.throws(() => validateLiveUrl('https://api.example.com/health?token=secret'), /Invalid live API URL/);
  assert.equal(validateLiveUrl('http://127.0.0.1:4101/api', true), 'http://127.0.0.1:4101/api');
});

test('source investigation finds endpoints and labels likely faults as unconfirmed', () => {
  const report = analyzeRepositoryFiles('team/api', [
    { path: 'src/app.js', content: "const student = { progress: undefined };\napp.get('/health', () => student.progress.completed);" },
    { path: 'main.py', content: '@app.get("/students")\ndef students():\n    return student["progress"]["completed"]' }
  ]);
  assert.deepEqual(report.routes.map(route => route.path), ['/health', '/students']);
  assert.equal(report.findings.length, 2);
  assert.match(report.conclusion, /Potential issues/);
  assert.deepEqual(report.filePaths, ['src/app.js', 'main.py']);
});

test('Luna review refuses API-key or missing CLI auth before making a model call', async () => {
  let calls = 0;
  const run = async () => { calls++; return { code: 0, stdout: 'Logged in using API key', stderr: '' }; };
  await assert.rejects(() => reviewWithCodex({ repository: 'team/api', sourceFiles: [] }, { run }), /not signed in with ChatGPT/);
  assert.equal(calls, 1);
});

test('repo-only and repo plus live URL work without a pasted stack trace', async () => {
  const source = await readFile(new URL('../demo/target-api.js', import.meta.url), 'utf8');
  const originalFetch = globalThis.fetch;
  const demoServer = await startDemoApi(0);
  const demoUrl = `http://127.0.0.1:${demoServer.address().port}/api/students/42/progress`;
  const output = [];
  globalThis.fetch = async (url, options) => {
    if (!String(url).startsWith('https://api.github.com/')) return originalFetch(url, options);
    const pathname = new URL(url).pathname;
    let body;
    if (pathname === '/repos/team/campus-api') body = { default_branch: 'main' };
    else if (pathname.endsWith('/git/trees/main')) body = { tree: [{ type: 'blob', path: 'demo/target-api.js', size: source.length }] };
    else body = { type: 'file', size: source.length, encoding: 'base64', content: Buffer.from(source).toString('base64'), sha: 'sha' };
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  try {
    await runCli(['inspect', 'https://github.com/team/campus-api', '--no-ai', '--json'], { output: line => output.push(JSON.parse(line)) });
    assert.equal(output.length, 1);
    assert.equal(output[0].report.findings[0].title, 'Possible missing progress crash');

    output.length = 0;
    await runCli(['inspect', 'https://github.com/team/campus-api', '--live', demoUrl, '--allow-local', '--no-ai', '--json'], { output: line => output.push(JSON.parse(line)) });
    assert.deepEqual(output.map(item => item.type), ['repository', 'probe']);
    assert.equal(output[1].result.statusCode, 500);
    assert.equal(output[1].result.diagnosis.kind, 'null-property');
    assert.equal(output[1].result.filePath, 'demo/target-api.js');
    assert.match(output[1].result.patch.after, /progress\?\.completed/);
  } finally {
    globalThis.fetch = originalFetch;
    await new Promise(resolve => demoServer.close(resolve));
  }
});

test('one-command demo detects its own live crash and previews the repair', async () => {
  const output = [];
  await runCli(['demo', '--json'], { output: line => output.push(JSON.parse(line)) });
  assert.deepEqual(output.map(item => item.type), ['repository', 'probe']);
  assert.equal(output[1].result.statusCode, 500);
  assert.match(output[1].result.patch.after, /progress\?\.completed/);
});

test('a live 500 without a traceback is reported as confirmed failure with unknown cause', async () => {
  const server = http.createServer((_request, response) => {
    response.writeHead(500, { 'Content-Type': 'text/plain' });
    response.end('Internal Server Error');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const result = await probeLiveApi(`http://127.0.0.1:${server.address().port}/health`, { repository: 'team/api', filePaths: [], routes: [], findings: [] });
    assert.equal(result.statusCode, 500);
    assert.match(result.diagnosis.rootCause, /did not expose enough information/);
    assert.equal(result.patch, undefined);
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
});

test('critical finding creates a real draft PR request from a fork branch', async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  const source = "app.get('/run', (req, res) => res.send(eval(req.query.code)));\n";
  const finding = {
    title: 'Remote code execution through eval', detail: 'Untrusted query input reaches eval.',
    filePath: 'app.js', line: 1, severity: 'critical', impact: 'An attacker can run server code.',
    original: 'eval(req.query.code)', replacement: 'String(req.query.code)', testPlan: 'Verify /run returns text without evaluation.'
  };
  globalThis.fetch = async (url, options) => {
    const pathname = new URL(url).pathname;
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ method: options.method, pathname, body });
    let data;
    if (pathname === '/repos/team/api' && options.method === 'GET') data = { default_branch: 'main', permissions: { push: false } };
    else if (pathname === '/repos/team/api/forks') data = { full_name: 'doctor/api', owner: { login: 'doctor' } };
    else if (pathname === '/repos/doctor/api/contents/app.js') data = { type: 'file', size: source.length, encoding: 'base64', content: Buffer.from(source).toString('base64'), sha: 'file-sha' };
    else if (pathname === '/repos/doctor/api/git/ref/heads/main') data = { object: { sha: 'base-sha' } };
    else if (pathname === '/repos/team/api/pulls') data = { html_url: 'https://github.com/team/api/pull/7', number: 7 };
    else data = {};
    return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  try {
    const pr = await createFindingPullRequest({ token: 'fake-token', repository: 'team/api', finding });
    assert.equal(pr.url, 'https://github.com/team/api/pull/7');
    assert.equal(pr.fork, true);
    const fileWrite = calls.find(call => call.method === 'PUT');
    assert.equal(Buffer.from(fileWrite.body.content, 'base64').toString(), source.replace(finding.original, finding.replacement));
    const request = calls.find(call => call.pathname === '/repos/team/api/pulls');
    assert.equal(request.body.draft, true);
    assert.match(request.body.head, /^doctor:api-doctor\/security-/);
    assert.equal(request.body.base, 'main');
  } finally { globalThis.fetch = originalFetch; }
});
