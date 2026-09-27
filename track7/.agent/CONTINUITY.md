## [DECISIONS]

- 2026-09-27T06:22:49Z [USER] Pivoted the new repo-only and repo-plus-live investigation workflow to CLI only.
- 2026-09-27T06:22:49Z [CODE] Repo-only findings are labeled possible; live HTTP 500 is confirmed, but its root cause stays unknown without a useful trace. Luna source review is optional and requires ChatGPT CLI sign-in.
- 2026-09-27T05:00:28Z [USER] Track 7 is “API Doctor (Odyssey)”: watch other teams' demo APIs, diagnose stack traces/logs, and open fix PRs. Likely target stacks include Node and Python/FastAPI.
- 2026-09-27T05:00:28Z [CODE] Keep automatic source edits limited to verified missing-progress demo cases. Other errors receive a diagnosis without a guessed patch.

## [PROGRESS]

- 2026-09-27T06:36:46Z [CODE] Added `npm run doctor -- demo`, a network-free CLI smoke test that starts the bundled broken Node API, detects its HTTP 500, and previews the patch. Ten tests pass.
- 2026-09-27T06:22:49Z [CODE] Added `npm run doctor -- inspect` with repo-only GitHub source scan, optional live GET probe/watch, optional Luna Fast source review, JSON output, and supported-case draft PR switch.
- 2026-09-27T06:22:49Z [TOOL] CLI tests cover both modes, a real bundled Node 500, an opaque live 500, and refusal of API-key auth for the optional Luna review.
- 2026-09-27T05:00:28Z [CODE] Built a dependency-free Node dashboard, bundled crashing Node API, Python traceback example, log ingest endpoint, polling, diagnosis, patch preview, and GitHub draft PR integration.
- 2026-09-27T05:00:28Z [TOOL] Four tests pass, including the HTTP demo and mocked GitHub PR creation. Browser check displayed both incidents and patch previews.
- 2026-09-27T05:07:08Z [USER] Supplied the Slash midnight-vault style reference and asked the dashboard to follow it.
- 2026-09-27T05:07:08Z [CODE] Restyled the dashboard with near-black surfaces, editorial serif headings, copper category labels, hairline dividers, pill controls, and a live incident ledger. Four tests pass; the responsive browser view was checked.

## [DISCOVERIES]

- 2026-09-27T06:22:49Z [TOOL] `codex login status` returned “Not logged in” on this machine; the optional Luna path cannot be end-to-end tested until CLI ChatGPT sign-in. A read-only scan of `karpathy/micrograd` succeeded against real GitHub with escalated network access.
- 2026-09-27T05:00:28Z [TOOL] This directory is an untracked subfolder of the parent `cresco` Git repository; no GitHub token was available, so no real PR was created.
