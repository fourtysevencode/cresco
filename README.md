# Cresco

Cresco is a student app with two halves that share one student identity:

- **Wallet.** Students tap an NFC card at the school canteen or bookstore to pay. The reader reads only the card's serial number; the server maps it to the student and holds the balance. The cashier types the amount on the dashboard, and parents add money there.
- **AI tutor.** A student photographs a textbook page. The tutor explains it in Tamil, Kannada, Telugu, Bengali or Marathi (plus Hindi and English), reads it aloud, answers questions and quizzes the student. Quiz points feed a weekly school leaderboard; the top 3 win prizes (free ice cream, a book discount, Olympiad registration), claimed by tapping the same card.

```
backend/            FastAPI + PostgreSQL API (Python 3.12, uv)
  app/api/v1/       HTTP endpoints
  app/services/     ledger (money), scans (reader taps), ai_tutor (Gemini / Claude), tts (speech), points, leaderboard, rewards
  app/static/       dashboard.html: web dashboard at /dashboard (admin, cashier, parent, student)
  alembic/          database migrations
  tests/            pytest suite (runs against a real, embedded PostgreSQL)
cresco_pn523/       ESP32 + PN532 reader sketch (sends card serial numbers)
scripts/            sim_terminal.py (fake reader)
docs/API.md         endpoint reference
```

## Quick start

### Locally, zero setup

```bash
cd backend && uv run python dev_server.py
```

This starts an embedded PostgreSQL, applies migrations, loads demo data on first run and serves the API.
- Dashboard: http://localhost:8000/dashboard
- API docs: http://localhost:8000/docs
- Demo logins and reader secrets: `backend/data/demo-credentials.json`

All demo accounts use the password `cresco123`:

| Role | Login |
|---|---|
| Admin | `admin@demo.cresco` |
| Canteen cashier | `canteen@demo.cresco` |
| Parent | `parent@demo.cresco` |
| Students | `student1@demo.cresco` … `student5@demo.cresco` |

For the real AI tutor, put `GEMINI_API_KEY=...` in `backend/.env`. Without a key, the tutor returns a canned demo lesson. Read-aloud needs no key.

### With Docker

```bash
cp .env.example .env
```

Fill in `JWT_SECRET`, `TERMINAL_MASTER_KEY` and `GEMINI_API_KEY`.

```bash
docker compose up --build
```

```bash
docker compose exec api uv run python -m app.cli seed-demo
```

### Deploy on Vercel

1. Import the repo into Vercel with **Root Directory** set to `backend`.
2. Add Neon Postgres from Storage, with the env-var prefix `DATABASE`, so it creates `DATABASE_URL`.
3. Set these environment variables: `ENV=production`, `JWT_SECRET`, `TERMINAL_MASTER_KEY`, `CRON_SECRET` and `GEMINI_API_KEY`.
4. Create the tables from your laptop:
   ```bash
   cd backend && DATABASE_URL='<neon url>' uv run alembic upgrade head
   ```
   Then create the first admin:
   ```bash
   cd backend && DATABASE_URL='<neon url>' uv run python -m app.cli create-admin --school "…" --name "…" --email … --password …
   ```
5. For each reader, use **Readers → New secret** in the live dashboard. Secrets depend on the server's `TERMINAL_MASTER_KEY`.

### Try a tap without hardware

```bash
uv run --project backend python scripts/sim_terminal.py --terminal-id <id> --secret <secret> scan C0:DE:00:01
```

### Tests

```bash
cd backend && uv run pytest
```

The suite starts its own throwaway PostgreSQL, so no setup is needed.

## Design notes

**Money.** `app/services/ledger.py` is the only code that changes a balance.
- Each change locks the wallet row, appends to an append-only ledger and updates the balance in one transaction.
- A database CHECK keeps balances from going negative.
- Each reader tap is processed once, even if the reader retries after a WiFi drop.
- Top-ups are mocked for the demo: the parent's "Add money" button credits the wallet instantly. To take real payments, add a gateway webhook that calls `ledger.credit_topup`.

**Cards.** A card is identified only by its factory serial number (UID); nothing is written to it.
- To register a card, tap it on any reader. The dashboard's Register tab picks up the unknown serial number, and creating the student maps it to them.
- To pay, the cashier sets the amount on the dashboard and the student taps. With nothing pending, a tap just shows who the student is.
- Every reader request is HMAC-signed with a timestamp and nonce.

Serial numbers can be cloned onto "magic" cards, so treat the card like a canteen token:
- Keep the daily spend limit low.
- Parents can block a lost card instantly.
- The cashier sees the student's name on every tap.

NTAG 424 DNA cards (a fresh cryptographic code per tap) are the upgrade path.

**AI tutor.** Students use it from the dashboard's **Learn** tab:
- Take or upload photos of up to 5 pages. The browser shrinks them first, to stay under Vercel's 4.5 MB upload limit.
- Read the explanation section by section in their language, and press 🔊 to hear it.
- Ask the tutor follow-up questions.
- Take a quiz; points go to the weekly leaderboard.

How it works:
- The model reads the photos directly (vision), so there's no separate OCR step.
- Structured outputs return typed JSON for lessons and quizzes.
- Quiz answers never reach the browser until the student submits.
- The provider is **Google Gemini** (`GEMINI_API_KEY`, default model `gemini-3.8-flash`, free tier) or **Claude** (`ANTHROPIC_API_KEY`), chosen automatically from whichever key is set. Set `AI_PROVIDER` to force one.

**Speech.** Read-aloud is generated on the server as MP3, so it plays in any browser, even one with no Indian-language voices installed.
- The default provider is **edge-tts**: Microsoft's neural voices, free and with no API key.
- It is an unofficial use of Edge's public read-aloud service, so it could change without notice. That's fine for the demo; revisit before production.
- Each piece of text is synthesised once and cached.
