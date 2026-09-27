import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { diagnose, proposePatch } from './diagnose.js';
import { createFixPullRequest, previewGitHubPatch } from './github.js';
import { startDemoApi } from '../demo/target-api.js';

const projectRoot = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const publicRoot = path.join(projectRoot, 'public');
const demoSource = path.join(projectRoot, 'demo', 'target-api.js');

function json(response, status, data) {
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  response.end(JSON.stringify(data));
}

async function bodyJson(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 120_000) throw new Error('Request exceeds 120 KB');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

export async function startApp({ port = Number(process.env.PORT ?? 4100), demoPort = Number(process.env.DEMO_PORT ?? 4101), startDemo = true } = {}) {
  const demoServer = startDemo ? await startDemoApi(demoPort) : null;
  const actualDemoPort = demoServer?.address().port ?? demoPort;
  const targets = [{
    id: 'campus-demo',
    name: 'Campus Progress API',
    stack: 'Node / Express style',
    url: `http://127.0.0.1:${actualDemoPort}/api/students/42/progress`,
    repository: process.env.GITHUB_REPOSITORY ?? null,
    filePath: process.env.GITHUB_FILE_PATH ?? 'track7/demo/target-api.js',
    localSource: demoSource
  }, {
    id: 'python-demo',
    name: 'Python Progress API',
    stack: 'Python / FastAPI style',
    url: null,
    repository: process.env.GITHUB_REPOSITORY ?? null,
    filePath: process.env.PYTHON_FILE_PATH ?? 'track7/demo/python-api.py',
    localSource: path.join(projectRoot, 'demo', 'python-api.py')
  }];
  try {
    const extra = JSON.parse(await readFile(path.join(projectRoot, 'targets.json'), 'utf8'));
    if (!Array.isArray(extra)) throw new Error('targets.json must be an array');
    for (const target of extra) {
      if (!target.id || !target.name || !/^https?:\/\//.test(target.url)) throw new Error('Invalid target in targets.json');
      targets.push({ ...target, localSource: null });
    }
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }

  const incidents = [];
  const seen = new Set();
  const targetById = new Map(targets.map(target => [target.id, target]));

  async function recordFailure(target, log, statusCode = null) {
    const diagnosis = diagnose(log);
    const key = `${target.id}:${diagnosis.evidence}:${diagnosis.location?.line ?? ''}`;
    if (seen.has(key)) return incidents.find(item => item.key === key);
    seen.add(key);
    const incident = {
      id: String(Date.now()), key, targetId: target.id, targetName: target.name,
      time: new Date().toISOString(), statusCode, diagnosis,
      patch: null, pr: null, state: 'diagnosed'
    };
    incidents.unshift(incident);
    if (incidents.length > 50) incidents.pop();

    if (diagnosis.location) {
      let patch = null;
      if (target.localSource) {
        const source = await readFile(target.localSource, 'utf8');
        patch = proposePatch(diagnosis, source, diagnosis.location.line);
      } else if (target.repository && target.filePath) {
        try {
          patch = await previewGitHubPatch({ token: process.env.GITHUB_TOKEN, repository: target.repository, filePath: target.filePath, diagnosis });
        } catch (error) {
          incident.previewError = error.message;
        }
      }
      if (patch) {
        incident.patch = { before: patch.before.trim(), after: patch.after.trim(), explanation: patch.explanation };
        incident.state = 'patch-ready';
      }
    }

    if (process.env.AUTO_PR === 'true' && process.env.GITHUB_TOKEN && target.repository && target.filePath && incident.patch) {
      try {
        const pr = await createFixPullRequest({
          token: process.env.GITHUB_TOKEN,
          repository: target.repository,
          filePath: target.filePath,
          diagnosis,
          id: incident.id
        });
        incident.pr = { url: pr.url, number: pr.number };
        incident.state = 'pr-opened';
      } catch (error) {
        incident.state = 'pr-failed';
        incident.prError = error.message;
      }
    }
    return incident;
  }

  async function checkTarget(target) {
    if (!target.url) return { targetId: target.id, monitored: false };
    try {
      const response = await fetch(target.url, { signal: AbortSignal.timeout(5_000) });
      const raw = (await response.text()).slice(0, 100_000);
      if (response.ok) return { targetId: target.id, healthy: true, statusCode: response.status };
      let log = raw;
      try { log = JSON.parse(raw).stack ?? raw; } catch { /* Plain response. */ }
      const incident = await recordFailure(target, log, response.status);
      return { targetId: target.id, healthy: false, statusCode: response.status, incidentId: incident.id };
    } catch (error) {
      const incident = await recordFailure(target, `${error.name}: ${error.message}`);
      return { targetId: target.id, healthy: false, statusCode: null, incidentId: incident.id };
    }
  }

  const server = http.createServer(async (request, response) => {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    try {
      if (request.method === 'GET' && pathname === '/api/state') {
        json(response, 200, {
          targets: targets.map(({ id, name, stack, url }) => ({ id, name, stack, url })),
          incidents,
          autoPrEnabled: process.env.AUTO_PR === 'true' && Boolean(process.env.GITHUB_TOKEN)
        });
      } else if (request.method === 'POST' && pathname === '/api/check') {
        const results = await Promise.all(targets.filter(target => target.url).map(checkTarget));
        json(response, 200, { results });
      } else if (request.method === 'POST' && pathname === '/api/demo/python') {
        const log = 'Traceback (most recent call last):\n  File "/app/main.py", line 2, in get_progress\n    return {"completed": student["progress"]["completed"]}\nKeyError: \'progress\'';
        const incident = await recordFailure(targetById.get('python-demo'), log, 500);
        json(response, 202, { incident });
      } else if (request.method === 'POST' && pathname === '/api/ingest') {
        if (process.env.INGEST_SECRET && request.headers['x-ingest-secret'] !== process.env.INGEST_SECRET) {
          json(response, 401, { error: 'Invalid ingest secret' });
          return;
        }
        const { targetId, log } = await bodyJson(request);
        const target = targetById.get(targetId);
        if (!target) return json(response, 404, { error: 'Unknown target' });
        const incident = await recordFailure(target, log);
        json(response, 202, { incident });
      } else if (request.method === 'GET' && ['/', '/index.html', '/style.css', '/app.js'].includes(pathname)) {
        const filename = pathname === '/' ? 'index.html' : pathname.slice(1);
        const content = await readFile(path.join(publicRoot, filename));
        const type = filename.endsWith('.css') ? 'text/css' : filename.endsWith('.js') ? 'text/javascript' : 'text/html';
        response.writeHead(200, { 'Content-Type': `${type}; charset=utf-8` });
        response.end(content);
      } else {
        json(response, 404, { error: 'Not found' });
      }
    } catch (error) {
      json(response, 400, { error: error.message });
    }
  });
  await new Promise(resolve => server.listen(port, '127.0.0.1', resolve));
  const timer = setInterval(() => Promise.all(targets.filter(target => target.url).map(checkTarget)).catch(error => console.error(error)), 15_000);
  timer.unref();
  return {
    server,
    demoServer,
    address: server.address(),
    close: async () => {
      clearInterval(timer);
      await Promise.all([server, demoServer].filter(Boolean).map(item => new Promise(resolve => item.close(resolve))));
    }
  };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  startApp().then(app => console.log(`API Doctor: http://127.0.0.1:${app.address.port}`)).catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
}
