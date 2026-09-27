import test from 'node:test';
import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import { renderPortrait, runLauncher, summarizeRun } from '../src/odyssey-launcher.js';

test('Odysseus image renders as printable ASCII without terminal color', () => {
  const output = new PassThrough();
  let portrait = '';
  output.on('data', chunk => { portrait += chunk.toString(); });
  renderPortrait(output);
  assert.equal(portrait.trimEnd().split('\n').length, 28);
  assert.match(portrait, /[%@#]/);
  assert.doesNotMatch(portrait, /\x1b/);
});

test('Odyssey summarizes severity, blocking logic, and draft PR outcomes', () => {
  const result = summarizeRun([
    { type: 'repository', report: { repository: 'team/api', filesScanned: 8, incomplete: false, agent: {
      summary: 'Three fixes', findings: [
        { severity: 'critical', category: 'security', blocksProject: false },
        { severity: 'high', category: 'logic', blocksProject: true },
        { severity: 'medium', category: 'reliability', blocksProject: false }
      ]
    }, pullRequests: [{ url: 'https://github.com/team/api/pull/1' }, { url: 'https://github.com/team/api/pull/2', existing: true }, { error: 'syntax check failed' }] } }
  ]);
  assert.deepEqual([result.critical, result.high, result.medium, result.logical, result.blockingLogic], [1, 1, 1, 1, 1]);
  assert.equal(result.pulls.length, 3);
});

test('Odyssey accepts repo URL, skips blank live URL, and displays a mission report', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  let screen = '';
  let received;
  output.on('data', chunk => { screen += chunk.toString(); });
  const running = runLauncher({ input, output, run: async (args, options) => {
    received = args;
    options.progress('Loading GitHub source');
    options.log('[12:00:00] Source 1/1: app.py');
    options.output(JSON.stringify({ type: 'repository', report: {
      repository: 'team/api', filesScanned: 1, incomplete: false,
      agent: { summary: 'One fix', findings: [{ title: 'Startup fails', severity: 'medium', category: 'logic', blocksProject: true, filePath: 'app.py', line: 4 }] },
      pullRequests: [{ url: 'https://github.com/team/api/pull/3' }]
    } }));
  } });
  input.write('https://github.com/team/api\n');
  await new Promise(resolve => setImmediate(resolve));
  input.write('\n');
  const result = await running;
  assert.deepEqual(received, ['inspect', 'https://github.com/team/api', '--verbose', '--json']);
  assert.equal(result.medium, 1);
  assert.match(screen, /ODYSSEY  \/  API DOCTOR/);
  assert.match(screen, /Source 1\/1: app.py/);
  assert.doesNotMatch(screen, /\.\-=========\-\./);
  assert.match(screen, /Blocking logic: 1/);
  assert.match(screen, /pull\/3/);
});
