# API Doctor · Odyssey

A CLI-first hackathon prototype for investigating Node and Python/FastAPI demo repositories. It accepts a GitHub repo URL alone, or the repo URL plus a live API URL. You do not need to paste an error log.

## Run

Requires Node.js 20 or newer. No npm packages are needed.

```bash
npm run doctor -- demo
npm run doctor -- inspect https://github.com/owner/repository
npm run doctor -- inspect https://github.com/owner/repository --live https://demo.example.com/health
npm run doctor -- inspect https://github.com/owner/repository --live https://demo.example.com/health --watch
```

Run `demo` first: it starts the bundled broken API briefly, detects its real HTTP 500, and prints the diagnosis and patch preview. It needs no network access, GitHub token, or Codex login.

Repo-only mode reads the GitHub file tree and up to 24 small application source files. It identifies Node and Python routes and flags a few concrete missing-data patterns. These are **possible faults**, not proof that the deployed API has crashed. The scan reads source; it does not install dependencies or execute the other team's code. Public repos work without GitHub credentials, subject to GitHub's unauthenticated rate limit. Set `GITHUB_TOKEN` in your shell for private repo access or a higher rate limit. Never put the token in the command line or a committed file.

Add `--live` to send a safe GET request to a public HTTPS health or demo endpoint. An HTTP 500 or connection failure becomes a confirmed incident. If the response contains a Node stack or Python traceback, API Doctor diagnoses it and tries to match the source file. If the server hides the trace, the CLI reports the confirmed failure and says that its root cause is unknown. `--watch` repeats the check every 15 seconds until Ctrl+C; use `--interval 30` to change that cadence. For a local demo URL only, add `--allow-local`. `--json` emits machine-readable report and probe records.

## Optional Luna review using your ChatGPT plan

```bash
codex login
npm run doctor -- inspect https://github.com/owner/repository --codex
```

Choose **Sign in with ChatGPT** during `codex login`. `--codex` sends the bounded source snapshot to a read-only, ephemeral GPT-6 Luna Fast analysis with xhigh reasoning. It uses the CLI's ChatGPT sign-in and Codex allowance; it refuses to run if the CLI is not signed in with ChatGPT, so this option does not silently use an API key. The CLI on this development machine was not signed in when this version was built, so this optional path has not been end-to-end verified here. Fast mode consumes allowance more quickly. The model's findings are suggestions, never treated as a confirmed live crash or an automatic code change.

## Draft pull requests

```bash
npm run doctor -- inspect https://github.com/owner/repository --live https://demo.example.com/health --pr
```

`--pr` requires `GITHUB_TOKEN` with write access to that repository. It opens a **draft** PR only when a live failure includes a source location and matches one of the two bundled, narrowly validated missing-progress repair recipes. Other failures are diagnosed without a guessed edit or PR. API Doctor never merges a PR. The target team must review and test any proposed change. This version does not create fork-based PRs for repositories where your token lacks write permission.

## Demo and checks

The repository includes a deliberately crashing Node API and a Python failure example used by automated tests. Run `npm test` to check the source diagnosis, CLI flows, live 500 detection, patch previews, and mocked GitHub draft PR creation. `npm start -- --help` prints all CLI options.

Use mock data only during the event. Ask teams before probing their demo API or scanning private code. Do not send student, health, or financial data to the bot.

## AI tool disclosure

OpenAI Codex was used to design and implement this prototype, its CLI, tests, and documentation. The default scan uses explicit rules and does not call a model. The optional `--codex` mode uses GPT-6 Luna through the locally authenticated Codex CLI.
