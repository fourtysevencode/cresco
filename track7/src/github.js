import { proposePatch } from './diagnose.js';
import { analyzeRepositoryFiles, selectSourcePaths } from './repository.js';
import { createHash, randomUUID } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { eligibleForPullRequest } from './eligibility.js';

async function github(token, method, url, body) {
  const response = await fetch(`https://api.github.com${url}`, {
    method,
    signal: AbortSignal.timeout(10_000),
    headers: {
      Accept: 'application/vnd.github+json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json'
    },
    body: body ? JSON.stringify(body) : undefined
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(`GitHub ${method} ${url}: ${response.status} ${data.message ?? 'request failed'}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

export async function inspectGitHubRepository({ token, repository, onLog }) {
  if (!/^[\w.-]+\/[\w.-]+$/.test(repository)) throw new Error('Invalid repository');
  const root = `/repos/${repository}`;
  onLog?.(`GitHub: reading ${repository} metadata`);
  const repo = await github(token, 'GET', root);
  onLog?.(`GitHub: default branch is ${repo.default_branch}`);
  const tree = await github(token, 'GET', `${root}/git/trees/${encodeURIComponent(repo.default_branch)}?recursive=1`);
  if (!Array.isArray(tree.tree)) throw new Error('GitHub did not return a repository file tree');
  const paths = selectSourcePaths(tree.tree);
  onLog?.(`GitHub: selected ${paths.length} source files from ${tree.tree.length} tree entries${tree.truncated ? ' (tree truncated)' : ''}`);
  const files = [];
  for (const [index, path] of paths.entries()) {
    onLog?.(`Source ${index + 1}/${paths.length}: ${path}`);
    try {
      const { source } = await sourceFile(token, repository, path, repo.default_branch);
      files.push({ path, content: source });
    } catch (error) {
      onLog?.(`Source skipped: ${path} (${error.message})`);
      if (!/GitHub GET .*: (403|429)/.test(error.message)) continue;
      throw error;
    }
  }
  const report = analyzeRepositoryFiles(repository, files, {
    defaultBranch: repo.default_branch,
    incomplete: Boolean(tree.truncated) || paths.length === 24 || files.length !== paths.length
  });
  Object.defineProperty(report, 'sourceFiles', { value: files });
  onLog?.(`Source scan: loaded ${files.length} files and found ${report.routes.length} route declarations${report.incomplete ? ' (partial scan)' : ''}`);
  return report;
}

async function sourceFile(token, repository, filePath, branch) {
  if (!/^[\w.-]+\/[\w.-]+$/.test(repository)) throw new Error('Invalid repository');
  if (!filePath || filePath.startsWith('/') || filePath.split('/').includes('..')) throw new Error('Invalid repository file path');
  const root = `/repos/${repository}`;
  const encodedPath = filePath.split('/').map(encodeURIComponent).join('/');
  const file = await github(token, 'GET', `${root}/contents/${encodedPath}${branch ? `?ref=${encodeURIComponent(branch)}` : ''}`);
  if (file.type !== 'file' || file.size > 100_000 || file.encoding !== 'base64') {
    throw new Error('Only source files under 100 KB are supported');
  }
  return { file, encodedPath, source: Buffer.from(file.content.replace(/\s/g, ''), 'base64').toString('utf8') };
}

export async function previewGitHubPatch({ token, repository, filePath, diagnosis }) {
  const { source } = await sourceFile(token, repository, filePath);
  return proposePatch(diagnosis, source, diagnosis.location?.line);
}

export async function createFixPullRequest({ token, repository, filePath, diagnosis, id }) {
  if (!token) throw new Error('GITHUB_TOKEN is required to create a pull request');
  const root = `/repos/${repository}`;
  const repo = await github(token, 'GET', root);
  const base = repo.default_branch;
  const { file, encodedPath, source } = await sourceFile(token, repository, filePath, base);
  const patch = proposePatch(diagnosis, source, diagnosis.location?.line);
  if (!patch) throw new Error('No safe automatic patch is available for this failure');

  const baseRef = await github(token, 'GET', `${root}/git/ref/heads/${encodeURIComponent(base)}`);
  const branch = `api-doctor/fix-${id}`;
  await github(token, 'POST', `${root}/git/refs`, {
    ref: `refs/heads/${branch}`,
    sha: baseRef.object.sha
  });
  await github(token, 'PUT', `${root}/contents/${encodedPath}`, {
    message: `fix: handle missing progress in demo API`,
    content: Buffer.from(patch.content, 'utf8').toString('base64'),
    sha: file.sha,
    branch
  });
  const pull = await github(token, 'POST', `${root}/pulls`, {
    title: `[API Doctor] ${diagnosis.title}`,
    head: branch,
    base,
    body: `## Observed failure\n\n${diagnosis.evidence}\n\n## Root cause\n\n${diagnosis.rootCause}\n\n## Proposed fix\n\n${patch.explanation}\n\nGenerated by API Doctor. Please review and run your API tests before merging.`,
    draft: true
  });
  return { url: pull.html_url, number: pull.number, patch };
}

function applyExactEdit(source, finding) {
  const { original, replacement, line } = finding;
  if (typeof original !== 'string' || !original.trim() || original.length > 8_000 || typeof replacement !== 'string' || replacement.length > 12_000 || original === replacement) {
    throw new Error('The proposed source edit is empty, unchanged, or too large');
  }
  const first = source.indexOf(original);
  if (first < 0 || source.indexOf(original, first + original.length) >= 0) throw new Error('The proposed source edit does not match exactly one current source location');
  const startLine = source.slice(0, first).split('\n').length;
  const endLine = startLine + original.split('\n').length - 1;
  if (!Number.isInteger(line) || line < startLine - 3 || line > endLine + 3) throw new Error('The proposed source edit does not match its reported line');
  return source.slice(0, first) + replacement + source.slice(first + original.length);
}

function validateSyntax(filePath, content) {
  let result;
  if (/\.py$/i.test(filePath)) {
    result = spawnSync('python', ['-c', 'import sys; compile(sys.stdin.read(), "candidate.py", "exec")'], { input: content, encoding: 'utf8', timeout: 5_000, windowsHide: true });
  } else if (/\.(?:js|mjs|cjs)$/i.test(filePath)) {
    result = spawnSync(process.execPath, ['--check', `--input-type=${/\.cjs$/i.test(filePath) ? 'commonjs' : 'module'}`], { input: content, encoding: 'utf8', timeout: 5_000, windowsHide: true });
  } else {
    throw new Error(`Automatic PRs do not yet support syntax validation for ${filePath}`);
  }
  if (result.error || result.status !== 0) throw new Error(`Proposed edit fails syntax validation for ${filePath}`);
}

async function writableRepository(token, upstream, base, upstreamRepo) {
  if (upstreamRepo.permissions?.push !== false) return { repository: upstream, headPrefix: '' };
  const fork = await github(token, 'POST', `/repos/${upstream}/forks`, { default_branch_only: true });
  if (!/^[\w.-]+\/[\w.-]+$/.test(fork.full_name ?? '')) throw new Error('GitHub did not return a valid fork');
  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      await github(token, 'GET', `/repos/${fork.full_name}/git/ref/heads/${encodeURIComponent(base)}`);
      return { repository: fork.full_name, headPrefix: `${fork.owner.login}:` };
    } catch (error) {
      if (error.status !== 404 || attempt === 4) throw error;
      await new Promise(resolve => setTimeout(resolve, 2_000));
    }
  }
}

