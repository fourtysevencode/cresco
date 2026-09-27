## [DECISIONS]

- 2026-09-27T08:38:19Z [USER] Repo-only PR scope is critical, high, and medium findings plus lower-severity logical errors that prevent startup or a core API/demo flow. Ordinary low-severity findings are excluded.
- 2026-09-27T07:12:00Z [USER] Asked to find an online repository for a real PR trial. Chose an intentionally vulnerable training API and created a practice fork under the signed-in account, avoiding unsolicited PRs to upstream.
- 2026-09-27T06:22:49Z [USER] Pivoted the new repo-only and repo-plus-live investigation workflow to CLI only.
- 2026-09-27T06:57:00Z [USER] Requested automatic draft fix PRs: live API failures when a URL is available, critical security findings when it is not.
- 2026-09-27T06:57:00Z [CODE] Luna review now runs by default for real inspections. Missing or inaccessible live endpoints fall back to critical repo-only review. PRs require a fresh exact source match and syntax-valid edit; GitHub forks are used without direct push access.
- 2026-09-27T05:00:28Z [USER] Track 7 is “API Doctor (Odyssey)”: watch other teams' demo APIs, diagnose stack traces/logs, and open fix PRs. Likely target stacks include Node and Python/FastAPI.

## [PROGRESS]

- 2026-09-27T08:47:56Z [CODE] CLI now accepts matching Markdown URL links, an escaped URL scheme colon, and accidental `-- live` as `--live`. Fifteen tests pass.
- 2026-09-27T08:47:56Z [TOOL] Corrected read-only invocation against fourtysevencode/cresco scanned 24 files; deployed `/health` returned HTTP 200. No PR was created.
- 2026-09-27T08:38:19Z [CODE] Added category and blocksProject to Luna schema, narrowed the prompt, and enforced the same eligibility rule at the GitHub PR boundary. README and CLI help now describe the threshold.
- 2026-09-27T08:38:19Z [TOOL] Fifteen tests and syntax checks pass. A read-only, signed-in Luna run on the bundled API returned one medium logic finding using the new schema; no PR was opened.
- 2026-09-27T07:12:00Z [CODE] Added quiet Git credential helper fallback and duplicate-PR detection. Relaxed exact-source line check to include the full matched snippet plus three lines. Thirteen tests pass.
- 2026-09-27T07:12:00Z [TOOL] Opened three real draft PRs on Stawberrymind/vulnerable-node-api: #1 parameterized login SQL, #2 restricted calculator expression, #3 execFile ping. All target main and are attached to this task.
- 2026-09-27T06:57:00Z [CODE] Added Luna finding schema, exact-edit draft PR creation, fork flow, duplicate-PR marker, CLI dual path, and revised README. Twelve tests pass.
- 2026-09-27T06:36:46Z [CODE] Added `npm run doctor -- demo`, a network-free CLI smoke test that starts the bundled broken Node API, detects its HTTP 500, and previews the patch. Ten tests pass.
- 2026-09-27T06:22:49Z [CODE] Added `npm run doctor -- inspect` with repo-only GitHub source scan, optional live GET probe/watch, optional Luna Fast source review, JSON output, and supported-case draft PR switch.
- 2026-09-27T06:22:49Z [TOOL] CLI tests cover both modes, a real bundled Node 500, an opaque live 500, and refusal of API-key auth for the optional Luna review.
- 2026-09-27T05:00:28Z [CODE] Built a dependency-free Node dashboard, bundled crashing Node API, Python traceback example, log ingest endpoint, polling, diagnosis, patch preview, and GitHub draft PR integration.
- 2026-09-27T05:00:28Z [TOOL] Four tests pass, including the HTTP demo and mocked GitHub PR creation. Browser check displayed both incidents and patch previews.
- 2026-09-27T05:07:08Z [USER] Supplied the Slash midnight-vault style reference and asked the dashboard to follow it.
- 2026-09-27T05:07:08Z [CODE] Restyled the dashboard with near-black surfaces, editorial serif headings, copper category labels, hairline dividers, pill controls, and a live incident ledger. Four tests pass; the responsive browser view was checked.

## [DISCOVERIES]

- 2026-09-27T07:12:00Z [TOOL] Existing Git credential works with GitHub API and has push access to the practice fork. The upstream training app has no test script; proposed JavaScript files passed syntax validation and PR diffs were reviewed.
- 2026-09-27T06:57:00Z [TOOL] Initial Codex OAuth token exchange failed in sandboxed network. Retrying with network access succeeded; elevated `codex login status` reports ChatGPT sign-in. A real read-only Luna Fast scan of `karpathy/micrograd` returned no critical finding. GitHub token and target team repository are still absent, so no external PR was opened.
- 2026-09-27T05:00:28Z [TOOL] This directory is an untracked subfolder of the parent `cresco` Git repository; no GitHub token was available, so no real PR was created.
