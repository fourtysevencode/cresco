#!/usr/bin/env node
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { readFile } from 'node:fs/promises';
import { diagnose, proposePatch } from './diagnose.js';
import { createFixPullRequest, createFindingPullRequest, inspectGitHubRepository, previewGitHubPatch } from './github.js';
import { analyzeRepositoryFiles, parseRepositoryUrl, validateLiveUrl } from './repository.js';
import { reviewWithCodex } from './codex.js';
import { githubToken } from './auth.js';
import { startDemoApi } from '../demo/target-api.js';

const USAGE = `API Doctor · Odyssey

Usage:
  npm run doctor -- demo
  npm run doctor -- inspect <github-repo-url> [--live <api-url>] [--watch] [--interval <seconds>] [--dry-run] [--json]

Examples:
  npm run doctor -- demo
  npm run doctor -- inspect https://github.com/team/campus-api
  npm run doctor -- inspect https://github.com/team/campus-api --live https://demo.example.com/health --watch

Repo-only mode asks Codex Luna for critical, high, medium, or project-blocking logical issues and opens draft fix PRs for validated findings.
Add --live to investigate an observed API failure and open a draft fix PR when evidence supports a repair.
Use GITHUB_TOKEN, GH_TOKEN, or your existing Git credential for PR creation. Sign the Codex CLI in with ChatGPT using codex login.
--dry-run analyzes without opening PRs. --no-ai runs only the basic source and HTTP checks.
Use --allow-local only to probe a local demo API. Press Ctrl+C to stop --watch.`;

export function parseArgs(args) {
  if (!args.length || args.includes('--help') || args.includes('-h')) return { help: true };
  if (args[0] === 'demo') {
    if (args.length > 2 || (args[1] && args[1] !== '--json')) throw new Error(`Unknown demo option\n\n${USAGE}`);
    return { demo: true, json: args.includes('--json') };
  }
  const values = args[0] === 'inspect' ? args.slice(1) : args;
  if (!values[0] || values[0].startsWith('--')) throw new Error(USAGE);
  const options = { repository: parseRepositoryUrl(values[0]), liveUrl: null, watch: false, dryRun: false, noAi: false, json: false, allowLocal: false, interval: 15 };
  for (let index = 1; index < values.length; index++) {
    let flag = values[index];
    if (flag === '--' && values[index + 1] === 'live') { flag = '--live'; index++; }
    if (flag === '--live') {
      options.liveUrl = values[++index];
      if (!options.liveUrl || options.liveUrl.startsWith('--')) throw new Error('--live requires an API URL');
    }
    else if (flag === '--interval') options.interval = Number(values[++index]);
    else if (flag === '--watch') options.watch = true;
    else if (flag === '--pr' || flag === '--codex') { /* Kept for earlier CLI invocations. */ }
    else if (flag === '--dry-run') options.dryRun = true;
    else if (flag === '--no-ai') options.noAi = true;
    else if (flag === '--json') options.json = true;
    else if (flag === '--allow-local') options.allowLocal = true;
    else throw new Error(`Unknown option: ${flag}\n\n${USAGE}`);
  }
  if (options.liveUrl) options.liveUrl = validateLiveUrl(options.liveUrl, options.allowLocal);
  if (options.watch && !options.liveUrl) throw new Error('--watch requires --live');
  if (!Number.isInteger(options.interval) || options.interval < 5 || options.interval > 3600) throw new Error('--interval must be 5–3600 seconds');
  return options;
}

function sourcePathFor(location, report) {
  if (!location) return null;
  const normalized = location.file.replaceAll('\\', '/');
  const candidates = report.filePaths ?? [];
  const suffix = candidates.find(candidate => normalized.endsWith(`/${candidate}`) || normalized === candidate);
  if (suffix) return suffix;
  const basename = path.posix.basename(normalized);
  const matches = candidates.filter(candidate => path.posix.basename(candidate) === basename);
  return matches.length === 1 ? matches[0] : null;
}

async function limitedText(response, max = 100_000) {
  const reader = response.body?.getReader();
  if (!reader) return '';
  const chunks = [];
  let size = 0;
  while (size < max) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value.subarray(0, max - size));
    size += value.length;
  }
  await reader.cancel().catch(() => {});
  return Buffer.concat(chunks).toString('utf8');
}

