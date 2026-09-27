import { spawnSync } from 'node:child_process';

export function githubToken() {
  if (process.env.GITHUB_TOKEN) return process.env.GITHUB_TOKEN;
  if (process.env.GH_TOKEN) return process.env.GH_TOKEN;
  const result = spawnSync('git', ['-c', 'credential.interactive=never', 'credential', 'fill'], {
    input: 'protocol=https\nhost=github.com\n\n',
    encoding: 'utf8',
    timeout: 5_000,
    windowsHide: true,
    env: { ...process.env, GIT_TERMINAL_PROMPT: '0', GCM_INTERACTIVE: 'never' }
  });
  if (result.status !== 0) return null;
  return result.stdout.match(/^password=(.+)$/m)?.[1] ?? null;
}
