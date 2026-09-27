import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { readFile, mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { parseArgs, probeLiveApi, runCli } from '../src/cli.js';
import { analyzeRepositoryFiles, parseRepositoryUrl, validateLiveUrl } from '../src/repository.js';
import { startDemoApi } from '../demo/target-api.js';
import { findCodexExecutable, reviewWithCodex } from '../src/codex.js';
import { createFindingPullRequest } from '../src/github.js';

test('CLI accepts repo-only and repo plus live URL, and rejects unsafe URLs', () => {
  assert.equal(parseRepositoryUrl('https://github.com/team/campus-api.git'), 'team/campus-api');
  assert.equal(parseArgs(['inspect', 'https://github.com/team/campus-api']).liveUrl, null);
  assert.equal(parseArgs(['inspect', 'https://github.com/team/campus-api']).noAi, false);
  assert.equal(parseArgs(['inspect', 'https://github.com/team/campus-api', '--dry-run']).dryRun, true);
  assert.equal(parseArgs(['inspect', 'https://github.com/team/campus-api', '--verbose']).verbose, true);
  assert.equal(parseArgs(['inspect', 'https://github.com/team/campus-api', '--live', 'https://api.example.com/health']).liveUrl, 'https://api.example.com/health');
  const pasted = parseArgs(['inspect', '[https://github.com/team/campus-api](https://github.com/team/campus-api)', '--', 'live', 'https\\://api.example.com/health']);
  assert.equal(pasted.repository, 'team/campus-api');
  assert.equal(pasted.liveUrl, 'https://api.example.com/health');
  assert.throws(() => parseRepositoryUrl('[https://github.com/team/api](https://github.com/other/api)'), /GitHub repository URL/);
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

test('desktop launcher finds the versioned Codex app executable without PATH', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'odyssey-codex-'));
  try {
    const binary = path.join(root, 'OpenAI', 'Codex', 'bin', 'version-id', 'codex.exe');
    await mkdir(path.dirname(binary), { recursive: true });
    await writeFile(binary, 'stub');
    assert.equal(findCodexExecutable({ env: { LOCALAPPDATA: root, Path: '' }, platform: 'win32' }), binary);
  } finally {
    if (!path.resolve(root).startsWith(`${path.resolve(tmpdir())}${path.sep}`)) throw new Error('Refusing to remove a temp directory outside the system temp folder');
    await rm(root, { recursive: true, force: true });
  }
});

test('repo-only Luna output keeps critical, high, medium, and blocking logic in scanned files', async () => {
  const calls = [];
  const logs = [];
  const findings = [
    { title: 'Critical injection', filePath: 'app.js', severity: 'critical', original: 'eval(x)', replacement: 'String(x)' },
    { title: 'High issue', filePath: 'app.js', severity: 'high', original: 'eval(x)', replacement: 'String(x)' },
    { title: 'Medium issue', filePath: 'app.js', severity: 'medium', original: 'eval(x)', replacement: 'String(x)' },
    { title: 'Startup failure', filePath: 'app.js', severity: 'low', category: 'logic', blocksProject: true, original: 'eval(x)', replacement: 'String(x)' },
    { title: 'Low edge case', filePath: 'app.js', severity: 'low', category: 'logic', blocksProject: false, original: 'eval(x)', replacement: 'String(x)' },
    { title: 'Outside scan', filePath: 'other.js', severity: 'critical', original: 'eval(x)', replacement: 'String(x)' }
  ];
  const run = async (args, input) => {
    calls.push({ args, input });
    return calls.length === 1
      ? { code: 0, stdout: 'Logged in using ChatGPT', stderr: '' }
      : { code: 0, stdout: JSON.stringify({ summary: 'Four findings', findings }), stderr: '' };
  };
  const analysis = await reviewWithCodex({ repository: 'team/api', filePaths: ['app.js'], sourceFiles: [{ path: 'app.js', content: 'eval(x)' }] }, { run, onLog: line => logs.push(line) });
  assert.deepEqual(analysis.findings.map(finding => finding.title), ['Critical injection', 'High issue', 'Medium issue', 'Startup failure']);
  assert.match(calls[1].input, /critical, high, or medium/);
  assert.match(calls[1].input, /blocksProject true only/);
  assert.ok(calls[1].args.includes('gpt-6-luna'));
  assert.ok(logs.some(line => /starting GPT-6 Luna Fast/.test(line)));
  assert.ok(logs.some(line => /passed eligibility/.test(line)));
});

