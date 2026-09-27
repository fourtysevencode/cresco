# Cresco

Cresco is a student app with two halves that share one student identity:

- **Wallet.** Students tap an NFC card at the school canteen or bookstore to pay. Parents add money to the wallet. The card stores only an ID and the student's name; the balance lives on the server.
- **AI tutor.** A student photographs a textbook page. The tutor explains it in Tamil, Kannada, Telugu, Bengali or Marathi (plus Hindi and English) and can read the explanation aloud. It also quizzes the student on the chapter. Quiz points feed a weekly school leaderboard, and the top 3 win prizes (free ice cream, a book discount, Olympiad registration). A student claims a prize by tapping the same card.

```
backend/            FastAPI + PostgreSQL API (Python 3.12, uv)
  app/api/v1/       HTTP endpoints
  app/services/     ledger (money), ai_tutor (Claude), tts (speech), points, leaderboard, rewards
  alembic/          database migrations
  tests/            pytest suite (runs against a real, embedded PostgreSQL)
firmware/reader_esp32/   ESP32 + MFRC522 reader sketch
scripts/            sim_terminal.py (fake reader), write_tag.md
docs/API.md         endpoint reference
```

## Quick start

### With Docker

```bash
cp .env.example .env
```

Fill in `JWT_SECRET`, `TERMINAL_MASTER_KEY` and `ANTHROPIC_API_KEY`. To run without keys, set `AI_PROVIDER=fake` and `TTS_PROVIDER=fake`.

```bash
docker compose up --build
```

```bash
docker compose exec api uv run python -m app.cli seed-demo
```

Open http://localhost:8000/docs.

### Without Docker

You need PostgreSQL 16.

```bash
cd backend && uv sync
```

```bash
cd backend && uv run alembic upgrade head
```

```bash
cd backend && uv run python -m app.cli seed-demo
```

```bash
cd backend && uv run uvicorn app.main:app --reload
```

`seed-demo` creates the following, and prints the terminal secrets plus the text to write on each student's card:
- a school with a canteen, a bookstore and two readers
- an admin, a parent and canteen staff
- five students with ₹200 each

All demo accounts use the password `cresco123`.

### Try a tap without hardware

```bash
uv run --project backend python scripts/sim_terminal.py --terminal-id <id> --secret <secret> charge --tag "CRESCO1|<token>|Arun" --amount 25
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
- Reader requests carry an idempotency key, so a retry after a WiFi drop never double-charges.
- Top-ups are mocked for the demo: the parent's "Add money" button credits the wallet instantly. To take real payments, add a gateway webhook that calls `ledger.credit_topup`.

**Card security.** A name + ID on an NFC tag is easy to copy, so the design limits the damage from a copied tag:
- The token is random and paired with the tag's hardware UID.
- Every reader request is HMAC-signed with a timestamp and nonce.
- Parents set a daily spend limit and can block a lost card instantly.

For stronger protection later, NTAG 424 DNA tags generate a fresh cryptographic code on every tap.

**AI tutor.**
- Claude reads the photos directly (vision), so there's no separate OCR step.
- Structured outputs return typed JSON for lessons and quizzes.
- The default model is `claude-opus-5`, with effort set by `CLAUDE_EFFORT`.
- Requests opt into Anthropic's server-side refusal fallback, so a request declined by a safety classifier is retried automatically on a fallback model.
- Quiz answers never reach the client before the student attempts the quiz.

**Speech.** Read-aloud uses Google Cloud Text-to-Speech, which has voices for all seven languages and a free monthly quota. Each piece of text is synthesised once and cached, so replays are free. To run fully free on your own GPU, the `TTSProvider` interface in `app/services/tts.py` can take a self-hosted model such as AI4Bharat Indic Parler-TTS.