export async function createFindingPullRequest({ token, repository, finding, mode = 'repository', onLog }) {
  if (!token) throw new Error('GITHUB_TOKEN is required to create a pull request');
  if (!/^[\w.-]+\/[\w.-]+$/.test(repository)) throw new Error('Invalid repository');
  if (!finding?.filePath || !/^[\w./-]+$/.test(finding.filePath) || finding.filePath.startsWith('/') || finding.filePath.split('/').includes('..')) throw new Error('Invalid source path');
  if (!eligibleForPullRequest(finding)) throw new Error('Finding is below the PR threshold: requires critical, high, medium, or blocking logic');
  if (/(?:exposed|leaked|hardcoded)\s+(?:secret|credential|token|password|key)/i.test(finding.title)) throw new Error('Exposed credentials require rotation and cannot be safely fixed by an automatic PR');
  const upstream = `/repos/${repository}`;
  onLog?.(`PR: checking ${finding.filePath}:${finding.line} against current GitHub state`);
  const repo = await github(token, 'GET', upstream);
  const base = repo.default_branch;
  const fingerprint = createHash('sha256').update(JSON.stringify([repository, mode, finding.filePath, finding.original, finding.replacement])).digest('hex').slice(0, 20);
  const marker = `<!-- api-doctor:${fingerprint} -->`;
  const openPulls = await github(token, 'GET', `${upstream}/pulls?state=open&per_page=100`);
  const sourceMarker = `**Source:** \`${finding.filePath}:${finding.line}\``;
  const existing = Array.isArray(openPulls) ? openPulls.find(pr => pr.body?.includes(marker) || (pr.title?.startsWith('[API Doctor]') && pr.body?.includes(sourceMarker))) : null;
  if (existing) {
    onLog?.(`PR: existing fix found at ${existing.html_url}`);
    return { url: existing.html_url, number: existing.number, existing: true };
  }
  const target = await writableRepository(token, repository, base, repo);
  onLog?.(target.repository === repository ? 'PR: using a branch in the target repository' : `PR: using fork ${target.repository}`);
  const targetRoot = `/repos/${target.repository}`;
  const { file, encodedPath, source } = await sourceFile(token, target.repository, finding.filePath, base);
  onLog?.('PR: validating the exact source edit and syntax');
  const content = applyExactEdit(source, finding);
  validateSyntax(finding.filePath, content);
  const baseRef = await github(token, 'GET', `${targetRoot}/git/ref/heads/${encodeURIComponent(base)}`);
  const branch = `api-doctor/${mode}-${randomUUID().slice(0, 8)}`;
  onLog?.(`PR: creating branch ${branch}`);
  await github(token, 'POST', `${targetRoot}/git/refs`, { ref: `refs/heads/${branch}`, sha: baseRef.object.sha });
  onLog?.(`PR: writing validated edit to ${finding.filePath}`);
  await github(token, 'PUT', `${targetRoot}/contents/${encodedPath}`, {
    message: `fix: ${finding.title.slice(0, 65)}`,
    content: Buffer.from(content, 'utf8').toString('base64'),
    sha: file.sha,
    branch
  });
  const pull = await github(token, 'POST', `${upstream}/pulls`, {
    title: `[API Doctor] ${finding.title.slice(0, 100)}`,
    head: `${target.headPrefix}${branch}`,
    base,
    body: `## ${mode === 'repository' ? 'Repository finding' : 'Observed API failure'}\n\n${finding.detail}\n\n**Severity:** ${finding.severity}\n\n**Impact:** ${finding.impact}\n\n**Source:** \`${finding.filePath}:${finding.line}\`\n\n**Validation:** ${finding.testPlan}\n\nProposed by API Doctor with Codex Luna. This is a draft; maintainers should review and run their tests before merging.\n\n${marker}`,
    draft: true,
    maintainer_can_modify: true
  });
  onLog?.(`PR: draft opened at ${pull.html_url}`);
  return { url: pull.html_url, number: pull.number, fork: target.repository !== repository };
}
