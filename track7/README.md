# API Doctor · Odyssey

An opt-in observability bot for HackBlitz Mission 7. It checks demo API endpoints, accepts stack traces through a log-ingest endpoint, identifies a likely cause, and prepares a narrow source patch. When GitHub access is configured, it opens a **draft** fix pull request for a supported failure.

## Run the demo

Requires Node.js 20 or newer. No package installation is needed.

```bash
npm start
```

Open <http://127.0.0.1:4100>. Click **Run live API check**. The bundled Campus Progress API returns a real 500 error; API Doctor captures its Node stack trace, locates the failing line, and displays the proposed fix. Click **Inject Python traceback** to see the same flow for a Python-style failure log.

```bash
npm test
```

## Connect other teams' APIs

Copy `targets.example.json` to `targets.json` and add only teams that agree to participate. Each entry needs a unique ID, display name, health or demo URL, GitHub `owner/repo`, and the repository path of the failing source file. The bot polls URLs every 15 seconds. To supply stack traces that are not included in the HTTP response, forward a log to:

```http
POST /api/ingest
Content-Type: application/json
X-Ingest-Secret: <optional shared secret>

{"targetId":"team-example","log":"<stack trace>"}
```

Set `INGEST_SECRET` in the bot's environment to require that header. The local server binds to `127.0.0.1`; a hosted deployment would need an HTTPS endpoint or a private tunnel for remote log forwarding. Do not forward real student or financial data. The demo uses mock data only.

## Enable draft fix PRs

Set these environment variables and restart the bot:

```text
AUTO_PR=true
GITHUB_TOKEN=<fine-grained token with Contents: write and Pull requests: write on the opted-in repository>
GITHUB_REPOSITORY=owner/repo
GITHUB_FILE_PATH=track7/demo/target-api.js
PYTHON_FILE_PATH=track7/demo/python-api.py
```

`GITHUB_REPOSITORY` and the file-path variables connect the bundled examples. For other teams, put `repository` and `filePath` in `targets.json`. The bot reads the repository's default branch, creates an `api-doctor/fix-*` branch, commits the patch, and opens a draft PR. The target team must grant the token repository access. Never put the token in `targets.json` or commit it.

The current automatic repair rules cover the two bundled missing-progress cases. Other Node and Python traces can receive a diagnosis, but the bot does not guess a source edit when it cannot verify a safe patch. A production version would add more validated repair recipes, tests in a disposable checkout, deployment, and stronger access control.

## Pitch demo path

1. Show the watched Campus Progress API and run the live check.
2. Open the 500 incident: the stack location, root cause, and before/after patch are visible.
3. Show a draft PR when connected to a demo repository, or show the exact patch preview.
4. Inject the Python traceback to show the bot handles both ecosystems.
5. Explain that other teams opt in with a URL, log forwarder, and repository permission.

## AI tool disclosure

OpenAI Codex was used to design and implement this prototype, its demo UI, tests, and documentation. No AI model is called by the running bot; its current diagnoses and patches use explicit rules.