test('repo-only and repo plus live URL work without a pasted stack trace', async () => {
  const source = await readFile(new URL('../demo/target-api.js', import.meta.url), 'utf8');
  const originalFetch = globalThis.fetch;
  const demoServer = await startDemoApi(0);
  const demoUrl = `http://127.0.0.1:${demoServer.address().port}/api/students/42/progress`;
  const output = [];
  const logs = [];
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
    await runCli(['inspect', 'https://github.com/team/campus-api', '--no-ai', '--verbose', '--json'], { output: line => output.push(JSON.parse(line)), log: line => logs.push(line) });
    assert.equal(output.length, 1);
    assert.equal(output[0].report.findings[0].title, 'Possible missing progress crash');
    assert.ok(logs.some(line => /Source 1\/1: demo\/target-api.js/.test(line)));
    assert.ok(logs.some(line => /Repository scan complete/.test(line)));

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
  const logs = [];
  const source = "exports.run = (req, res) => {\n  const value = req.body.value;\n  if (typeof value !== 'string') return res.sendStatus(400);\n  return res.send(eval(value));\n};\n";
  const finding = {
    title: 'Remote code execution through eval', detail: 'Untrusted query input reaches eval.',
    filePath: 'app.js', line: 4, severity: 'critical', impact: 'An attacker can run server code.',
    original: source.slice(0, -1), replacement: source.slice(0, -1).replace('eval(value)', 'String(value)'), testPlan: 'Verify /run returns text without evaluation.'
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
    const pr = await createFindingPullRequest({ token: 'fake-token', repository: 'team/api', finding, onLog: line => logs.push(line) });
    assert.equal(pr.url, 'https://github.com/team/api/pull/7');
    assert.equal(pr.fork, true);
    const fileWrite = calls.find(call => call.method === 'PUT');
    assert.equal(Buffer.from(fileWrite.body.content, 'base64').toString(), source.replace(finding.original, finding.replacement));
    const request = calls.find(call => call.pathname === '/repos/team/api/pulls' && call.method === 'POST');
    assert.equal(request.body.draft, true);
    assert.match(request.body.head, /^doctor:api-doctor\/repository-/);
    assert.equal(request.body.base, 'main');
    assert.ok(logs.some(line => /validating the exact source edit and syntax/.test(line)));
    assert.ok(logs.some(line => /draft opened/.test(line)));
  } finally { globalThis.fetch = originalFetch; }
});

test('a low-severity startup-blocking logic bug can create a draft PR', async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  const source = "const port = config.server.port.trim();\napp.listen(port);\n";
  globalThis.fetch = async (url, options) => {
    const pathname = new URL(url).pathname;
    calls.push({ method: options.method, pathname, body: options.body ? JSON.parse(options.body) : null });
    const data = pathname === '/repos/team/api' && options.method === 'GET'
      ? { default_branch: 'main', permissions: { push: true } }
      : pathname === '/repos/team/api/contents/app.js'
        ? { type: 'file', size: source.length, encoding: 'base64', content: Buffer.from(source).toString('base64'), sha: 'file-sha' }
        : pathname === '/repos/team/api/git/ref/heads/main'
          ? { object: { sha: 'base-sha' } }
          : pathname === '/repos/team/api/pulls' && options.method === 'POST'
            ? { html_url: 'https://github.com/team/api/pull/9', number: 9 }
            : [];
    return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  try {
    const finding = {
      title: 'Missing config prevents startup', detail: 'Missing server port causes a startup exception.',
      severity: 'low', category: 'logic', blocksProject: true, filePath: 'app.js', line: 1, impact: 'The API cannot start.',
      original: 'config.server.port.trim()', replacement: "String(config.server?.port ?? 3000).trim()", testPlan: 'Start the API without server.port.'
    };
    const pr = await createFindingPullRequest({ token: 'fake-token', repository: 'team/api', finding });
    assert.equal(pr.url, 'https://github.com/team/api/pull/9');
    const request = calls.find(call => call.pathname === '/repos/team/api/pulls' && call.method === 'POST');
    assert.match(request.body.body, /\*\*Severity:\*\* low/);
    assert.equal(request.body.draft, true);
    assert.ok(!calls.some(call => call.pathname === '/repos/team/api/forks'));
  } finally { globalThis.fetch = originalFetch; }
});

test('ordinary low-severity findings are rejected before any GitHub request', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => { calls++; throw new Error('GitHub must not be called'); };
  try {
    await assert.rejects(() => createFindingPullRequest({
      token: 'fake-token', repository: 'team/api',
      finding: { title: 'Missing name affects one request', severity: 'low', category: 'logic', blocksProject: false, filePath: 'app.js' }
    }), /below the PR threshold/);
    assert.equal(calls, 0);
  } finally { globalThis.fetch = originalFetch; }
});

test('a repeated finding reuses an open API Doctor PR', async () => {
  const originalFetch = globalThis.fetch;
  const methods = [];
  globalThis.fetch = async (url, options) => {
    methods.push(options.method);
    const pathname = new URL(url).pathname;
    const data = pathname === '/repos/team/api'
      ? { default_branch: 'main' }
      : [{ title: '[API Doctor] Existing fix', body: '**Source:** `app.js:4`', html_url: 'https://github.com/team/api/pull/8', number: 8 }];
    return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  try {
    const pr = await createFindingPullRequest({ token: 'fake-token', repository: 'team/api', finding: { title: 'Finding', filePath: 'app.js', line: 4, severity: 'critical', original: 'eval(value)', replacement: 'String(value)' } });
    assert.equal(pr.existing, true);
    assert.equal(pr.url, 'https://github.com/team/api/pull/8');
    assert.deepEqual(methods, ['GET', 'GET']);
  } finally { globalThis.fetch = originalFetch; }
});
