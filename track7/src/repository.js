const SOURCE_FILE = /(?:^|\/)(?:[^/]+\.)?(?:[cm]?[jt]sx?|py)$/i;
const SKIP_PATH = /(?:^|\/)(?:node_modules|vendor|dist|build|\.next|\.venv|venv|__pycache__|test|tests|spec|specs)(?:\/|$)/i;

export function parseRepositoryUrl(input) {
  let url;
  try { url = new URL(input); } catch { throw new Error('Enter a GitHub repository URL'); }
  const segments = url.pathname.replace(/\/$/, '').split('/').filter(Boolean);
  if (url.protocol !== 'https:' || url.hostname !== 'github.com' || url.username || url.password || url.search || url.hash || segments.length !== 2) {
    throw new Error('Use a GitHub URL like https://github.com/owner/repository');
  }
  const [owner, name] = segments;
  const repo = name.replace(/\.git$/i, '');
  if (!/^[\w.-]+$/.test(owner) || !/^[\w.-]+$/.test(repo)) throw new Error('Invalid GitHub repository');
  return `${owner}/${repo}`;
}

export function validateLiveUrl(input, allowLocal = false) {
  if (!input) return null;
  let url;
  try { url = new URL(input); } catch { throw new Error('Enter a full live API URL'); }
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.hash || url.search) throw new Error('Invalid live API URL');
  const hostname = url.hostname.toLowerCase();
  const local = hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]';
  if (local && allowLocal) return url.href;
  if (url.protocol !== 'https:' || local || hostname.endsWith('.local') || hostname.endsWith('.internal') || hostname.endsWith('.localhost') || hostname.includes(':') || /^\d+\.\d+\.\d+\.\d+$/.test(hostname)) {
    throw new Error('Use a public HTTPS API URL');
  }
  return url.href;
}

export function analyzeRepositoryFiles(repository, files, metadata = {}) {
  const routes = [];
  const findings = [];
  let hasPython = false;
  let hasFastApi = false;
  let hasJavaScript = false;
  for (const { path, content } of files) {
    if (path.endsWith('.py')) {
      hasPython = true;
      if (/\bfastapi\b|@\s*(?:app|router)\s*\.\s*(?:get|post|put|patch|delete)\s*\(/i.test(content)) hasFastApi = true;
    }
    if (/\.[cm]?[jt]sx?$/.test(path)) hasJavaScript = true;
    const lines = content.split(/\r?\n/);
    for (let index = 0; index < lines.length; index++) {
      const line = lines[index];
      const nodeRoute = line.match(/\b(?:app|router|server)\s*\.\s*(get|post|put|patch|delete)\s*\(\s*['"`]([^'"`]+)['"`]/i);
      const fastRoute = line.match(/@\s*(?:app|router)\s*\.\s*(get|post|put|patch|delete)\s*\(\s*['"]([^'"]+)['"]/i);
      const route = nodeRoute ?? fastRoute;
      if (route && routes.length < 40) routes.push({ method: route[1].toUpperCase(), path: route[2], filePath: path, line: index + 1 });
      if (/\bstudent\.progress\.completed\b/.test(line) && /\bprogress\s*:\s*(?:undefined|null)\b/.test(content)) {
        findings.push({ title: 'Possible missing progress crash', detail: 'This file defines a student without progress and later reads student.progress.completed.', filePath: path, line: index + 1, confidence: 'medium', kind: 'null-property' });
      }
      if (/\bstudent\[['"]progress['"]\]\[['"]completed['"]\]/.test(line)) {
        findings.push({ title: 'Possible missing progress key', detail: 'A missing progress key would raise KeyError on this line.', filePath: path, line: index + 1, confidence: 'low', kind: 'python-missing-value' });
      }
    }
  }
  return {
    repository,
    scannedAt: new Date().toISOString(),
    defaultBranch: metadata.defaultBranch ?? null,
    filesScanned: files.length,
    filePaths: files.map(file => file.path),
    incomplete: Boolean(metadata.incomplete),
    stack: [hasJavaScript ? 'Node / JavaScript' : null, hasFastApi ? 'Python / FastAPI' : hasPython ? 'Python' : null].filter(Boolean).join(' + ') || 'Stack unknown',
    routes,
    findings: findings.slice(0, 20),
    conclusion: findings.length
      ? 'Potential issues found in source. Reproduce or observe a failing request to confirm the cause.'
      : 'No known failure pattern found in the inspected source. A repository alone cannot confirm a live crash.'
  };
}

export function selectSourcePaths(tree) {
  const paths = tree.filter(item => item.type === 'blob' && item.size <= 80_000 && SOURCE_FILE.test(item.path) && !SKIP_PATH.test(item.path)).map(item => item.path);
  return paths.sort((a, b) => {
    const score = value => /(?:^|\/)(?:main|app|server|index|api|routes?)\.[cm]?[jt]sx?$|(?:^|\/)(?:main|app|api|routes?)\.py$/i.test(value) ? 0 : 1;
    return score(a) - score(b) || a.localeCompare(b);
  }).slice(0, 24);
}