export async function probeLiveApi(url, report, { token, createPr = false, incidentIds = new Set(), localSources } = {}) {
  let response;
  try {
    response = await fetch(url, { method: 'GET', redirect: 'error', signal: AbortSignal.timeout(8_000), headers: { Accept: 'application/json, text/plain;q=0.8' } });
  } catch (error) {
    return { url, checkedAt: new Date().toISOString(), healthy: false, statusCode: null, error: `${error.name}: ${error.message}`, diagnosis: diagnose(`${error.name}: ${error.message}`) };
  }
  const raw = await limitedText(response);
  const result = { url, checkedAt: new Date().toISOString(), healthy: response.status < 500, statusCode: response.status };
  if (response.status < 500) {
    if (response.status >= 400) result.note = 'Endpoint returned a client error. Check that the URL points to a public GET health or demo route.';
    return result;
  }
  let log = raw;
  try {
    const data = JSON.parse(raw);
    log = data.stack ?? data.traceback ?? data.detail ?? data.error ?? raw;
    if (typeof log !== 'string') log = JSON.stringify(log);
  } catch { /* The response was not JSON. */ }
  result.diagnosis = diagnose(log || `HTTP ${response.status} from ${new URL(url).pathname}`);
  if (result.diagnosis.kind === 'unknown') {
    result.diagnosis = {
      ...result.diagnosis,
      title: `API returned HTTP ${response.status}`,
      rootCause: 'The live API failed, but its response did not expose enough information to establish the cause. Inspect its server logs or add an opt-in error feed.',
      evidence: `HTTP ${response.status} at ${new URL(url).pathname}; response: ${result.diagnosis.evidence}`
    };
  }
  const filePath = sourcePathFor(result.diagnosis.location, report);
  if (filePath) {
    result.filePath = filePath;
    try {
      const patch = localSources?.has(filePath)
        ? proposePatch(result.diagnosis, localSources.get(filePath), result.diagnosis.location?.line)
        : await previewGitHubPatch({ token, repository: report.repository, filePath, diagnosis: result.diagnosis });
      if (patch) result.patch = { before: patch.before.trim(), after: patch.after.trim(), explanation: patch.explanation };
    } catch (error) { result.patchError = error.message; }
  }
  const key = `${result.statusCode}:${result.diagnosis.evidence}:${filePath ?? ''}`;
  if (createPr && result.patch && !incidentIds.has(key)) {
    incidentIds.add(key);
    try {
      const pr = await createFixPullRequest({ token, repository: report.repository, filePath, diagnosis: result.diagnosis, id: Date.now() });
      result.pr = { url: pr.url, number: pr.number };
    } catch (error) { result.prError = error.message; }
  }
  return result;
}

function printReport(report, output) {
  output(`\nREPOSITORY  ${report.repository} · ${report.defaultBranch ?? 'default branch'}`);
  output(`STACK       ${report.stack}`);
  output(`INSPECTED   ${report.filesScanned} source files${report.incomplete ? ' (partial scan)' : ''}`);
  output(`ENDPOINTS   ${report.routes.length ? report.routes.map(route => `${route.method} ${route.path}`).join(', ') : 'None found in inspected files'}`);
  output(`FINDINGS    ${report.findings.length}`);
  for (const finding of report.findings) output(`  • ${finding.title} · ${finding.filePath}:${finding.line} · ${finding.confidence} confidence\n    ${finding.detail}`);
  output(report.conclusion);
}

function printProbe(result, output) {
  output(`\nLIVE CHECK  ${result.checkedAt} · ${result.statusCode ?? 'NO RESPONSE'} · ${result.healthy ? 'reachable' : 'failed'}`);
  if (result.note) output(result.note);
  if (result.error) output(result.error);
  if (result.diagnosis) {
    output(`DIAGNOSIS   ${result.diagnosis.title}`);
    output(result.diagnosis.rootCause);
    if (result.filePath) output(`SOURCE      ${result.filePath}:${result.diagnosis.location.line}`);
    if (result.patch) output(`PATCH       ${result.patch.before} → ${result.patch.after}`);
  }
  if (result.pr) output(`DRAFT PR    ${result.pr.url}`);
  if (result.prError) output(`PR ERROR    ${result.prError}`);
  if (result.agent) {
    if (result.agentMode === 'repository') output('FALLBACK    Live endpoint unavailable; checked repository for actionable issues.');
    output(`LUNA REVIEW ${result.agent.summary}`);
    for (const finding of result.agent.findings) output(`  • ${finding.title} · ${finding.filePath}:${finding.line}\n    ${finding.detail}`);
    if (result.dryRun) output('DRY RUN     No pull requests created.');
  }
}

