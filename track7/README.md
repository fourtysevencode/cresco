# API Doctor · Odyssey

API Doctor is a CLI for opt-in hackathon repositories. Give it a GitHub repository URL. Add a live API URL when one is available. It uses GPT-6 Luna through the locally signed-in Codex CLI to investigate and proposes reviewable **draft pull requests** for supported Node and Python/FastAPI source files.

## Setup

Requires Node.js 20+ and the Codex CLI signed in with **ChatGPT**. GitHub authorization is needed to create PRs; public repository scans can run without it. API Doctor first uses `GITHUB_TOKEN` or `GH_TOKEN`, then tries the existing Git credential helper. No npm packages or OpenAI API key are required. On Windows, Odyssey also finds the Codex app's versioned `codex.exe` if PowerShell cannot find `codex` on `PATH`.

```bash
codex login
```

If the Git credential helper is not signed in, set `GITHUB_TOKEN` in your shell's environment or secret manager. Do not paste it into a command, chat, or committed file. For repositories you cannot push to, the credential must allow creating a fork; API Doctor then opens the PR against the original repository from a branch in that fork. The target team's repository must allow outside PRs. The tool never merges a PR.

## Run

From this `track7` folder, run `npm run odyssey`, or double-click the **Odyssey API Doctor** desktop shortcut on the configured Windows machine. The launcher opens PowerShell with character art sampled from the supplied Odysseus statue image. Paste a plain GitHub repository URL, then paste a GET API URL or press **Enter** to scan the repository without one. A healthy live endpoint ends the check without a Luna review; use a route such as `/health`, not a website homepage that redirects.

While Odyssey runs, the terminal shows a progress bar and timestamped logs for source loading, Luna review (including elapsed-time updates), live probes, and PR validation. The final report shows critical, high, medium, and logical-error counts plus draft PR links. Logs include file paths and status, but do not print credentials or source contents.

```bash
# Repository only: investigate critical/high/medium and blocking logical issues, then open draft fix PRs
npm run doctor -- inspect https://github.com/owner/repository --verbose

# Review without opening PRs
npm run doctor -- inspect https://github.com/owner/repository --dry-run --verbose

# Repository and live API: probe the endpoint, diagnose a failure, and open a draft fix PR
npm run doctor -- inspect https://github.com/owner/repository --live https://demo.example.com/health

# Keep monitoring the live URL every 15 seconds; Ctrl+C stops it
npm run doctor -- inspect https://github.com/owner/repository --live https://demo.example.com/health --watch
```

Use `--verbose` for timestamped diagnostic progress on stderr, `--json` for machine-readable results on stdout, or `--interval 30` with `--watch`. `--no-ai` runs the basic source and HTTP checks only. For a local development API, add `--allow-local`. `npm run doctor -- demo` runs a bundled, network-free smoke check. The CLI commands must run from this folder, where `package.json` lives; pass `inspect` after npm's `--` separator and paste URLs as plain text rather than Markdown links.

With a live URL, API Doctor sends a GET request. A server error triggers Luna analysis using the observed response and repository source. If the URL cannot be reached, it falls back to repository analysis. A healthy endpoint does not trigger a PR. With only a repository URL, Luna looks for concrete, fixable **critical, high, or medium** issues and lower-severity **logical errors that prevent startup or a core API/demo workflow from running**. It creates a draft PR for each eligible finding that passes the source and syntax checks; ordinary low-severity findings are excluded. The bounded scan prioritizes application source and currently inspects up to 24 files, so a clean result is not a full audit or proof that every possible issue was found. The CLI does not execute the target's code.

Before opening a PR, API Doctor checks that the proposed original text occurs exactly once in the current GitHub file, matches the cited line, and that the edited JavaScript or Python file parses. It opens one draft PR per finding. The maintainer must run relevant tests and review the change. An exposed credential requires rotation, so API Doctor does not open an automatic PR for that finding.

## Validation and limits

`npm test` covers repository and live modes, the bundled HTTP failure, Codex account gating, the interactive launcher, severity filtering, and GitHub's fork/branch/file/draft-PR request flow. Real read-only Luna scans of public repositories have also completed. Automated PR tests use a controlled GitHub API mock; real PRs require GitHub authorization and a repository that accepts them.

The end-to-end GitHub flow was also exercised on a fork of an [intentionally vulnerable training API](https://github.com/Dishan-Chalana/vulnerable-node-api). It opened three real draft PRs on the [practice fork](https://github.com/Stawberrymind/vulnerable-node-api): [login SQL injection](https://github.com/Stawberrymind/vulnerable-node-api/pull/1), [calculator code execution](https://github.com/Stawberrymind/vulnerable-node-api/pull/2), and [ping command injection](https://github.com/Stawberrymind/vulnerable-node-api/pull/3). These demonstrate the workflow and are not changes to the training upstream. That repository has no test script; the edited files passed syntax checks and their diffs were reviewed.

To try Odyssey yourself, paste the practice fork URL into the launcher and press Enter at the live URL prompt. Matching fixes may appear as existing PRs because those three drafts are already open.

Only probe APIs and repositories that teams have agreed to share. Use mock student, health, and financial data for the hackathon. Document any AI tools used in your submitted project README.

## AI tool disclosure

OpenAI Codex was used to implement API Doctor. API Doctor uses GPT-6 Luna through the Codex CLI for repository and incident analysis. The bundled demo and `--no-ai` mode do not call a model.
