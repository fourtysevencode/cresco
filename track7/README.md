# API Doctor · Odyssey

API Doctor is a CLI for opt-in hackathon repositories. Give it a GitHub repository URL. Add a live API URL when one is available. It uses GPT-6 Luna through the locally signed-in Codex CLI to investigate and proposes reviewable **draft pull requests** for supported Node and Python/FastAPI source files.

## Setup

Requires Node.js 20+, the Codex CLI signed in with **ChatGPT**, and a GitHub token with access to the target repository and permission to create pull requests. No npm packages or OpenAI API key are required.

```bash
codex login
```

Set `GITHUB_TOKEN` in your shell's environment or secret manager. Do not paste it into a command, chat, or committed file. For repositories you cannot push to, the token must allow creating a fork; API Doctor then opens the PR against the original repository from a branch in that fork. The target team's repository must allow outside PRs. The tool never merges a PR.

## Run

```bash
# Repository only: investigate critical security issues and open draft fix PRs
npm run doctor -- inspect https://github.com/owner/repository

# Repository and live API: probe the endpoint, diagnose a failure, and open a draft fix PR
npm run doctor -- inspect https://github.com/owner/repository --live https://demo.example.com/health

# Keep monitoring the live URL every 15 seconds; Ctrl+C stops it
npm run doctor -- inspect https://github.com/owner/repository --live https://demo.example.com/health --watch
```

Use `--dry-run` to review findings without opening PRs, `--json` for machine-readable results, or `--interval 30` with `--watch`. `--no-ai` runs the basic source and HTTP checks only. For a local development API, add `--allow-local`. `npm run doctor -- demo` runs a bundled, network-free smoke check.

With a live URL, API Doctor sends a GET request. A server error or connection failure triggers Luna analysis using the observed response and repository source. A healthy endpoint does not trigger a PR. With only a repository URL, Luna looks for demonstrable **critical** security vulnerabilities; it should return no findings when evidence is insufficient. The bounded scan prioritizes application source and currently inspects up to 24 files, so a clean result is not a full security audit. The CLI does not execute the target's code.

Before opening a PR, API Doctor checks that the proposed original text occurs exactly once in the current GitHub file, matches the cited line, and that the edited JavaScript or Python file parses. It opens one draft PR per finding. The maintainer must run relevant tests and review the change. An exposed credential requires rotation, so API Doctor does not open an automatic PR for that finding.

## Validation and limits

`npm test` covers repository and live modes, the bundled HTTP failure, Codex account gating, and GitHub's fork/branch/file/draft-PR request flow. The GitHub PR flow is tested with a controlled API mock; creating a real PR requires a target repository and GitHub credentials. The Codex CLI on this machine still needs its ChatGPT sign-in completed before a live Luna run can be verified.

Only probe APIs and repositories that teams have agreed to share. Use mock student, health, and financial data for the hackathon. Document any AI tools used in your submitted project README.

## AI tool disclosure

OpenAI Codex was used to implement API Doctor. API Doctor uses GPT-6 Luna through the Codex CLI for repository and incident analysis. The bundled demo and `--no-ai` mode do not call a model.
