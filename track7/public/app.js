const $ = id => document.getElementById(id);

function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
}

async function loadState() {
  const response = await fetch('/api/state');
  if (!response.ok) throw new Error('Could not read monitor state');
  const state = await response.json();
  $('target-count').textContent = state.targets.length;
  $('incident-count').textContent = state.incidents.length;
  $('patch-count').textContent = state.incidents.filter(item => item.patch).length;
  $('pr-count').textContent = state.incidents.filter(item => item.pr).length;
  const latest = state.incidents[0];
  $('hero-status').textContent = latest ? latest.pr ? 'Draft PR open' : latest.patch ? 'Patch ready' : 'Diagnosed' : 'Awaiting signal';
  $('hero-code').textContent = latest ? latest.statusCode ?? 'LOG' : '—';
  $('hero-title').textContent = latest?.diagnosis.title ?? 'The room is quiet.';
  $('hero-detail').textContent = latest?.diagnosis.rootCause ?? 'Run a check to watch API Doctor detect and explain the bundled demo failure.';
  $('hero-time').textContent = latest ? new Date(latest.time).toLocaleTimeString() : 'LIVE MONITORING';

  const targets = $('targets');
  targets.replaceChildren();
  for (const target of state.targets) {
    const card = element('div', 'target');
    const top = element('div', 'target-top');
    top.append(element('strong', '', target.name), element('span', `badge ${target.url ? '' : 'muted'}`, target.url ? 'HTTP WATCH' : 'LOG INGEST'));
    card.append(top, element('p', '', target.stack ?? 'API source'), element('p', '', target.url ?? 'Python traceback example'));
    targets.append(card);
  }

  const feed = $('incidents');
  feed.replaceChildren();
  if (!state.incidents.length) {
    feed.append(element('div', 'empty', 'No incidents yet. Run a live check to catch the bundled demo API crash.'));
    return;
  }
  for (const item of state.incidents) {
    const card = element('article', 'incident');
    const top = element('div', 'incident-top');
    top.append(element('span', 'time', `${item.targetName} · ${new Date(item.time).toLocaleTimeString()}`), element('span', `badge ${item.pr ? '' : 'error'}`, item.pr ? 'DRAFT PR OPEN' : item.patch ? 'PATCH READY' : 'DIAGNOSED'));
    card.append(top, element('h4', '', item.diagnosis.title), element('p', 'root', item.diagnosis.rootCause));
    card.append(element('div', 'label', 'EVIDENCE'), element('pre', '', `${item.diagnosis.evidence}\n${item.diagnosis.location ? `${item.diagnosis.location.file}:${item.diagnosis.location.line}` : 'No source location in log'}`));
    if (item.patch) {
      card.append(element('div', 'label', 'PROPOSED FIX'));
      card.append(element('pre', 'before', `− ${item.patch.before}`));
      card.append(element('pre', 'after', `+ ${item.patch.after}`));
      card.append(element('p', '', item.patch.explanation));
    } else {
      card.append(element('p', '', item.diagnosis.nextStep));
    }
    if (item.pr) {
      const link = element('a', '', `Open draft pull request #${item.pr.number} ↗`);
      link.href = item.pr.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      card.append(link);
    }
    if (item.prError) card.append(element('p', '', `PR creation failed: ${item.prError}`));
    feed.append(card);
  }
}

async function action(button, endpoint, message) {
  button.disabled = true;
  $('message').textContent = 'Working…';
  try {
    const response = await fetch(endpoint, { method: 'POST' });
    if (!response.ok) throw new Error((await response.json()).error ?? 'Request failed');
    await loadState();
    $('message').textContent = message;
  } catch (error) {
    $('message').textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

$('check').addEventListener('click', () => action($('check'), '/api/check', 'Check complete. The incident feed now shows the diagnosis and proposed fix.'));
$('python').addEventListener('click', () => action($('python'), '/api/demo/python', 'Python traceback analyzed.'));
loadState().catch(error => { $('message').textContent = error.message; });
setInterval(() => loadState().catch(() => {}), 10_000);
