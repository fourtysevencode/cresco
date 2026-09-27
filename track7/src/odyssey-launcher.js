#!/usr/bin/env node
import { createInterface } from 'node:readline/promises';
import { stdin, stdout } from 'node:process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { runCli } from './cli.js';
import { parseRepositoryUrl, validateLiveUrl } from './repository.js';

const GOLD = '\x1b[38;2;214;169;91m';
const BLUE = '\x1b[38;2;123;184;198m';
const FAINT = '\x1b[2m';
const RESET = '\x1b[0m';

const ODYSSEUS = String.raw`
                         .-=========-.
                      .-'  _/|||\_  '-.
                    .'    /__|||__\    '.
                   /____.-'  |||  '-.____\
                       /  _  |||  _  \
                      |  /_\ ||| /_\  |
                      |   o  |||  o   |
                      |      / \      |
                       \   \_____/   /
                        '._       _.'
                           '--.--'
                           / /\ \
                     _____/ /  \ \_____
                    /______/    \______\
`;

function paint(value, color, output) {
  return output.isTTY && !process.env.NO_COLOR ? `${color}${value}${RESET}` : value;
}

export function renderBanner(output = stdout) {
  output.write(paint(ODYSSEUS, GOLD, output));
  output.write(paint('                          ODYSSEUS\n', GOLD, output));
  output.write(paint('             ODYSSEY  /  API DOCTOR\n', BLUE, output));
  output.write(paint('       Find the fault. Chart the fix. Bring it home.\n\n', FAINT, output));
}

function progressBar(output) {
  let stage = '';
  let frame = 0;
  let timer;
  const draw = () => {
    const width = 24;
    const start = frame++ % (width - 5);
    const bar = Array.from({ length: width }, (_, index) => index >= start && index < start + 6 ? '=' : '-').join('');
    output.write(`\r\x1b[2K  ${stage.padEnd(34)} [${bar}]`);
  };
  return {
    update(nextStage) {
      if (stage === nextStage) return;
      stage = nextStage;
      if (!output.isTTY) { output.write(`  ${stage}\n`); return; }
      if (!timer) {
        output.write('\x1b[?25l');
        timer = setInterval(draw, 120);
      }
      draw();
    },
    stop() {
      if (timer) {
        clearInterval(timer);
        output.write('\r\x1b[2K\x1b[?25h');
      }
    }
  };
}

export function summarizeRun(records) {
  const report = records.find(item => item.type === 'repository')?.report;
  const probe = records.find(item => item.type === 'probe')?.result;
  const analysis = probe?.agent ?? report?.agent;
  const findings = analysis?.findings ?? [];
  const pulls = probe?.pullRequests ?? report?.pullRequests ?? [];
  const count = severity => findings.filter(item => item.severity === severity).length;
  return {
    repository: report?.repository ?? '',
    filesScanned: report?.filesScanned ?? 0,
    incomplete: Boolean(report?.incomplete),
    liveStatus: probe?.statusCode ?? null,
    liveError: probe?.error ?? null,
    reviewed: Boolean(analysis),
    summary: analysis?.summary ?? '',
    findings,
    critical: count('critical'),
    high: count('high'),
    medium: count('medium'),
    blockingLogic: findings.filter(item => item.category === 'logic' && item.blocksProject === true).length,
    logical: findings.filter(item => item.category === 'logic').length,
    pulls
  };
}

function printSummary(result, output) {
  output.write(`\n${paint('MISSION REPORT', GOLD, output)}  ${result.repository}\n`);
  output.write(`  Source files inspected: ${result.filesScanned}${result.incomplete ? ' (partial scan)' : ''}\n`);
  if (result.liveStatus !== null) output.write(`  Live API: HTTP ${result.liveStatus}\n`);
  else if (result.liveError) output.write(`  Live API: ${result.liveError}\n`);
  if (!result.reviewed) {
    output.write('  Live API is healthy; Luna review and draft PRs were not needed.\n');
    return;
  }
  output.write(`  Eligible findings: ${result.findings.length}\n`);
  output.write(`  Critical: ${result.critical}  |  High/severe: ${result.high}  |  Medium: ${result.medium}\n`);
  output.write(`  Logical errors: ${result.logical}  |  Blocking logic: ${result.blockingLogic} (included in severity counts)\n`);
  if (result.summary) output.write(`\n  Luna: ${result.summary}\n`);
  for (const finding of result.findings) output.write(`  - [${finding.severity}] ${finding.title} (${finding.filePath}:${finding.line})\n`);
  const created = result.pulls.filter(pull => pull.url && !pull.existing);
  const existing = result.pulls.filter(pull => pull.existing);
  const failed = result.pulls.filter(pull => pull.error);
  output.write(`\n  Draft PRs created: ${created.length}  |  Existing: ${existing.length}  |  Failed: ${failed.length}\n`);
  for (const pull of [...created, ...existing]) output.write(`  ${pull.existing ? 'Existing' : 'Draft'}: ${pull.url}\n`);
  for (const pull of failed) output.write(`  PR error (${pull.title}): ${pull.error}\n`);
}

export async function runLauncher({ input = stdin, output = stdout, run = runCli } = {}) {
  renderBanner(output);
  output.write('  Press Enter at the live URL prompt to scan the repo. Use a GET API route such as /health for live checks.\n\n');
  const reader = createInterface({ input, output });
  let repository;
  let liveUrl;
  try {
    while (!repository) {
      const answer = (await reader.question('  GitHub repository URL: ')).trim();
      try { repository = parseRepositoryUrl(answer); }
      catch (error) { output.write(`  ${error.message}\n`); }
    }
    while (liveUrl === undefined) {
      const answer = (await reader.question('  Live API URL (press Enter to skip): ')).trim();
      if (!answer) { liveUrl = null; break; }
      try { liveUrl = validateLiveUrl(answer); }
      catch (error) { output.write(`  ${error.message}\n`); }
    }
  } finally {
    reader.close();
  }
  output.write('\n  Starting Odyssey. Validated findings will become draft PRs.\n\n');
  const progress = progressBar(output);
  const records = [];
  try {
    const args = ['inspect', `https://github.com/${repository}`, ...(liveUrl ? ['--live', liveUrl] : []), '--json'];
    await run(args, { output: line => records.push(JSON.parse(line)), progress: stage => progress.update(stage) });
    progress.stop();
    const result = summarizeRun(records);
    printSummary(result, output);
    return result;
  } catch (error) {
    progress.stop();
    output.write(`\n  Odyssey stopped: ${error.message}\n`);
    throw error;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  runLauncher().catch(() => { process.exitCode = 1; });
}
