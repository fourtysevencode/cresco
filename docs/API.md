# Cresco API reference (v1)

The web dashboard is at **`/dashboard`**. Interactive API docs, where you can try every endpoint, are at **`/docs`**.

## Conventions

- Money is always an integer number of **paise** (₹1 = 100). For example, `balance_paise: 15500` is ₹155.00.
- Users authenticate with `Authorization: Bearer <access_token>` from `/v1/auth/login`. Access tokens last 30 min; get a new one from `/v1/auth/refresh`.
- ESP32 readers authenticate by signing each request (see [Terminal signing](#terminal-signing)).
- Errors look like `{"detail": "<code>"}`, e.g. `student_not_found`, `account_exists`, `ai_busy`.
- Languages are `ta` Tamil, `kn` Kannada, `te` Telugu, `bn` Bengali, `mr` Marathi, `hi` Hindi and `en` English.

## Roles

| Role | Created by | Can |
|---|---|---|
| `admin` | `python -m app.cli create-admin` | Manage their school's students, parents, cards, shops, readers and prizes |
| `parent` | admin | See their children's wallets, add money, set spend limits, block lost cards, see lessons |
| `student` | admin | Photo → lesson, ask the tutor, take quizzes, see points/leaderboard/rewards |
| `merchant_staff` | admin | See their shop's sales and refund them |

## Endpoints

### Auth
| | | |
|---|---|---|
| `POST` | `/v1/auth/login` | `{login: email or phone, password}` → `{access_token, refresh_token}` |
| `POST` | `/v1/auth/refresh` | `{refresh_token}` → new tokens |
| `GET` | `/v1/auth/me` | Current user |

### Admin (school)
| | | |
|---|---|---|
| `POST` | `/v1/admin/students` | **Create a user mapped to a card:** `{name, grade, tag_uid?, preferred_language?, daily_limit_paise?, email/phone + password?}`. Creates the wallet; with `tag_uid` the card works immediately. The login is optional and only needed for the AI tutor |
| `GET` | `/v1/admin/students` | List the school's students |
| `POST` | `/v1/admin/parents` | Create a parent account |
| `POST` | `/v1/admin/parents/{parent_id}/students` | Link a parent to a student |
| `POST` | `/v1/admin/merchant-staff` | Create a canteen/bookstore staff account |
| `POST` | `/v1/admin/students/{id}/cards` | `{tag_uid}`: map a card (by serial number) to a student. Retires their previous card |
| `GET` | `/v1/admin/cards/lookup?tag_uid=04:A1:B2:C3` | Who a serial number belongs to |
| `GET` | `/v1/admin/scans?unknown_only=true` | Recent taps on the school's readers. Unknown cards show up here, ready to register |
| `GET` | `/v1/admin/merchants` · `/v1/admin/terminals` | Shops and readers (with last-seen time) |
| `PATCH` | `/v1/admin/cards/{card_id}` | Block/unblock a card, change its daily limit |
| `POST` | `/v1/admin/merchants` | Create a shop: `kind` is `canteen` or `bookstore` |
| `POST` | `/v1/admin/terminals` | Register a reader → `terminal_id` + `secret` (shown once) |
| `POST` | `/v1/admin/terminals/{id}/rotate-secret` | New secret; the old one stops working |
| `GET`/`PUT` | `/v1/admin/reward-config` | Prize for each leaderboard rank |
| `POST` | `/v1/admin/leaderboard/close-week?week=2026-W39` | Issue a week's prizes now (defaults to last week). Also runs automatically Sundays 23:59 IST |

### Wallet (parents, students)
| | | |
|---|---|---|
| `GET` | `/v1/parents/me/children` | Each child's balance, card status, daily limit and today's spend |
| `GET` | `/v1/wallets/{student_id}` | One wallet (the student themself, a linked parent, or an admin) |
| `GET` | `/v1/wallets/{student_id}/transactions?limit=50&before=<created_at>` | History, newest first |
| `POST` | `/v1/wallets/{student_id}/topups` | **Mock "Add money" button** (parent): `{amount_paise, idempotency_key?}` credits instantly. No real payment |
| `PATCH` | `/v1/students/{student_id}/spend-limit` | Parent sets the card's daily limit |
| `POST` | `/v1/students/{student_id}/card/block` | Parent reports the card lost; it stops working immediately |

### Terminal (ESP32 + PN532 readers, signed)
| | | |
|---|---|---|
| `POST` | `/v1/terminal/heartbeat` | Connectivity + auth check (the reader sends one every 60 s) |
| `POST` | `/v1/terminal/scan` | `{tag_uid, scan_id}` → `{result, reason, name, balance_paise, amount_paise, reward}` |

The reader only sends the card's serial number. The server decides what the tap means:

| `result` | When |
|---|---|
| `approved` | A charge (or prize claim) was pending on this reader and it went through |
| `declined` | A charge was pending but failed; see `reason`. The charge stays open so the student can tap again |
| `identified` | Nothing pending; `name` and `balance_paise` say who tapped |
| `unknown` | The serial number isn't registered (`reason: unregistered_card`) |

A declined tap carries one of these `reason`s:

| `reason` | Meaning |
|---|---|
| `insufficient_balance` | Not enough money in the wallet |
| `daily_limit` | Today's spending would go over the card's daily limit |
| `card_blocked` | Card is blocked, lost or replaced |
| `wrong_school` | Student is from a different school than this shop |
| `no_reward` | Prize claim, but the student has no prize for this shop |

### Counter (cashier dashboard: merchant staff for their shop's readers, admins for any)
| | | |
|---|---|---|
| `GET` | `/v1/merchant/terminals` | Readers you can operate |
| `POST` | `/v1/merchant/terminals/{id}/pending` | `{kind: "charge", amount_paise}` or `{kind: "redeem"}`: what the next tap does. Replaces anything pending; lapses after 2 minutes |
| `DELETE` | `/v1/merchant/terminals/{id}/pending` | Cancel |
| `GET` | `/v1/merchant/terminals/{id}/status` | Poll about once a second: what's pending and the last tap's result |

### Merchant staff
| | | |
|---|---|---|
| `GET` | `/v1/merchant/transactions?today_only=true` | The shop's sales |
| `POST` | `/v1/merchant/transactions/{entry_id}/refund` | Full refund. A second refund of the same sale returns 409 |

### Learning (students; parents can read)
| | | |
|---|---|---|
| `POST` | `/v1/lessons` | multipart: `images` (1–5 JPEG/PNG/WEBP pages, ≤5 MB each), `language?`, `subject?` → lesson with `extracted_text` and `explanation {summary, sections[], key_terms[]}`. Re-uploading the same photos returns the existing lesson (`deduplicated: true`) without calling the AI. An unreadable photo returns 422 with a `tip` on how to retake it |
| `GET` | `/v1/lessons?student_id=` | Lesson list |
| `GET` | `/v1/lessons/{id}` | Lesson + follow-up chat |
| `GET` | `/v1/lessons/{id}/audio?section=n` | MP3 of the summary (no `section`) or of one section, in the lesson's language |
| `POST` | `/v1/lessons/{id}/messages` | `{question}` → tutor reply in the lesson's language |
| `GET` | `/v1/lessons/{id}/messages/{index}/audio` | MP3 of a chat message |
| `POST` | `/v1/lessons/{id}/quiz` | New multiple-choice quiz. Correct answers are **not** included |
| `GET` | `/v1/quizzes/{id}` | Quiz without answers |
| `POST` | `/v1/quizzes/{id}/attempts` | `{answers: [0-based option per question]}` → score, points, and per-question correct answer + explanation |

### Leaderboard and rewards
| | | |
|---|---|---|
| `GET` | `/v1/leaderboard/weekly?week=` | School standings (default: this week) + `me` |
| `GET` | `/v1/leaderboard/weekly/winners?week=` | Prize winners (default: last week) |
| `GET` | `/v1/students/me/points` | This week, all time, and this week's rank |
| `GET` | `/v1/students/me/rewards` | Prizes and their status: `issued`, `redeemed` or `expired` |

## Scoring rules

- **10 points** per correct answer, plus **20** for full marks.
- Only the **first attempt** at a quiz scores. Retries are graded but earn 0.
- At most **5 scoring quizzes per day**, so practice beyond that doesn't inflate the leaderboard.
- Weekly ranking is by points. On a tie, whoever reached the score first ranks higher.
- Default prizes:
  - 1st: free Olympiad registration (bookstore)
  - 2nd: ₹200 off at the bookstore
  - 3rd: free ice cream at the canteen

  Admins can change these through `/v1/admin/reward-config`.
- Prizes expire after 14 days. The student claims one by tapping their card after the cashier clicks **Redeem a prize** on the dashboard.

All of these numbers are settings in `backend/app/core/config.py`.

## Terminal signing

Every `/v1/terminal/*` request carries four headers:

| Header | Value |
|---|---|
| `X-Terminal-Id` | The reader's id |
| `X-Timestamp` | Unix seconds (UTC); must be within 60 s of the server's clock |
| `X-Nonce` | 8–64 random characters, never reused |
| `X-Signature` | `hex(HMAC_SHA256(secret, "<X-Timestamp>.<X-Nonce>.<raw request body>"))` |

`scripts/sim_terminal.py` is the reference implementation, and `cresco_pn523/cresco_pn523.ino` has the C++ version.

Readers must retry a timed-out scan with the **same** `scan_id` and a new nonce. The server returns the original result, so the student is charged once.
