# SAGE

SAGE — Smart Assistance, Guidance & Ease — is a Flask chat app with personal
memory, built on a small Python client for [Token Harbor](https://tokenharbor.ai):
one OpenAI-compatible gateway for many models. The client handles message
passing, token streaming, conversation history, tool calling, and typed error
handling.

- **Gateway:** `https://tokenharbor.ai/v1` (OpenAI-compatible `/v1/chat/completions`)
- **Auth:** universal key `thk_live_...`, read from the environment
- **Streaming:** Server-Sent Events, exposed as an iterator of text deltas
- **Tools:** `@tool` decorator + automatic tool-calling loop
- **Errors:** every HTTP status mapped to a clear code, with retries and backoff

## Project layout

```
.
├── app.py                 # Flask app: pages, auth API, chat API, memory tools
├── auth.py                # users, password hashing, JWTs, auth middleware
├── logging_config.py      # structured logs, request IDs, rotating file
├── memory.py              # personal memory schema, queries, agent tools
├── voice.py               # ElevenLabs web-request client for call mode
├── templates/
│   ├── login.html         # email + password
│   ├── register.html      # first name, last name, email, password
│   ├── chat.html          # greeting + chat UI
│   └── call.html          # voice-call UI
├── static/
│   ├── style.css
│   ├── call.css           # voice-call screen styles
│   ├── theme.js           # pre-paint light/dark bootstrap
│   ├── session.js         # token storage + fetch wrapper (Bearer header)
│   ├── auth.js            # login/register form handling
│   ├── chat.js            # streams replies, keeps history per user
│   └── call.js            # mic input, call stream, full-reply TTS
├── docs/
│   ├── DESIGN.md          # design system, components, screens, React Native handoff
│   └── API.md             # every HTTP endpoint, payload, error, TypeScript client
├── prompts/
│   ├── english.md         # SAGE system prompt (English)
│   ├── arabic.md          # SAGE system prompt (Arabic)
│   ├── english-call.md    # spoken call persona (English)
│   └── arabic-call.md     # spoken call persona (Arabic)
├── core/
│   ├── __init__.py        # package exports
│   └── core.py            # TokenHarbor client, streaming, history, tools
├── tests/
│   ├── test_auth.py       # registration, login, JWT and middleware tests
│   ├── test_app.py        # Flask app tests (stubbed client, no network)
│   ├── test_core.py       # client unit tests (local mock gateway, no network)
│   ├── test_memory.py     # entities, relationships, facts, tool handlers
│   ├── test_logging.py    # formatters, request IDs, file rotation, redaction
│   ├── test_voice.py      # ElevenLabs payload/token/chunking/logging tests
│   └── integration/
│       └── test_integration.py   # live tests against tokenharbor.ai
├── instance/app.db        # SQLite user database (created at runtime, gitignored)
├── requirements.txt
├── .env.example           # template for your API key
├── .gitignore
└── README.md
```

Two references for building another client (for example a React Native app):

- [`docs/DESIGN.md`](docs/DESIGN.md) — design tokens, components, every screen, and the
  native porting notes.
- [`docs/API.md`](docs/API.md) — all 55 routes with payloads, SSE frames, error codes, and
  TypeScript examples.

## Requirements

- Python 3.10+
- `Flask`, `requests`, `python-dotenv`, `PyJWT`

```bash
pip install -r requirements.txt
cp .env.example .env      # then paste your real key
```

Get a key at <https://tokenharbor.ai/dashboard/api-keys>. The client reads
`TH_API_KEY` (falling back to `TOKENHARBOR_API_KEY` / `TOKENHARBOR_KEY`) and
auto-loads a local `.env` if `python-dotenv` is installed.

> `.env` is gitignored. Never commit your real key.

## Web app (Flask chat)

```bash
pip install -r requirements.txt
python app.py          # http://127.0.0.1:5000
```

How it works:

1. **`/`** — redirects to `/login`.
2. **`/register`** — first name, last name, email and password. Signing up logs
   you straight in.
3. **`/login`** — email + password.
4. **`/chat`** — greets you by your first name and lets you chat; `log out` sits
   in the header.
5. **`POST /api/chat/stream`** — the server calls Token Harbor and streams the
   reply back token by token over SSE.
6. **`POST /api/chat`** — non-streaming fallback returning `{"reply": "..."}`.
7. **Conversation history** — every turn is also written to the server, so
   transcripts survive a cleared browser. `GET /api/conversations` lists the
   signed-in user's threads and `GET /api/conversations/<id>` returns one thread
   with its turns. See “Conversation history” below.
8. **Memory tools** — the model can save and recall personal facts, and ask for
   the account name. Tool activity is streamed as transient status frames, so
   the UI can show “Saving memory...” without inventing assistant text.
9. **`/settings`** — update your name and email, or change your password. The
   avatar in the chat header links here.
10. **`/call`** — a voice-call screen: speak (or type) and hear SAGE reply using
    the ElevenLabs web request. The chat header links to it. See “Voice calls”
    below.

## Conversation history

The database is the **source of truth** for the chat transcript. The browser no
longer keeps its own copy, and it no longer sends history to the server: it
sends one message, and the server reloads the thread from SQLite.

Two tables hold it:

- `conversations` — one row per thread (`mode`, `language`, `started_at`,
  `ended_at`).
- `conversation_turns` — one row per message, with `role`, `content`,
  `created_at`, `finish_reason`, `model`, `latency_ms`, `outcome`, and the names
  of any tools that ran.

| Method | Endpoint | Result |
| --- | --- | --- |
| `GET` | `/api/conversations/current` | `200 {conversation, turns}`; creates the thread on first use |
| `GET` | `/api/conversations` | `200 {conversations: [...]}` newest first |
| `GET` | `/api/conversations/<id>` | `200 {conversation, turns}` or `404 not_found` |
| `DELETE` | `/api/conversations` | `204`; deletes every thread for the caller ("Clear") |
| `DELETE` | `/api/conversations/<id>/turns/<turn_id>` | `204`; a user turn also deletes its reply |
| `DELETE` | `/api/conversations/<id>` | `204`; deletes one thread and its turns |

**There is one ongoing thread per account.** `GET /api/conversations/current`
returns the most recent one and creates it if the user has none.

**The model reads the database.** `POST /api/chat` and `/api/chat/stream` accept:

```jsonc
{ "conversation_id": "...", "message": "what the user just typed", "language": "en" }
```

The server appends the user turn, reloads the thread, and sends *that* to the
model. Client-supplied history is ignored on this path, so a modified browser
cannot inject assistant turns. Only the most recent 50 turns are sent, so a long
thread cannot outgrow the model's context. The legacy `messages` array still
works for the call page, which keeps its own in-memory history.

Behaviour worth knowing:

- **Deleting a message deletes it from the database** and the bubble is removed
  from the page. Removing a user message also removes the assistant reply that
  followed it, matching the bubble rule in the UI.
- **"Clear" deletes every thread** for the account, not just the current one. A
  database created before the single-thread change holds one conversation per
  message, so deleting only the newest would make an old thread reappear on the
  next load.
- **Stopping a reply deletes the pair.** The UI rolls a stopped turn back, so the
  stored rows go too. A dropped connection is *not* a rollback: the user turn
  and any partial reply are kept and flagged `outcome: "stopped"`.
- **No refetch after a reply.** The stream ends with a `{"saved": {...}}` frame
  carrying `conversation_id`, `user_turn_id`, and `assistant_turn_id`. The page
  attaches those to the bubbles it already rendered, so sending a message costs
  one request instead of two. It only reloads the thread on first load, or if
  persistence failed and no ids came back.

- **Persistence is best effort.** A database error while recording a turn never
  fails the reply the user is waiting for.
- **Failed turns are recorded** with `outcome: "error"`. An assistant row with no
  text is not rendered as a bubble.
- **Message text is never logged.** `conversation.sql` logs the statement with
  `?` placeholders, matching the memory SQL log.
- Deleting a user cascades to their conversations and turns.

## Controlling your memory

`/memory` is a public HTML shell (like `/settings`) that lists everything stored
for the signed-in account and lets the user act on it.

- **See it.** `GET /api/memory` returns the active entities, relationships, and
  facts; add `?include_expired=true` to include superseded values.
- **Delete it.** `DELETE /api/memory/fact/<id>`, `/entity/<id>`, or
  `/relationship/<id>` removes one record. Deleting an entity cascades to its
  facts and relationships. Unknown or foreign ids return `404`, so an id is
  never a way into another account.
- **Forget everything.** `DELETE /api/memory` requires `{"confirm": true}` and
  wipes all three stores for that account only.
- **Export it.** `GET /api/memory/export` streams a JSON download. The export is
  built on demand and never stored server-side, so there is no second copy to
  secure or forget.

User-initiated deletion is a **hard delete**, unlike a superseded fact which
keeps its row with `valid_until`. Deletion never removes consent records.

## Consent

Consent is stored per account in the `consents` table and is checked
server-side, never trusted from the client.

| Kind | Gates |
| --- | --- |
| `location` | storing a position via `POST /api/location` |
| `voice` | voice replies |
| `memory_sharing` | sharing memory with trusted people |
| `familiar_voice` | familiar-voice playback |

`GET /api/consents` reports every kind; `PUT /api/consents/<kind>` with
`{"granted": true|false}` grants or revokes one. Each entry also carries a
`default` flag so the UI can tell a default from an explicit choice.

**`location` is granted by default.** Every other kind requires an explicit
opt-in. A revoke always wins over the default, so turning location off sticks
until the user turns it back on — it does not silently revert on the next
request. Users still control the browser-level geolocation prompt separately,
so no position is sent until the browser itself allows it.

`POST /api/location` returns `403 consent_required` while the `location` consent
is off, so the `/chat` and `/call` pings stop storing anything until the user
turns it back on in `/memory`.

## Appointments, tasks, and routines

Memory holds durable facts; scheduled things need a different shape. `schedule.py`
adds two tables, both scoped to the signed-in user:

- `appointments` — `title`, `starts_at`, optional `ends_at`, `place`, `notes`,
  and a `status` of `pending`, `done`, or `cancelled`.
- `routines` — a repeating habit with `label`, `cadence` (e.g. `daily`), and an
  optional `window` (e.g. `08:00-09:00`).

**Timezones.** Every timestamp is stored as ISO-8601 **UTC**. A client sending
`2026-05-04T16:00:00+02:00` is stored as `2026-05-04T14:00:00Z` and rendered in
the user's local time. `GET /api/appointments/today` takes `tz_offset` in
minutes east of UTC (`-new Date().getTimezoneOffset()`) so "today" means the
user's day, not the server's.

**Listing.** `GET /api/appointments` returns upcoming items soonest-first.
Completed and cancelled items are hidden unless `include_done` is set. An
appointment that started earlier but ends later still appears in a window, so a
multi-day event is not lost.

**Reminders** are in-app only: `/chat` shows a banner with the next appointment
today (`static/schedule-reminder.js`). There is no push or email delivery.

**Model tools.** The chat model gets `save_appointment`, `list_appointments`, and
`complete_appointment` alongside the memory tools. Anything with a date or time
should go to `save_appointment`, not `save_memory`. Tool handlers bind the user
id server-side and drop any model-supplied `user_id`.

## Familiar places and location context

Location pings used to be write-only. `places.py` turns a position into
something useful.

- A **place** is a named circle: `name`, `latitude`, `longitude`, `radius_m`
  (10–50,000 m, default 150), and optional `notes`. The user saves one from
  their current position on `/schedule`, so the browser supplies the
  coordinates rather than the model.
- Matching runs **on the server** with a haversine distance. The nearest place
  whose radius contains the point wins; ties break on distance.
- `POST /api/location` now returns `204` when the position matches nothing, and
  `200 {"context": ...}` when it matches a place. The context carries the
  place, the facts of any entity with the same name, and the next appointment.
- `GET /api/location/context?latitude=…&longitude=…` returns the same payload
  without storing the position, and requires the `location` consent.
- `/chat` shows a banner (“You are at Mom's house · Next: Call her”) from
  `static/location.js`.

**Privacy.** The context payload never includes `latitude`/`longitude`: the
browser already knows where it is, and echoing a saved place's coordinates back
would leak the user's own data for no benefit. Coordinates are never logged.

**An unfamiliar place is neutral.** It produces no context, no warning, and no
inference about the user's state. The requirement is explicit that being
somewhere new must not be treated as confusion or danger.

## Trusted people

A user can nominate someone — by email — who is allowed to see their activity,
so a supporter can notice when they have gone quiet. `/trusted` is the public
shell for it.

**Three gates, all required.** Nothing is visible until:

1. the invitation is **accepted** by the invited email,
2. it is **not revoked** by either side, and
3. the owner grants the **`trusted_sharing`** consent (default **off**).

Every read re-checks all three; nothing is cached and nothing is trusted from
the client.

**What a trusted person can see.** `GET /api/trusted/people/<id>` returns:

| Field | Meaning |
| --- | --- |
| `last_seen` | the newer of `last_message_at` and `last_location_at` |
| `location` | `{latitude, longitude, at}`, or `null` when not shared |
| `warning` | `true` once `last_seen` is older than 24 hours |
| `status` | `active`, `stale`, or `unknown` |

Per-contact permissions (`can_see_last_seen`, `can_see_location`) are chosen at
invite time, so a viewer can follow last-seen without seeing a position.

**The 24-hour warning is a prompt, not a diagnosis.** `status` is `unknown` —
not `stale` — for someone who has never been active, so a new account never
raises a false alarm. The warning means "no activity recorded", which can simply
mean the app was closed; it must never be worded as evidence that something is
wrong.

**The `trusted_sharing` switch.** It defaults to **off**, so an accepted
invitation shows the person as "sharing paused" until the owner turns it on. The
switch lives at the top of `/trusted` (and also appears on `/memory` with the
other permissions). A viewer cannot enable it for someone else — only the owner
can.

`GET /api/trusted/watching` lists every accepted contact with a `sharing` flag,
even while that flag is `false`. Hiding the row would make an accepted
invitation look like it vanished, and the viewer has no way to fix it.
`GET /api/trusted/people/<id>` still returns `403` while sharing is off, so the
flag is a label, not data.

**No email enumeration.** `POST /api/trusted` answers `201` whether or not the
address has an account, so it cannot be used to probe for SAGE users.

## Accounts & authentication

Accounts live in a small SQLite database (`instance/app.db`, override with
`TH_DB_PATH`). Passwords are hashed with **Werkzeug scrypt** — never stored or
logged in plain text.

Logging in returns a **JWT** (HS256, 7 days) that the browser keeps in
`localStorage` and sends on every request as `Authorization: Bearer <token>`:

```json
{
  "sub": "1",
  "id": 1,
  "email": "youssef@example.com",
  "full_name": "Youssef Smith",
  "iat": 1763400000,
  "exp": 1764004800
}
```

| Method | Endpoint | Body | Success |
| --- | --- | --- | --- |
| `POST` | `/api/auth/register` | `first_name, last_name, email, password` | `201 {token, expires_at, user}` |
| `POST` | `/api/auth/login` | `email, password` | `200 {token, expires_at, user}` |
| `GET` | `/api/auth/me` | – | `200 {user}` |
| `PATCH` | `/api/auth/profile` | `first_name, last_name, email, current_password`* | `200 {token, expires_at, user}` |
| `POST` | `/api/auth/password` | `current_password, new_password, confirm_password` | `204` |
| `POST` | `/api/auth/logout` | – | `204` |
| `POST` | `/api/location` | `latitude, longitude` | `204` |
| `GET` | `/api/conversations` | – | `200 {conversations}` |
| `GET` | `/api/conversations/current` | – | `200 {conversation, turns}` |
| `GET` | `/api/conversations/<id>` | – | `200 {conversation, turns}` |
| `DELETE` | `/api/conversations/<id>/turns/<id>` | – | `204` / `404` |
| `DELETE` | `/api/conversations/<id>` | – | `204` / `404` |
| `GET` | `/api/memory` | – | `200 {entities, relationships, facts}` |
| `GET` | `/api/memory/export` | – | `200` JSON download |
| `DELETE` | `/api/memory/fact/<id>` | – | `204` / `404` |
| `DELETE` | `/api/memory/entity/<id>` | – | `204` / `404` |
| `DELETE` | `/api/memory/relationship/<id>` | – | `204` / `404` |
| `DELETE` | `/api/memory` | `confirm: true` | `200 {deleted}` |
| `GET` | `/api/consents` | – | `200 {consents}` |
| `PUT` | `/api/consents/<kind>` | `granted` | `200 {kind, granted, ...}` |
| `GET` | `/api/appointments` | – | `200 {appointments}` |
| `GET` | `/api/appointments/today` | `tz_offset`* | `200 {from, to, appointments}` |
| `POST` | `/api/appointments` | `title, starts_at, ...` | `201 {appointment}` |
| `PATCH` | `/api/appointments/<id>` | `status` | `200 {appointment}` |
| `DELETE` | `/api/appointments/<id>` | – | `204` / `404` |
| `GET` | `/api/routines` | – | `200 {routines}` |
| `POST` | `/api/routines` | `label, cadence, window` | `201 {routine}` |
| `DELETE` | `/api/routines/<id>` | – | `204` / `404` |
| `GET` | `/api/places` | – | `200 {places}` |
| `POST` | `/api/places` | `name, latitude, longitude, radius_m` | `201 {place}` |
| `DELETE` | `/api/places/<id>` | – | `204` / `404` |
| `GET` | `/api/location/context` | `latitude, longitude` | `200 {context}` / `null` |
| `GET` | `/api/trusted` | – | `200 {trusted}` |
| `POST` | `/api/trusted` | `email, can_see_location, can_see_last_seen` | `201 {contact}` |
| `DELETE` | `/api/trusted/<id>` | – | `204` / `404` |
| `GET` | `/api/trusted/invitations` | – | `200 {invitations}` |
| `POST` | `/api/trusted/invitations/<id>/accept` | – | `204` / `404` |
| `POST` | `/api/trusted/invitations/<id>/decline` | – | `204` / `404` |
| `GET` | `/api/trusted/watching` | – | `200 {watching}` |
| `GET` | `/api/trusted/people/<id>` | – | `200 {owner, last_seen, location, warning}` / `403` |

\* `tz_offset` is minutes **east** of UTC, i.e. `-new Date().getTimezoneOffset()`.

\* `current_password` is required only when the email address actually changes.

`POST /api/location` stores the caller's **latest** position only: it overwrites
`last_latitude` / `last_longitude` / `last_location_at` on that account's row,
so no location history accumulates. Both values must be finite numbers inside
`[-90, 90]` and `[-180, 180]`; anything else is `400 invalid_request`. The
`/chat` and `/call` pages send a ping every 5 seconds while they are visible.
Coordinates are personal data: they are never logged, never returned by
`/api/auth/me`, and never stored in chat history.

Errors: `400 invalid_request`, `400 password_mismatch`, `409 email_taken`,
`401 invalid_credentials` (one generic message for a wrong email *or* password —
wrong current password uses the same code), `401 unauthorized` /
`401 token_expired`.

A profile update returns a **fresh token**, because the JWT carries the name and
email in its claims; the client stores it before the next request. Changing the
password proves the current one but does **not** revoke other sessions: tokens
are stateless and stay valid until they expire (7 days by default).

**Middleware.** `auth.init_app(app)` registers a single `before_request` hook
that verifies the bearer token on every route except the public ones
(`/login`, `/register`, `/chat`, `/settings`, `/static/*`, and the two auth
endpoints). API paths get a `401` JSON; page paths get redirected to `/login`.

> Because a browser *navigation* can't carry an `Authorization` header, the
> `/chat` and `/settings` HTML shells are public and contain no data. Their
> first action is `GET /api/auth/me`; a `401` clears the token and bounces you
> to `/login`. Everything with data or side effects goes through the API, which
> the middleware fully guards.

Chat history stays in the browser, keyed per account
(`th_chat_history_<user id>`), so two accounts on one browser don't share a
thread.

Each accepted chat turn stamps `users.last_message_at` with an ISO-8601 UTC
timestamp, in both `/api/chat` and `/api/chat/stream` and in both chat and call
mode. It is internal bookkeeping: the value is not exposed by `/api/auth/me`.

The browser never sees the Token Harbor API key; all model calls go through
Flask. The server prepends the system prompt so the message sent to the model
always looks like:

```python
[
  {"role": "system", "content": SYSTEM_PROMPT},   # always index 0
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": "..."},
  ...
]
```

A client-supplied `system` message is rejected (400), so the prompt at index 0
can't be overridden. Requests are also validated: only `user`/`assistant` roles,
non-empty content, capped length and message count.

The system prompt comes from the SAGE persona documents in `prompts/`
(`english.md` / `arabic.md`). The client picks one per request with a `language`
field (`"en"` or `"ar"`; anything else falls back to English), and the server
appends a directive forcing the reply language. `TH_SYSTEM_PROMPT` overrides the
persona for every language. A missing prompt file falls back to a short built-in
string, so the app still runs without `prompts/`.

```jsonc
// POST /api/chat  (and /api/chat/stream)
{
  "messages": [{"role": "user", "content": "..."}],
  "language": "ar"          // optional; "en" (default) or "ar"
}
```

The UI also has:

- **English / Arabic** — a toggle in the chat header. It selects the matching
  SAGE prompt, translates the visible UI, and flips the chat to right-to-left for
  Arabic (`<html dir="rtl">`). The choice is remembered per browser.

- **Light / dark mode** — a toggle in the chat header and on the auth pages.
  It follows your OS theme until you pick one, then remembers the choice.
- **Delete your message** — hover one of *your* messages and click the trash
  icon; it removes that message together with the assistant's reply. `Clear`
  wipes the whole conversation. Message ids stay in the browser, so the API only
  ever receives `role` and `content`.

Config (all optional except the key):

| Variable | Default | Purpose |
| --- | --- | --- |
| `TH_API_KEY` | — | required; the `thk_live_...` key |
| `JWT_SECRET` | random per boot | signs login tokens; set it so logins survive a restart |
| `JWT_TTL_DAYS` | `7` | how long a login lasts |
| `TH_DB_PATH` | `instance/app.db` | SQLite user database |
| `TH_MODEL` | `deepseek-v4.1-flash:free` | model used by the web app |
| `TH_SYSTEM_PROMPT` | — | optional persona override applied to every language; unset uses `prompts/*.md` |
| `PORT` | `5000` | dev server port |
| `FLASK_DEBUG` | `1` | set to `0` to disable the debug reloader |
| `LOG_LEVEL` | `INFO` | stdlib log level |
| `LOG_FORMAT` | `text` | `text` for readable logs or `json` for machines |
| `LOG_SQL` | `1` | log normalized SQL statements; `0` disables |
| `LOG_SQL_PARAMS` | — | log SQL bind values (full query); dev only, see privacy note |
| `LOG_FILE` | `instance/app.log` | rotating log file; set to empty to disable |
| `LOG_MAX_BYTES` | `5000000` | rotate the log file after this size |
| `LOG_BACKUP_COUNT` | `5` | number of rotated log files to keep |
| `ELEVENLABS_REFRESH_TOKEN` | — | preferred: Firebase refresh token; mints access tokens |
| `FIREBASE_API_KEY` | public web key | Firebase Web API key; referrer-restricted |
| `ELEVENLABS_UID` | — | optional Firebase user id for reference |
| `ELEVENLABS_TOKEN` | — | fallback: short-lived access token; expires hourly |
| `ELEVENLABS_BASE_URL` | `https://api.us.elevenlabs.io` | regional ElevenLabs host |
| `ELEVENLABS_VOICE_ID` | `r1KmysJdVYZjJCm4mL3b` | English voice id (the `owl` voice) |
| `ELEVENLABS_VOICE_ID_AR` | `L10lEremDiJfPicq5CPh` | Arabic voice id |
| `ELEVENLABS_STABILITY` | `0.5` | 0..1; lower is more expressive |
| `ELEVENLABS_SIMILARITY` | `0.75` | 0..1; how tightly to hold the voice |

Generate a signing key with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Personal memory (agent tools)

The chat model can persist durable facts in the same SQLite database, and read
the account name, through three tools:

- **`save_memory`** — batch saves entities, relationships, and facts.
- **`recall_memory`** — searches the signed-in user's saved memory.
- **`get_name`** — returns the signed-in user's name from their JWT claims (the
  database is only a fallback for older tokens). It takes no arguments; the
  handler is bound per request and ignores anything the model passes, so it can
  never return another account's name.

The tables mirror the product schema, translated to SQLite: UUIDs are `TEXT`,
JSONB becomes JSON `TEXT`, and timestamps are ISO-8601 UTC `TEXT`. `user_id`
stays an `INTEGER` foreign key to `users.id`. Tables are created automatically
on startup, so an existing `instance/app.db` gains them without a manual
migration. Foreign keys are enabled by `auth.connect()`.

For example, “My sister Sara is 20” can be saved as:

```jsonc
{
  "entities": [{"name": "Sara", "entity_type": "Person"}],
  "relationships": [
    {"source_entity": "Current User", "target_entity": "Sara", "relationship_type": "Sister"}
  ],
  "facts": [
    {"entity": "Sara", "predicate": "age", "value": 20, "confidence": 1.0}
  ]
}
```

- `Current User` is a per-user self entity. The model never receives a user id;
  handlers are bound to the authenticated account, and every query is scoped by
  `user_id`.
- Facts are append-only. An older value is retired by setting `valid_until`
  rather than deleted, so `include_expired: true` still returns it.
  `recall_memory` hides expired facts unless `include_expired` is true.
- **Single-valued predicates supersede automatically.** Saving a new `name`,
  `birthday`, `age`, `email`, `address`, `nickname`, and similar attributes
  retires the previous value without needing `replace`, so saying “my name is
  George” and then “my name is Yousef” leaves one active name. The set lives in
  `SINGLE_VALUED_PREDICATES` in `memory.py`. Multi-valued predicates such as
  `likes`, `hobby`, or `pet` keep every value unless you pass `replace: true`.
  Re-saving an identical value does not create a second row.
- The speaker's own name is the `name` fact on `Current User`, never an entity
  named after them. `save_memory` does not change the account name that
  `get_name` returns; that comes from `users` and is edited on `/settings`.
- `value` can be any JSON scalar, array, or object.
- Every returned entity, relationship, and fact includes its `created_at`.
  Facts also include `observed_at` and `valid_until`.
- The system prompts include a memory protocol instructing the model to recall
  before answering personal questions and save only information the user
  explicitly stated.
- `app.system_prompt()` also appends `memory.prompt_brief()`: a compact
  description of the three stores, the exact keys each tool accepts, the
  server-managed fields (`id`, `user_id`, `created_at`) the model must never
  send, and the validation limits. `TH_SYSTEM_PROMPT` replaces only the persona,
  so the brief is present in every request. The brief is guidance; the actual
  boundary is that no tool schema exposes `user_id` and `toolset()` binds it
  from the JWT.

The streaming endpoint emits tool lifecycle frames alongside text:

```text
data: {"tool":{"name":"save_memory","status":"start"}}

data: {"tool":{"name":"save_memory","status":"end","ok":true}}

data: {"delta":"Saved."}
```

`static/chat.js` turns those into localized status text (“Saving memory...”,
“Searching memory...”, “Memory saved”, “Checking your account...”) and never
stores the status in chat history. Tool arguments and results are not sent to the browser. The configured
model must support tool calling (`TH_MODEL`).

### Stopping generation

Every streamed response carries an `X-Stream-ID` header. Send it back to stop
that generation:

```jsonc
// POST /api/chat/stop
{ "id": "4f1c9e7a…" }        // 200 {"stopped": true} | {"stopped": false}
```

- The id is scoped to the signed-in account: another user's id (or a stale one)
  returns `{"stopped": false}` and changes nothing.
- Stopping sets a cancel flag **and** closes the provider response, so a silent
  upstream is cut immediately rather than on the next token.
- The stream then ends with `data: {"stopped":true}` followed by `data: [DONE]`.
  The stopped turn is rolled back: the user message and any partial answer are
  removed from the transcript and from browser history, and the typed text is
  put back in the composer so it can be edited or resent.
- If the browser vanishes instead of calling the endpoint, the WSGI server closes
  the response generator and the server severs the provider connection the same
  way (`chat.stream.done … reason=disconnected`).
- Stopping requires a concurrent server: the Flask dev server works, gunicorn
  needs `--threads`, and single-threaded sync workers cannot serve the stop
  request while a stream is running.

## Logging

Logs go to stdout and a rotating file (`instance/app.log` by default). Set
`LOG_FILE=` to disable the file handler. Text is the default format for local
readability; set `LOG_FORMAT=json` for structured production logs.

Every request gets an `X-Request-ID` response header. HTTP, chat, memory, and
tool logs carry `request_id`, and authenticated operations also carry `user_id`.

```text
2026-11-17 04:03:17.836 INFO  memory         memory.query | request_id=9ddc8dddf5b44f06a42f7e914f1244b7 user_id=1 has_entity=true has_predicate=false has_query=false query_chars=0 include_expired=false limit=20 entity_count=1 relationship_count=0 fact_count=1 duration_ms=2.41ms outcome=ok
2026-11-17 04:03:17.838 INFO  memory         memory.sql | request_id=9ddc8dddf5b44f06a42f7e914f1244b7 user_id=1 operation=fact.query duration_ms=0.08ms outcome=ok
    SQL: SELECT f.*, e.name AS entity_name, e.entity_type AS entity_type FROM memory_facts f JOIN entities e ON e.id = f.entity_id WHERE f.user_id = ? AND e.user_id = ? AND e.name = ? COLLATE NOCASE ORDER BY f.observed_at DESC, f.created_at DESC LIMIT ?
```

What is logged:

- `http.request` / `http.response` — method, path, status, duration.
- `chat.request` / `chat.done` / `chat.stream.done` — model, language, message
  count, finish reason, duration.
- `memory.query` / `memory.save` — filter flags, input counts, result counts,
  duration.
- `memory.sql` — normalized SQL statements with `?` placeholders, operation
  name, and duration. Set `LOG_SQL=0` to disable. Set `LOG_SQL_PARAMS=1` to add
  a `PARAMS:` line with the bind values (see the privacy note below).
- `tool.start` / `tool.end` / `tool.error` — tool name, success, duration,
  error type.
- `memory.*.invalid` and `*.error` — validation warnings and unexpected errors.

Privacy: logs never contain chat text, entity names, predicates, fact values,
query text, tool arguments/results, JWTs, API keys, cookies, or request bodies.
Loggers use counts, flags, timings, and error types instead. SQL logging includes
only statement text and `?` placeholders. The one opt-in exception is
`LOG_SQL_PARAMS=1`, which appends the bind values so you can see the full query;
those values are personal memory data (names, facts, query text), so keep the
flag off outside local debugging and avoid leaving it on in `instance/app.log`.

```text
2026-11-17 04:03:17.838 INFO  memory         memory.sql | request_id=… user_id=1 operation=fact.query duration_ms=0.08ms outcome=ok
    SQL: SELECT f.*, e.name AS entity_name, … WHERE f.user_id = ? AND e.user_id = ? AND e.name = ? COLLATE NOCASE … LIMIT ?
    PARAMS: (1, 1, 'Sara', 20)
```

## Voice calls

The chat header links to the `/call` screen, which turns the chat into a spoken
conversation:

1. The user speaks (browser Web Speech API) or types.
2. The transcript goes to the normal `/api/chat/stream` endpoint with
   `mode: "call"`, so SAGE uses the spoken call persona and the memory tools.
3. Speech is generated in whole segments, not sentences. If the model calls a
   tool, the text before the call is sent immediately (so it plays while the
   tool runs) and the text after the call is sent when the reply finishes.
   Each segment goes to `POST /api/voice/speak`, which generates MP3 audio
   through the ElevenLabs web request; the browser queues the segments in
   order. Tool status frames are never spoken.

The call screen unlocks audio on the Start gesture and pauses speech recognition
while SAGE is speaking, so the microphone cannot hear the assistant and cancel
its own playback.

This follows the [ElevenApi](https://github.com/Black0joker/ElevenApi) approach:
`POST /v1/text-to-dialogue/stream` with model `eleven_v4_exp` and a browser
`authorization: Bearer eyJ...` session token. There is no `xi-api-key` path.

### Getting a voice token

The web app authenticates with Firebase, so the preferred setup is its
long-lived refresh token; the server mints a fresh one-hour access token
automatically whenever it is needed.

1. Open <https://elevenlabs.io/app> and sign in.
2. DevTools → Application → Local Storage → `https://elevenlabs.io`.
3. Open the `firebase:authUser:<FIREBASE_API_KEY>:[DEFAULT]` entry and copy
   `stsTokenManager.refreshToken`.
4. Put it in `.env` as `ELEVENLABS_REFRESH_TOKEN=...` (and optionally
   `ELEVENLABS_UID=...`). The public `FIREBASE_API_KEY` has a default and only
   needs overriding if ElevenLabs changes it.

The server exchanges that refresh token at `securetoken.googleapis.com` and
caches the access token in memory. If Firebase rotates the refresh token, the
new value is written back to `.env` automatically. A short-lived
`ELEVENLABS_TOKEN` copied from the `authorization` header still works as a
fallback. `/api/voice/status` reports availability and expiry; `/call` shows a
banner when voice is unavailable. Text chat and typed call turns keep working
when voice is down. ElevenLabs may also restrict an account (for example after
unusual-activity detection), which is reported as `voice_account_restricted`.

> **A `401` from ElevenLabs is not always a bad token.** It also answers `401`
> when the account runs out of credits, with
> `{"detail":{"code":"quota_exceeded", ...}}`. That is reported as
> `voice_quota_exceeded` together with ElevenLabs' own message (for example
> *"You have 7 credits remaining, while 16 credits are required"*), and
> `/api/voice/status` then reports `reason: "voice_quota_exceeded"` so `/call`
> shows a banner before anyone speaks. Top up the plan or switch accounts; a
> successful request clears the state. The old behaviour blamed the token and
> sent people looking for a token problem that did not exist.

If a token copied from the browser works but the refreshed token gets
`voice_account_restricted`, compare how the two sessions signed in. The
refresh token carries a Firebase `sign_in_provider` claim (`google.com`,
`password`, etc.); use a refresh token captured from the **same sign-in method**
as the working web session. For a Google-based web session, sign in with Google
before copying `stsTokenManager.refreshToken`. The restriction itself is on the
ElevenLabs account, not on the refresh call: a refreshed token can return
`200` from `/v1/user/subscription` while `/v1/text-to-dialogue/stream` still
returns `detected_unusual_activity`.

The server sends only assistant text to ElevenLabs. Voice text, tokens, and
audio are never logged and never stored in chat history. One turn produces at
most one request per text segment around tool calls; if a segment is longer
than the provider's 2,000-character limit, the server splits that single
segment with continuity metadata rather than the client sending many separate
prompts.

Call-mode system prompts also include a delivery-tag cheat sheet: square-bracket
tags such as `[laughs]`, `[whispers]`, `[sympathetic]`, and `[thoughtful]` are
performed instead of read aloud, while punctuation controls pacing (`...`, `--`,
CAPS, `!`, `?`). Eleven v4 has no SSML and no markdown, so the model is told to
use tags sparingly and never to read one aloud.

## Deployment

The app is deliberately a **single-process** server: the streaming registry behind
`POST /api/chat/stop` and the SQLite database both live inside one process. Run
exactly one instance, and serve it with threaded gunicorn rather than the Flask
dev server.

```bash
gunicorn --bind 0.0.0.0:$PORT \
  --worker-class gthread --workers 1 --threads 8 --timeout 0 \
  --access-logfile - --error-logfile - app:app
```

`--threads` is required — a stop request must be served while a stream is open —
and `--timeout 0` stops gunicorn from killing long-lived SSE replies. `gunicorn`
is in `requirements.txt`, so both deploy paths below install it. It cannot run on
Windows, where `python app.py` remains the way to develop.

### Railway

`railway.json` pins the Dockerfile builder and the start command, so a deploy does
not depend on Railway guessing how to run the app. The container must be able to
write `TH_DB_PATH`; the image runs as root because Railway bind-mounts volumes as
`root:root` and an unprivileged user cannot create the SQLite file there (the
symptom is `unable to open database file` at import, and the worker never boots).

| Setting | Value | Why |
| --- | --- | --- |
| Branch | `main` | pointing the service at a feature branch keeps old code online and misses later fixes |
| `TH_API_KEY` | your `thk_live_...` key | chat answers `500` without it |
| `JWT_SECRET` | long random string | unset, the key is random per boot, so every restart signs everyone out |
| `FLASK_DEBUG` | `0` | the default `1` runs the reloader and the interactive debugger |
| `TH_DB_PATH` | `/app/instance/app.db` | the volume mount below |
| `LOG_FILE` | `/app/instance/app.log` | keeps rotating logs on the volume too |
| Replicas | `1` | SQLite plus the in-process stream registry |

> **Attach a volume** and mount it at **`/app/instance`**. Railway's filesystem is
> ephemeral, so without a volume the database — users, memory, transcripts,
> appointments, places, and trusted contacts — is wiped on every deploy.
>
> A volume mounted anywhere else is fine too, but then set `TH_DB_PATH` and
> `LOG_FILE` to that path; the mount is root-owned, which is why the image does
> not drop privileges.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET
```

### Docker

```bash
docker build -t sage .
docker run --rm -p 5000:5000 \
  --env-file .env \
  -v sage-data:/app/instance \
  sage
```

The image sets `FLASK_DEBUG=0` and runs gunicorn with the flags above. It runs as
root so that a mounted volume is writable; if you want a non-root runtime, add an
entrypoint that `chown`s the mount and then drops privileges (that entrypoint has
to start as root anyway).

> Environment variables, not `.env`: the image does not copy `.env` (it is in
> `.dockerignore`). Pass configuration with `--env-file` or `-e`.

The sections below describe the `core` client that the app is built on.

## Quick start

```python
from core import TokenHarbor

client = TokenHarbor(model="deepseek-v4.1-flash")

# One-shot, non-streaming
print(client.chat("Say hello in five words."))
```

## Streaming

```python
for piece in client.stream("Write a haiku about the sea."):
    print(piece, end="", flush=True)
```

Need lifecycle info (finish reason, token usage)? Use `stream_events`:

```python
for event in client.stream_events("Write a haiku.", include_usage=True):
    if event["type"] == "delta":
        print(event["content"], end="", flush=True)
    elif event["type"] == "done":
        print("\nfinish:", event["finish_reason"], "usage:", event["usage"])
```

## Conversation history

`client.messages` holds the conversation. `ask` / `stream_ask` append the user
turn, call the model, and append the reply for you:

```python
client.system("You are a terse assistant.")
client.ask("My name is Youssef.")
print(client.ask("What is my name?"))   # remembers the prior turn

client.reset()                          # start over
client.load([{"role": "user", "content": "seeded"}])  # or import history
```

Or manage it by hand:

```python
client.user("hi")
for piece in client.stream(client.messages):   # send explicit messages
    ...
```

## Tool calling

Decorate a plain Python function with `@tool`; the schema is built from its
signature and docstring.

```python
from core import TokenHarbor, tool

@tool
def get_weather(city: str) -> dict:
    """Get the current weather for a city."""
    return {"city": city, "temp_c": 21}

client = TokenHarbor()
print(client.run_tools("What's the weather in Paris?", tools=[get_weather]))
```

`run_tools` runs the whole loop: it sends the schema, calls your handler, feeds
the result back as a `tool` message, and returns the final assistant text.
Streaming variant:

```python
for piece in client.run_tools_stream("What's the weather in Paris?", tools=[get_weather]):
    print(piece, end="", flush=True)
```

- `tools` accepts `@tool` functions and/or raw OpenAI schema dicts.
- Extra handlers: `handlers={"name": callable}`.
- Handler exceptions, unknown tools, and malformed JSON arguments are sent back
  to the model as tool results instead of crashing the loop.
- `max_rounds` (default `8`) caps runaway loops.
- With streaming, `stream_events()` puts parsed `ToolCall`s in
  `event["tool_calls"]` on the final `done` event.
- Tool calling needs a model that supports it.

## Full request control

Any extra keyword is forwarded to the API (only non-`None` values are sent):

```python
client.chat(
    "Summarise this.",
    model="qwen3.7-max",
    temperature=0.2,
    max_tokens=512,
    top_p=0.9,
    stop=["\n\n"],
)
```

## `:free` models

Free-tier ids never charge your balance. They work exactly the same:

```python
TokenHarbor(model="deepseek-v4.1-flash:free").chat("hi")
```

## Errors

Every non-2xx raises `TokenHarborError` with `.status_code`, `.code`, `.body`,
and `.retryable`:

```python
from core import TokenHarbor, TokenHarborError

try:
    TokenHarbor().chat("hi")
except TokenHarborError as e:
    print(e.status_code, e.code, e.retryable)
    print(e)   # [401 invalid_api_key] Invalid or revoked API key...
```

| Status | Code | Retried |
| --- | --- | --- |
| 400 | `invalid_request` | no |
| 401 | `invalid_api_key` | no |
| 402 | `insufficient_credit` | no |
| 403 | `forbidden` | no |
| 404 | `not_found` | no |
| 405 | `method_not_allowed` | no |
| 408 | `request_timeout` | yes |
| **409** | **`conflict`** | **yes** |
| 413 | `payload_too_large` | no |
| 415 | `unsupported_media_type` | no |
| 422 | `unprocessable_entity` | no |
| 425 | `too_early` | yes |
| 429 | `rate_limited` | yes (honours `Retry-After`) |
| 500 | `internal_error` | yes |
| 502 | `bad_gateway` | yes |
| 503 | `service_unavailable` | yes |
| 504 | `gateway_timeout` | yes |

Retries use exponential backoff with jitter and respect `Retry-After`. Tune
with `max_retries`, `backoff_base`, and `backoff_max`. Code embedded in the
response body (e.g. `model_not_found`) takes precedence over the generic
status code.

## Tests

Two suites:

```bash
# unit tests — offline (mock gateway, stubbed client, temp database)
python -m unittest discover -s tests -v

# integration tests — live calls against tokenharbor.ai (needs TH_API_KEY)
python -m unittest discover -s tests/integration -v
```

The offline suite covers the `core` client, the Flask app, authentication, and
personal memory: registration, duplicate/case-insensitive emails, validation,
login, JWT claims and expiry, the middleware, the per-message delete/prompt
rules, memory saves/queries, user isolation, replacement/expiry behavior, tool
lifecycle events, structured logging, request correlation, log redaction, and
the ElevenLabs payload/token/error handling.

Integration tests default to the free model `deepseek-v4.1-flash:free` so they
don't spend your balance. Override with `TH_TEST_MODEL`:

```bash
TH_TEST_MODEL=qwen3.7-max python -m unittest discover -s tests/integration -v
```

## API surface

| Member | Purpose |
| --- | --- |
| `TokenHarbor(...)` | Create a client (reads key from env) |
| `.chat(...)` / `.chat_raw(...)` | Non-streaming: text / full JSON |
| `.chat_message(...)` | Assistant message dict, incl. raw `tool_calls` |
| `.stream(...)` | Yield text deltas |
| `.stream_events(...)` | Yield `delta` / `reasoning` / `done` (with `tool_calls`) |
| `.ask(...)` / `.stream_ask(...)` | History-aware call helpers |
| `.run_tools(...)` / `.run_tools_stream(...)` | Full tool-calling loop |
| `.run_tools_events(...)` | Tool loop with `delta` / `reasoning` / `tool_start` / `tool_end` / `done` events |
| `@tool` / `function_tool` | Build a tool schema from a function |
| `parse_tool_calls(...)` | Convert raw `tool_calls` to `ToolCall` objects |
| `.system/.user/.assistant/.tool` | Append a message |
| `.messages` / `.history` / `.load` / `.reset` | History access |
| `.list_models()` | `GET /v1/models` |
| `.close()` / `with TokenHarbor() as c:` | Clean up the session |