export async function runCli(args, { output = console.log, error = console.error, signal, progress } = {}) {
  const options = parseArgs(args);
  if (options.help) { output(USAGE); return 0; }
  if (options.demo) {
    const files = await Promise.all(['target-api.js', 'python-api.py'].map(async name => ({
      path: `demo/${name}`,
      content: await readFile(new URL(`../demo/${name}`, import.meta.url), 'utf8')
    })));
    const report = analyzeRepositoryFiles('bundled/demo', files);
    const demoServer = await startDemoApi(0);
    try {
      const url = `http://127.0.0.1:${demoServer.address().port}/api/students/42/progress`;
      const result = await probeLiveApi(url, report, { localSources: new Map(files.map(file => [file.path, file.content])) });
      if (options.json) {
        output(JSON.stringify({ type: 'repository', report }));
        output(JSON.stringify({ type: 'probe', result }));
      } else {
        printReport(report, output);
        printProbe(result, output);
      }
    } finally {
      await new Promise(resolve => demoServer.close(resolve));
    }
    return 0;
  }
  const token = githubToken();
  progress?.('Loading GitHub source');
  const report = await inspectGitHubRepository({ token, repository: options.repository });
  if (!options.liveUrl && !options.noAi) {
    progress?.('Luna is reviewing the repository');
    report.agent = await reviewWithCodex(report, { mode: 'repository' });
    report.pullRequests = [];
    for (const finding of report.agent.findings) {
      try {
        if (!options.dryRun) progress?.('Creating draft pull requests');
        if (!options.dryRun) report.pullRequests.push(await createFindingPullRequest({ token, repository: report.repository, finding, mode: 'repository' }));
      } catch (error) { report.pullRequests.push({ error: error.message, title: finding.title }); }
    }
  }
  if (options.json) output(JSON.stringify({ type: 'repository', report }));
  else {
    printReport(report, output);
    if (report.agent) {
      output(`\nLUNA REVIEW ${report.agent.summary}`);
      for (const finding of report.agent.findings) output(`  • ${finding.title} · ${finding.filePath}:${finding.line} · ${finding.severity}\n    ${finding.detail}\n    Impact: ${finding.impact}`);
      for (const pr of report.pullRequests ?? []) output(pr.url ? `DRAFT PR    ${pr.url}` : `PR ERROR    ${pr.title}: ${pr.error}`);
      if (options.dryRun) output('DRY RUN     No pull requests created.');
    }
  }
  if (!options.liveUrl) return 0;
  const incidentIds = new Set();
  do {
    progress?.('Checking the live API');
    const result = await probeLiveApi(options.liveUrl, report, { token });
    const securityFallback = result.statusCode === null || (result.statusCode >= 400 && result.statusCode < 500);
    if ((!result.healthy || securityFallback) && !options.noAi) {
      const mode = securityFallback ? 'repository' : 'live';
      const incidentKey = `${mode}:${result.statusCode}:${result.error ?? ''}:${result.diagnosis?.evidence ?? ''}`;
      if (!incidentIds.has(incidentKey)) {
        incidentIds.add(incidentKey);
        result.agentMode = mode;
        result.dryRun = options.dryRun;
        progress?.(mode === 'live' ? 'Luna is diagnosing the failure' : 'Luna is reviewing the repository');
        result.agent = await reviewWithCodex(report, { mode, liveFailure: mode === 'live' ? result : null });
        result.pullRequests = [];
        for (const finding of result.agent.findings) {
          try {
            if (!options.dryRun) progress?.('Creating draft pull requests');
            if (!options.dryRun) result.pullRequests.push(await createFindingPullRequest({ token, repository: report.repository, finding, mode }));
          } catch (error) { result.pullRequests.push({ error: error.message, title: finding.title }); }
        }
        result.pr = result.pullRequests.find(pr => pr.url);
        result.prError = result.pullRequests.find(pr => pr.error)?.error;
      }
    }
    if (options.json) output(JSON.stringify({ type: 'probe', result }));
    else printProbe(result, output);
    if (!options.watch || signal?.aborted) break;
    await new Promise(resolve => {
      const timer = setTimeout(resolve, options.interval * 1000);
      signal?.addEventListener('abort', () => { clearTimeout(timer); resolve(); }, { once: true });
    });
  } while (!signal?.aborted);
  return 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const controller = new AbortController();
  process.once('SIGINT', () => controller.abort());
  runCli(process.argv.slice(2), { signal: controller.signal }).catch(error => {
    console.error(`API Doctor: ${error.message}`);
    process.exitCode = 1;
  });
}
