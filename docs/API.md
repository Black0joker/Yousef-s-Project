# SAGE — HTTP API Reference

Every endpoint the SAGE web app uses, exactly as implemented in `app.py` (with
`auth.py`, `memory.py`, `conversations.py`, `schedule.py`, `places.py`, `trusted.py`,
`voice.py`). Written so a React Native client can be built against it without reading
the Flask code.

Companion document: [`DESIGN.md`](./DESIGN.md) — screens, tokens, and native porting notes.

- **55 routes**: 9 HTML page routes + 46 API routes (2 of them public: register and login).
- **Base URL** in local dev: `http://127.0.0.1:5000` (override with `PORT`).
- All API paths are under `/api/`.

---

## 1. Conventions

### 1.1 Requests

| Header | When | Notes |
| --- | --- | --- |
| `Authorization: Bearer <jwt>` | every route except the public ones | the token from register/login |
| `Content-Type: application/json` | any request with a body | `silent=True` server-side, so a malformed body behaves like `{}` and usually yields `400` |
| `Accept: text/event-stream` | streaming only | optional; the stream response sets its own content type |

### 1.2 Responses

- JSON is UTF-8. Timestamps are **ISO-8601 UTC with a `Z` suffix**
  (`2026-11-20T09:41:07Z`). Clients render them in local time.
- `204 No Content` is the success response for most deletes and for `logout`.
- Every response carries an `X-Request-ID` header (echo it when reporting a bug).
- `POST /api/chat/stream` additionally returns `X-Stream-ID` and `X-Conversation-ID`.

### 1.3 Errors

```json
{ "error": { "code": "invalid_request", "message": "Message content is too long (max 8000 characters)." } }
```

Chat provider failures add the upstream status:

```json
{ "error": { "code": "rate_limited", "message": "[429 rate_limited] Too many requests.", "status": 429 } }
```

| Status | Meaning in this API |
| --- | --- |
| `400` | validation failed (`invalid_request`, `password_mismatch`, `voice_invalid_request`, `voice_text_too_long`) |
| `401` | missing/expired token (`unauthorized`, `token_expired`) or bad credentials (`invalid_credentials`) |
| `403` | consent gate closed (`consent_required`, `forbidden`) |
| `404` | not found **or not the caller's row** — the API never confirms that a foreign id exists |
| `409` | `email_taken` |
| `500` | provider client could not start (usually a missing `TH_API_KEY`) |
| `502` | upstream/provider failure (`bad_gateway`, `upstream_error`, `voice_upstream_error`) |
| `503` | `voice_token_missing`, `service_unavailable` |
| `4xx/5xx` from the model gateway | passed through as-is with the gateway's code |

Unauthenticated handling differs by path: an `/api/*` request gets the JSON `401`,
a page request is redirected to `/login`.

### 1.4 Public routes (no token)

```
GET  /                     → 302 to /login
GET  /login, /register     → HTML
GET  /chat, /call          → HTML shells (data-free; the browser fetches with a token)
GET  /settings, /memory    → HTML shells
GET  /schedule, /trusted   → HTML shells
GET  /favicon.ico
GET  /static/*
POST /api/auth/register
POST /api/auth/login
```

`OPTIONS` requests always pass through (CORS preflight).

### 1.5 Authentication model

- JWT, HS256, signed with `JWT_SECRET` (a random per-boot key if unset — tokens then die
  on restart). TTL `JWT_TTL_DAYS`, default **7 days**.
- Claims: `sub` (string id), `id` (int), `email`, `full_name`, `iat`, `exp`.
- The token is returned once by register/login/profile-update as
  `{"token", "expires_at", "user"}` where `expires_at` is ISO-8601 UTC.
- Tokens are stateless: `logout` only tells the client to forget the token. Changing a
  password does **not** revoke other tokens.
- Store the token in secure storage; keep `expires_at` to pre-empt `401`s.

---

## 2. Auth

### 2.1 `POST /api/auth/register` — public

```json
{ "first_name": "Youssef", "last_name": "Smith",
  "email": "you@example.com", "password": "Password123!", "confirm_password": "Password123!" }
```

`201`:

```json
{
  "token": "eyJhbGciOiJIUzI1NiIs...",
  "expires_at": "2026-11-27T09:41:07Z",
  "user": { "id": 7, "first_name": "Youssef", "last_name": "Smith",
            "email": "you@example.com", "full_name": "Youssef Smith" }
}
```

Validation: first/last name required, ≤ 60 chars after whitespace collapsing; email must
match a simple `x@y.z` pattern, ≤ 254, lowercased; password 8–128; `confirm_password`
must match. Errors: `400 invalid_request`, `400 password_mismatch`, `409 email_taken`.

### 2.2 `POST /api/auth/login` — public

```json
{ "email": "you@example.com", "password": "Password123!" }
```

`200` with the same payload as register. Errors: `401 invalid_credentials`
(*"Incorrect email or password."* — deliberately identical for unknown email and wrong
password).

### 2.3 `GET /api/auth/me`

```json
{ "user": { "id": 7, "first_name": "Youssef", "last_name": "Smith",
            "email": "you@example.com", "full_name": "Youssef Smith" } }
```

Never includes coordinates, `last_message_at`, or password material. `401` if the
account was deleted after the token was issued.

### 2.4 `POST /api/auth/logout`

`204` always. The client should clear its stored session.

### 2.5 `PATCH /api/auth/profile`

```json
{ "first_name": "Youssef", "last_name": "Smith",
  "email": "new@example.com", "current_password": "Password123!" }
```

`200` → a **new** `{token, expires_at, user}` (the name/email live in the claims, so the
token must be replaced). `current_password` is required **only when the email changes**;
without it the email is left alone. Errors: `400 invalid_request`,
`401 invalid_credentials` (wrong current password), `409 email_taken`.

### 2.6 `POST /api/auth/password`

```json
{ "current_password": "Password123!", "new_password": "NewPassword123!",
  "confirm_password": "NewPassword123!" }
```

`204`. Errors: `400 password_mismatch`, `400 invalid_request` (length),
`401 invalid_credentials`. Other devices stay signed in — do not clear the local session.

---

## 3. Chat

### 3.1 `POST /api/chat` — non-streaming

Body:

```json
{ "message": "What do I have today?", "language": "en",
  "mode": "chat", "conversation_id": "a4f0…" }
```

| Field | Notes |
| --- | --- |
| `message` | **preferred.** The server appends it to the caller's thread, reloads the thread from the database, and sends *that* to the model |
| `messages` | legacy array `[{role, content}]`, max 50 items, content ≤ 8000 chars, roles `user`/`assistant` only. Used by the call page |
| `language` | `en`/`ar` (+ aliases `eng`, `english`, `ara`, `arabic`); unknown → `en` |
| `mode` | `chat` (default) or `call` (caps the reply at 220 tokens) |
| `greeting` | call mode only: with no messages, inserts a synthetic user turn so the assistant opens the call |
| `conversation_id` | optional; ignored if it isn't the caller's thread |

When `message` is present, client-supplied `messages` are **ignored on purpose** — a
modified client must not be able to inject assistant turns.

`200`:

```json
{ "reply": "You have a dentist appointment at 3pm.", "conversation_id": "a4f0…" }
```

Errors: `400 invalid_request` (empty/oversized message, > 50 messages, bad role),
`500` (client construction, usually a missing key), gateway errors passed through.

### 3.2 `POST /api/chat/stream` — SSE

Same request body as `/api/chat`. Response headers:

```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
X-Stream-ID: 3f9c…            ← required for stopping
X-Conversation-ID: a4f0…      ← the thread the turn landed in
```

Validation failures return **JSON**, not a stream (`400 invalid_request`, `500`).
Otherwise the body is a sequence of `data: <json>\n\n` frames terminated by the literal

```
data: [DONE]
```

| Frame | Meaning |
| --- | --- |
| `{"delta": "text"}` | append to the answer |
| `{"reasoning": "text"}` | model reasoning, separate from the answer; safe to ignore |
| `{"tool": {"name": "recall_memory", "status": "start"}}` | a tool started — show a label, never arguments |
| `{"tool": {"name": "recall_memory", "status": "end", "ok": true}}` | the tool finished; `ok:false` means it threw |
| `{"done": true, "finish_reason": "stop", "usage": {...}}` | generation finished; `usage` may be `null` |
| `{"stopped": true}` | the caller's stop request took effect; the turn was deleted |
| `{"saved": {"conversation_id": "...", "user_turn_id": "...", "assistant_turn_id": "..."}}` | the ids to attach to the bubbles you already rendered |
| `{"error": {"code": "...", "message": "...", "status": 429}}` | provider error; the stream ends after `[DONE]` |
| `{"error": {"code": "server_error", "message": "..."}}` | unexpected server error |

Behaviour worth knowing:

- `saved` is emitted **after** persistence, only when the turn was not stopped — it is
  the reason the web app does not refetch the thread after every reply.
- A stop deletes the user turn and its reply, deletes nothing else, and emits
  `stopped` instead of `saved`.
- A dropped connection is *not* a rollback: the server keeps the user turn and records
  the partial reply.
- Stopping needs a concurrent server: the Flask dev server works, gunicorn needs
  `--threads`, and single-threaded sync workers cannot serve the stop request while a
  stream is open.

### 3.3 `POST /api/chat/stop`

```json
{ "id": "3f9c…" }
```

`200` → `{"stopped": true}` when the id belongs to one of *your* active streams,
`{"stopped": false}` for unknown or foreign ids. `400 invalid_request` when `id` is
missing. The server also closes the upstream provider link, so the stream ends promptly.

---

## 4. Conversations

Threads are server-authoritative; the app renders what the database holds.

### 4.1 `GET /api/conversations/current`

The caller's single active thread, **created on first use**.

```json
{
  "conversation": { "id": "a4f0…", "mode": "chat", "language": "en",
                    "started_at": "2026-11-20T09:00:00Z", "ended_at": null },
  "turns": [
    { "id": "t1…", "conversation_id": "a4f0…", "role": "user",
      "content": "hi", "created_at": "2026-11-20T09:00:01Z",
      "finish_reason": null, "model": null, "strategy_id": null,
      "latency_ms": null, "outcome": null, "tool_calls": null },
    { "id": "t2…", "conversation_id": "a4f0…", "role": "assistant",
      "content": "Hello!", "created_at": "2026-11-20T09:00:02Z",
      "finish_reason": "stop", "model": "deepseek-v4.1-flash:free",
      "strategy_id": null, "latency_ms": 812.4, "outcome": "ok",
      "tool_calls": [{ "name": "recall_memory", "ok": true }] }
  ]
}
```

`tool_calls` holds **names and ok flags only** — never arguments or results.
Turn `outcome` is one of `ok`, `stopped`, `error`. Stored content is capped at 20 000
characters per turn.

### 4.2 `GET /api/conversations`

`?limit=` (default 50, max 100) →

```json
{ "conversations": [ { "id": "a4f0…", "mode": "chat", "language": "en",
                       "started_at": "…", "ended_at": null } ] }
```

Newest first. This app keeps one ongoing thread per account; the list is
mostly useful for auditing.

### 4.3 `GET /api/conversations/<conversation_id>`

`{ "conversation": {...}, "turns": [...] }`, or `404 not_found` when the thread isn't
the caller's (same response as "doesn't exist").

### 4.4 `DELETE /api/conversations` — clear everything

`204`. Deletes **every** thread for the account, not just the current one: databases
created before the single-thread change hold one conversation per message, and deleting
only the newest would resurrect an old thread on the next load. This is the `Clear`
button — confirm before calling it.

### 4.5 `DELETE /api/conversations/<conversation_id>`

`204`, or `404 not_found`.

### 4.6 `DELETE /api/conversations/<conversation_id>/turns/<turn_id>`

`204`, or `404 not_found` when nothing was deleted.

**Deleting a user turn also deletes the assistant turn that followed it**, matching the
UI rule "deleting a question removes its answer". The follow-up is resolved by rowid, so
timestamps never matter. Use the ids from `saved` (or from `/current`) — never invent one.

---

## 5. Memory

Three stores, all scoped to the caller: **entities**, **relationships**, **facts**.

### 5.1 `GET /api/memory`

Query: `include_expired=1|true|yes` (default off), `limit` (integer, default 20, max 100 per store).
A non-integer `limit` answers `400 invalid_request`.

```json
{
  "entities": [
    { "id": "e1…", "name": "Mom", "entity_type": "person",
      "created_at": "2026-11-19T18:00:00Z" }
  ],
  "relationships": [
    { "id": "r1…", "source": "Sarah", "target": "Mom",
      "relationship_type": "friend_of", "created_at": "…" }
  ],
  "facts": [
    { "id": "f1…", "entity": "Mom", "entity_type": "person",
      "predicate": "birthday", "value": "3 April 1958", "confidence": 1.0,
      "source": null, "created_at": "…", "observed_at": "…", "valid_until": null }
  ]
}
```

- `value` is JSON-decoded when it was stored as JSON, otherwise the raw string.
- Facts are **never updated in place**: a superseded value keeps its row and gets
  `valid_until`, which is why `include_expired` exists. Hidden-by-default expired facts
  are why a corrected name doesn't look like a duplicate.
- Single-valued predicates (name, birthday, age, email, address, …) supersede
  automatically; multi-valued ones (likes, hobbies) require an explicit replace.
- The entity named `Current User` is the account owner.

Deletion is a **hard delete** (`DELETE FROM`), unlike supersession.

### 5.2 `GET /api/memory/export`

Returns `application/json` with `Content-Disposition: attachment; filename="sage-memory-export.json"`:

```json
{ "exported_at": "2026-11-20T09:41:07Z",
  "entities": [...], "relationships": [...], "facts": [...],
  "consents": { "location": { "kind": "location", "granted": true,
                              "default": true, "granted_at": "…", "revoked_at": null } } }
```

Includes **expired facts and consents**. Generated on demand, never persisted server-side.

### 5.3 `DELETE /api/memory/fact/<fact_id>` · `…/entity/<entity_id>` · `…/relationship/<relationship_id>`

`204`, or `404 not_found` for an unknown **or foreign** id (the API never confirms that
someone else's row exists). Deleting an entity cascades to its facts and relationships;
deleting a relationship leaves both entities in place.

### 5.4 `DELETE /api/memory` — forget everything

```json
{ "confirm": true }
```

`200` → `{"deleted": {"entities": 4, "relationships": 2, "facts": 9}}`.
Without `confirm: true` the API answers `400 invalid_request` — the guard exists so a
mis-wired client cannot wipe an account. `consents` rows survive; entity ids that were
re-created later could collide, so refresh the memory screen after this call.

### 5.5 `GET /api/consents`

```json
{ "consents": {
    "location":       { "kind": "location", "granted": true,  "default": true,  "granted_at": "…", "revoked_at": null },
    "trusted_sharing":{ "kind": "trusted_sharing", "granted": false, "default": false, "granted_at": null, "revoked_at": null },
    "voice":          { "kind": "voice", "granted": false, "default": false, "granted_at": null, "revoked_at": null },
    "memory_sharing": { "kind": "memory_sharing", "granted": false, "default": false, "granted_at": null, "revoked_at": null },
    "familiar_voice": { "kind": "familiar_voice", "granted": false, "default": false, "granted_at": null, "revoked_at": null } } }
```

`location` is granted by default; everything else needs an explicit opt-in. A **revoke
row always wins** over a default, so turning location off sticks.

Currently **enforced** server-side: `location` (`POST /api/location`,
`GET /api/location/context`) and `trusted_sharing` (`GET /api/trusted/people/<id>`, and
the `sharing` flag in `watching`). The other kinds are stored and displayed but nothing
gates on them yet.

### 5.6 `PUT /api/consents/<kind>`

```json
{ "granted": true }
```

`200` → `{ "kind": "location", "granted": true, "granted_at": "…", "revoked_at": null }`.
`400 invalid_request` for a non-boolean `granted` or an unknown kind. Revoking keeps the
row (`revoked_at` set) for the audit trail.

---

## 6. Schedule

### 6.1 `GET /api/appointments`

Query: `start`, `end` (ISO-8601), `include_done`, `limit` (default 50, max 200).

An appointment is included when it starts before `end` and ends after `start`, so a
multi-day event that began yesterday still shows today. Omitting `start` lists
everything from now onward. `done` and `cancelled` are hidden unless `include_done=1`.

```json
{ "appointments": [
  { "id": "ap1…", "title": "Dentist", "starts_at": "2026-11-20T13:00:00Z",
    "ends_at": "2026-11-20T14:00:00Z", "place": "Downtown",
    "notes": "Bring the referral letter.", "status": "pending",
    "created_at": "2026-11-19T20:00:00Z" } ] }
```

### 6.2 `GET /api/appointments/today?tz_offset=120`

`tz_offset` is **minutes east of UTC** — exactly what `-new Date().getTimezoneOffset()`
returns (`120` for UTC+2, `-300` for UTC−5). Clamped to ±14h. Omit it and "today" means
UTC.

```json
{ "from": "2026-11-19T22:00:00Z", "to": "2026-11-20T22:00:00Z",
  "appointments": [ /* same shape, same filtering as 6.1 */ ] }
```

The web app shows the first not-yet-past appointment from this list in the chat banner.

### 6.3 `POST /api/appointments`

```json
{ "title": "Dentist", "starts_at": "2026-11-20T13:00:00Z",
  "ends_at": "2026-11-20T14:00:00Z", "place": "Downtown", "notes": "…" }
```

`201` → the appointment object, `status` starts as `pending`.
Validation: title required ≤ 200; `starts_at` required ISO-8601; `ends_at` optional but
must not precede `starts_at`; place ≤ 200; notes ≤ 2000. Errors → `400 invalid_request`.

### 6.4 `PATCH /api/appointments/<appointment_id>`

```json
{ "status": "done" }
```

`status` ∈ `pending` | `done` | `cancelled`. `200` → the updated appointment,
`400 invalid_request`, `404 not_found`.

### 6.5 `DELETE /api/appointments/<appointment_id>`

`204`, or `404 not_found`.

### 6.6 `GET /api/routines`

`include_inactive=1` to include paused routines.

```json
{ "routines": [
  { "id": "ro1…", "label": "Morning walk", "cadence": "daily",
    "window": "07:30", "active": true, "created_at": "…" } ] }
```

### 6.7 `POST /api/routines`

```json
{ "label": "Morning walk", "cadence": "daily", "window": "07:30" }
```

`201` → the routine. Label ≤ 200, cadence ≤ 100, window ≤ 100.
Errors → `400 invalid_request`.

### 6.8 `DELETE /api/routines/<routine_id>`

`204`, or `404 not_found`.

---

## 7. Places and location context

### 7.1 `GET /api/places`

```json
{ "places": [
  { "id": "pl1…", "name": "Home", "latitude": 30.0444, "longitude": 31.2357,
    "radius_m": 150, "notes": "Quiet street, third floor.",
    "created_at": "2026-11-19T20:00:00Z" } ] }
```

> The caller's **own** saved places include their coordinates — the client already knows
> them. This is *not* the rule for the location-context payload (§7.4), which never
> returns coordinates.

Sorted by name. `limit` accepted, default 50, max 200.

### 7.2 `POST /api/places`

```json
{ "name": "Home", "latitude": 30.0444, "longitude": 31.2357,
  "radius_m": 150, "notes": "Quiet street." }
```

`201` → the place (with coordinates). `radius_m` defaults to 150, range 10–50 000.
Coordinates must be finite numbers in range (`bool`, strings, `NaN`, `inf` rejected).
Errors → `400 invalid_request`.

### 7.3 `DELETE /api/places/<place_id>`

`204`, or `404 not_found`.

### 7.4 `POST /api/location` — the periodic ping

```json
{ "latitude": 30.0444, "longitude": 31.2357 }
```

| Response | Meaning |
| --- | --- |
| `200` `{"context": {...}}` | position stored **and** it matched a familiar place |
| `204` | position stored; no familiar place (this is normal and must not be treated as an error) |
| `400 invalid_request` | bad coordinates |
| `403 consent_required` | the caller revoked the `location` consent — stop pinging and hide the banner |

Familiar-place context (`200`):

```json
{ "context": {
    "place": { "id": "pl1…", "name": "Home", "radius_m": 150,
               "notes": "Quiet street.", "created_at": "…", "distance_m": 12.4 },
    "memories": [ { "id": "f1…", "entity": "Home", "predicate": "note",
                    "value": "Spare key with the neighbour.", "...": "…" } ],
    "appointment": { "id": "ap1…", "title": "Call Mom", "starts_at": "…" } } }
```

- `place` **never** contains `latitude`/`longitude` (asserted by `tests/test_places.py`),
  and `distance_m` is the distance from the ping to the place centre.
- `memories` are the facts of an entity that shares the place's name.
- `appointment` is the next upcoming appointment, or `null`.
- An unfamiliar place yields no context at all — the API deliberately does not hint that
  the user is lost, confused, or in danger.

Recommended client cadence (matching the web): every 5 s while the chat/call screen is
foregrounded; skip a tick while a request is in flight; stop when permission is denied;
pause in the background.

### 7.5 `GET /api/location/context?latitude=30.04&longitude=31.23`

A one-shot context lookup that does not store the position (the web calls it when the
ping returned `204`). Same payload as §7.4 under `{"context": …}`, including the `null`
case. Coordinates arrive as **query strings**, so send them as plain numbers.
Errors: `400 invalid_request`, `403 consent_required`.

### 7.6 Coordinate privacy rules

| Surface | Coordinates returned? |
| --- | --- |
| `GET /api/places`, `POST /api/places` | yes (the caller's own saved places) |
| `POST /api/location`, `GET /api/location/context` | **no** |
| `GET /api/auth/me` | **no** (`last_latitude`/`last_longitude` are internal) |
| `GET /api/trusted/people/<id>` | yes, but only for an authorized viewer with both gates open (§8.7) |

Coordinates are never logged and never written to a conversation turn.

---

## 8. Trusted contacts

Invitations are keyed on **email**, so an invitation works before the other person has an
account. Three gates are re-checked on every cross-user read: the contact is `accepted`,
it is not revoked, and the **owner** still grants the `trusted_sharing` consent.

### 8.1 `GET /api/trusted` — invitations I sent

```json
{ "trusted": [
  { "id": "tc1…", "email": "friend@example.com",
    "can_see_location": true, "can_see_last_seen": true,
    "status": "pending", "created_at": "…", "accepted_at": null, "revoked_at": null } ] }
```

`status` ∈ `pending` | `accepted` | `revoked`.

### 8.2 `POST /api/trusted` — invite by email

```json
{ "email": "friend@example.com", "can_see_last_seen": true, "can_see_location": true }
```

`201` → the contact row. Both `can_see_*` default to `true` when omitted.

> **`201` is returned even when no account exists for that address.** Making the response
> depend on existence would turn this endpoint into a user-enumeration oracle
> (`tests/test_trusted.py` pins this). Do not "improve" it by returning `404`.

Inviting the same email twice, or the caller's own email, answers
`400 invalid_request` — validation, not a server fault.

### 8.3 `DELETE /api/trusted/<contact_id>` — revoke

`204`, or `404 not_found`. Owner-side revocation: the viewer loses access immediately.

### 8.4 `GET /api/trusted/invitations` — invitations addressed to me

`{"invitations": [ /* contact rows, status "pending" */ ]}` matched on the caller's
current email.

### 8.5 `POST /api/trusted/invitations/<contact_id>/accept`

`204`, or `404 not_found` when the invitation isn't addressed to the caller's email.

### 8.6 `POST /api/trusted/invitations/<contact_id>/decline`

`204`, or `404 not_found`. Declining matches on email, not on owner id.

### 8.7 `GET /api/trusted/watching` — people who trust me

```json
{ "watching": [
  { "id": "tc9…", "owner_id": 3, "name": "Sara Ali",
    "can_see_location": true, "can_see_last_seen": true,
    "accepted_at": "…", "sharing": false } ] }
```

Accepted contacts are returned **even when `sharing` is `false`** — the UI must render
them as *sharing paused* instead of dropping them, otherwise an accepted invitation looks
like it vanished (the viewer cannot change another account's consent). `peek()` still
refuses, so the flag leaks nothing.

### 8.8 `GET /api/trusted/people/<contact_id>` — view activity

`contact_id` is the id of the accepted contact row from `watching`.

```json
{ "contact_id": "tc9…",
  "owner": { "id": 3, "name": "Sara Ali" },
  "last_seen": "2026-11-20T07:12:00Z",
  "location": { "latitude": 30.0444, "longitude": 31.2357, "at": "2026-11-20T07:11:50Z" },
  "warning": false,
  "warning_hours": 24,
  "status": "active" }
```

- `last_seen` is the newer of the owner's `last_message_at` and `last_location_at`, or
  `null` when they've never been active.
- `location` is `null` unless the contact granted location **and** a position exists.
  This endpoint is the documented exception to the "never return coordinates" rule: it is
  the feature.
- `last_seen` is `null` when the contact withheld last-seen permission.
- `warning` is `true` only when the last activity is older than 24 h.
- `status`: **`unknown`** when there is no activity yet, **`stale`** when past 24 h,
  **`active`** otherwise. A brand-new account must never report `stale`.
- `403 forbidden` (*"You cannot see this person's activity."*) when any gate is closed —
  surface it as an error, not as an empty card.

---

## 9. Voice

### 9.1 `GET /api/voice/status`

```json
{ "configured": true, "available": true, "expired": false,
  "expires_at": "2026-11-20T10:41:07Z", "reason": "refresh",
  "refresh_configured": true, "token_source": "refresh", "manual_token": false,
  "voices": ["arabic", "george", "host", "owl"],
  "default_voice": "owl",
  "model": "eleven_v4_exp" }
```

Call `available` before enabling call mode. `reason` explains the state: `refresh`,
`ok`, `opaque`, `missing`, `expired`, or a voice error code such as
`voice_token_expired` / `voice_quota_exceeded`. `voice_expired` in `reason` means a
refreshed token was still rejected.

`token_source` (`manual`, `cache`, `refresh`) and `manual_token` are diagnostics: a
configured `ELEVENLABS_TOKEN` wins over a working refresh token, so this is how you tell
which credential is actually in play. No token material is ever returned.

> `reason: "voice_quota_exceeded"` means the **token is fine** and the ElevenLabs account
> is out of credits. ElevenLabs signals that with a `401`, so do not render it as an
> expired session. The state clears itself on the next successful request.

### 9.2 `POST /api/voice/speak`

```json
{ "text": "You have a dentist appointment at three.", "language": "en", "voice": "owl" }
```

`200` → `audio/mpeg` bytes with `Cache-Control: no-store`. Send one **segment** at a
time (assistant text between tool calls). `language: "ar"` selects the Arabic voice.

| Error | Status |
| --- | --- |
| `voice_invalid_request` (missing text, unknown voice) | `400` |
| `voice_text_too_long` (over 4 000 characters) | `400` |
| `voice_token_missing` / `voice_token_expired` | `503` |
| `voice_quota_exceeded` (the account is out of credits; the message carries ElevenLabs' own figures) | `503` |
| `voice_account_restricted` (free-tier / unusual-activity restriction) | `503` |
| `voice_upstream_error` | `502` |

Server-side details: segments over the provider's 2 000-character limit are split with
continuity metadata automatically; a rotated refresh token is persisted to `.env` (best
effort). Voice text, tokens, voice ids, and audio are never logged or stored in chat
history.

---

## 10. Limits and constants

| Constant | Value | Where |
| --- | --- | --- |
| `MAX_MESSAGES` | 50 | legacy `messages` arrays, and stored turns sent to the model |
| `MAX_CONTENT_CHARS` | 8 000 | per message |
| `MAX_STORED_CONTENT` | 20 000 | per stored turn |
| `CALL_MAX_TOKENS` | 220 | call-mode replies |
| Conversation list limit | default 50, max 100 | `GET /api/conversations` |
| Turn listing | all turns of the thread | `GET /api/conversations/<id>` |
| Memory query limit | default 20, max 100 | `GET /api/memory` |
| Appointments limit | default 50, max 200 | `GET /api/appointments` |
| Places limit | default 50, max 200 | `GET /api/places` |
| Trusted list limit | 50 | list/invitations/watching |
| Place radius | 10 – 50 000 m (default 150) | `POST /api/places` |
| `tz_offset` | −840 … 840 minutes east of UTC | `GET /api/appointments/today` |
| Voice text | ≤ 4 000 chars request; 2 000 per provider chunk | `POST /api/voice/speak` |
| Inactivity warning | 24 h | `GET /api/trusted/people/<id>` |
| JWT TTL | 7 days (`JWT_TTL_DAYS`) | auth |
| Name / email / password | 60 / 254 / 8–128 chars | auth |

---

## 11. TypeScript client

### 11.1 Core types

```ts
export type PublicUser = {
  id: number; first_name: string; last_name: string;
  email: string; full_name: string;
};

export type AuthPayload = { token: string; expires_at: string; user: PublicUser };

export type ApiError = { code: string; message: string; status?: number };

export type Turn = {
  id: string; conversation_id: string; role: 'user' | 'assistant';
  content: string; created_at: string;
  finish_reason: string | null; model: string | null; strategy_id: string | null;
  latency_ms: number | null; outcome: 'ok' | 'stopped' | 'error' | null;
  tool_calls: { name: string; ok: boolean | null }[] | null;
};

export type MemoryEntity = { id: string; name: string; entity_type: string; created_at: string };
export type MemoryRelationship = {
  id: string; source: string; target: string; relationship_type: string; created_at: string;
};
export type MemoryFact = {
  id: string; entity: string; entity_type: string; predicate: string;
  value: unknown; confidence: number | null; source: string | null;
  created_at: string; observed_at: string; valid_until: string | null;
};

export type Appointment = {
  id: string; title: string; starts_at: string; ends_at: string | null;
  place: string | null; notes: string | null;
  status: 'pending' | 'done' | 'cancelled'; created_at: string;
};

export type Contact = {
  id: string; email: string; can_see_location: boolean; can_see_last_seen: boolean;
  status: 'pending' | 'accepted' | 'revoked';
  created_at: string; accepted_at: string | null; revoked_at: string | null;
};

export type WatchEntry = {
  id: string; owner_id: number; name: string;
  can_see_location: boolean; can_see_last_seen: boolean;
  accepted_at: string | null; sharing: boolean;
};

export type Peek = {
  contact_id: string; owner: { id: number; name: string };
  last_seen: string | null;
  location: { latitude: number; longitude: number; at: string | null } | null;
  warning: boolean; warning_hours: number;
  status: 'unknown' | 'stale' | 'active';
};

export type Place = {
  id: string; name: string; latitude: number; longitude: number;
  radius_m: number; notes: string | null; created_at: string;
};

export type LocationContext = {
  place: Omit<Place, 'latitude' | 'longitude'> & { distance_m: number | null };
  memories: MemoryFact[];
  appointment: Appointment | null;
};
```

### 11.2 Client with 401 handling

```ts
import * as SecureStore from 'expo-secure-store';

export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message);
  }
}

export function createApi(baseUrl: string, onUnauthorized: () => void) {
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = await SecureStore.getItemAsync('th_token');
    const headers: Record<string, string> = {
      Accept: 'application/json',
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...((init.headers as Record<string, string>) ?? {}),
    };

    const response = await fetch(`${baseUrl}${path}`, { ...init, headers });

    if (response.status === 401) {
      onUnauthorized();                                  // clear session, go to Login
      const body = await response.json().catch(() => null);
      throw new ApiError(401, body?.error?.code ?? 'unauthorized', body?.error?.message ?? 'Session expired.');
    }
    if (response.status === 204) return undefined as T;
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new ApiError(response.status, body?.error?.code ?? 'error',
                         body?.error?.message ?? `Request failed (${response.status})`);
    }
    return (await response.json()) as T;
  }

  return {
    request,
    login: (email: string, password: string) =>
      request<AuthPayload>('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
    register: (payload: Record<string, string>) =>
      request<AuthPayload>('/api/auth/register', { method: 'POST', body: JSON.stringify(payload) }),
    me: () => request<{ user: PublicUser }>('/api/auth/me'),
    logout: () => request<void>('/api/auth/logout', { method: 'POST' }),
    currentThread: () =>
      request<{ conversation: { id: string }; turns: Turn[] }>('/api/conversations/current'),
    clearThreads: () => request<void>('/api/conversations', { method: 'DELETE' }),
    deleteTurn: (conversationId: string, turnId: string) =>
      request<void>(`/api/conversations/${conversationId}/turns/${turnId}`, { method: 'DELETE' }),
    memory: (includeExpired = false) =>
      request<{ entities: MemoryEntity[]; relationships: MemoryRelationship[]; facts: MemoryFact[] }>(
        `/api/memory${includeExpired ? '?include_expired=1' : ''}`),
    appointments: () => request<{ appointments: Appointment[] }>('/api/appointments'),
    today: (tzOffsetMinutes: number) =>
      request<{ from: string; to: string; appointments: Appointment[] }>(
        `/api/appointments/today?tz_offset=${tzOffsetMinutes}`),
    consents: () => request<{ consents: Record<string, { kind: string; granted: boolean }> }>('/api/consents'),
    setConsent: (kind: string, granted: boolean) =>
      request<{ kind: string; granted: boolean }>(`/api/consents/${kind}`,
        { method: 'PUT', body: JSON.stringify({ granted }) }),
    trusted: () => request<{ trusted: Contact[] }>('/api/trusted'),
    watching: () => request<{ watching: WatchEntry[] }>('/api/trusted/watching'),
    peek: (contactId: string) => request<Peek>(`/api/trusted/people/${contactId}`),
    places: () => request<{ places: Place[] }>('/api/places'),
    voiceStatus: () => request<{ configured: boolean; available: boolean; expired: boolean }>('/api/voice/status'),
    ping: (latitude: number, longitude: number) =>
      request<{ context: LocationContext } | undefined>('/api/location',
        { method: 'POST', body: JSON.stringify({ latitude, longitude }) }),
  };
}
```

### 11.3 Streaming chat

```ts
export type StreamEvent =
  | { delta: string }
  | { reasoning: string }
  | { tool: { name: string; status: 'start' | 'end'; ok?: boolean } }
  | { done: true; finish_reason?: string | null; usage?: unknown }
  | { stopped: true }
  | { saved: { conversation_id: string; user_turn_id: string; assistant_turn_id: string } }
  | { error: { code: string; message: string; status?: number } };

export function parseFrame(line: string): StreamEvent | null {
  if (!line.startsWith('data:')) return null;
  const data = line.slice(5).trim();
  if (!data || data === '[DONE]') return null;
  try { return JSON.parse(data) as StreamEvent; } catch { return null; }
}

export async function streamChat(
  baseUrl: string,
  token: string,
  body: { conversation_id?: string; message: string; language: 'en' | 'ar'; mode?: 'chat' | 'call'; greeting?: boolean },
  onEvent: (event: StreamEvent) => void,
  onStreamId: (id: string | null, conversationId: string | null) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`${baseUrl}/api/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  });

  onStreamId(response.headers.get('X-Stream-ID'), response.headers.get('X-Conversation-ID'));

  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.error?.message ?? `Stream failed (${response.status})`);
  }

  // React Native: `response.body` is not a standard ReadableStream on every runtime.
  // Use react-native-sse, or XHR with `onprogress` plus the same line splitting below.
  const reader = (response.body as ReadableStream<Uint8Array>).getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop() ?? '';
    for (const part of parts) {
      const event = parseFrame(part.trim());
      if (event) onEvent(event);
    }
  }
}

export async function stopStream(baseUrl: string, token: string, streamId: string): Promise<void> {
  await fetch(`${baseUrl}/api/chat/stop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ id: streamId }),
  });
}
```

Send flow to mirror the web app:

1. Render the user bubble optimistically; stub the assistant bubble with the typing dots.
2. POST `{conversation_id, message, language}` and keep the `X-Stream-ID`.
3. Append `delta` text; show tool names/status; ignore `reasoning` unless you display it.
4. On `saved`, attach `user_turn_id` / `assistant_turn_id` to the bubbles — **no refetch**.
5. On `stopped`, remove **both** bubbles and put the text back in the composer.
6. On error frames, mark the assistant bubble as an error (keep the user bubble).

### 11.4 Location ping

```ts
import * as Location from 'expo-location';
import { AppState } from 'react-native';

export function startLocationPing(api: ReturnType<typeof createApi>, onContext: (ctx: LocationContext | null) => void) {
  let timer: ReturnType<typeof setInterval> | null = null;
  let inFlight = false;
  let denied = false;

  const tick = async () => {
    if (denied || AppState.currentState !== 'active' || inFlight) return;
    inFlight = true;
    try {
      const permission = await Location.getForegroundPermissionsAsync();
      if (permission.status === 'denied') { denied = true; stop(); return; }
      const position = await Location.getCurrentPositionAsync({});
      const result = await api.ping(position.coords.latitude, position.coords.longitude);
      onContext(result?.context ?? null);        // 204 -> null -> hide the banner
    } catch (error: any) {
      if (error instanceof ApiError && error.code === 'consent_required') { denied = true; stop(); }
      // transient failures are retried on the next tick
    } finally {
      inFlight = false;
    }
  };

  const start = () => { if (!timer && !denied) { void tick(); timer = setInterval(tick, 5000); } };
  const stop = () => { if (timer) { clearInterval(timer); timer = null; } };

  const subscription = AppState.addEventListener('change', (state) => (state === 'active' ? start() : stop()));
  start();
  return () => { stop(); subscription.remove(); };
}
```

### 11.5 Voice playback

```ts
export async function speak(baseUrl: string, token: string, text: string, language: 'en' | 'ar', voice?: string) {
  const response = await fetch(`${baseUrl}/api/voice/speak`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ text, language, ...(voice ? { voice } : {}) }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(response.status, payload?.error?.code ?? 'voice_error',
                       payload?.error?.message ?? 'Voice generation failed.');
  }
  return response.arrayBuffer();               // write to a temp .mp3 and play with expo-av
}
```

Call-mode loop: stream the reply, split the answer at tool boundaries, `speak()` each
segment in order, and pause speech recognition while a segment plays.

---

## 12. Privacy rules the client must not break

These are enforced server-side; the client should not fight them.

1. **Never send a `user_id`, `owner_id`, or `contact_user_id`** to scope a write. Every
   handler binds the caller's id from the JWT. A model- or client-supplied id is ignored.
2. **Never send a `system` turn.** Only `user` and `assistant` roles are accepted, and the
   server inserts the system prompt at index 0 itself.
3. **Coordinates** are personal data: not logged, not stored in chat turns, absent from
   `/api/auth/me` and from the location-context payload. `/api/trusted/people/<id>` is the
   single, intentional exception — and only behind all three gates.
4. **`GET /api/trusted/people/<id>` failing means fail loudly.** A `403` must not be
   rendered as "nothing to show".
5. **`POST /api/trusted` answers `201` for unknown addresses** on purpose. Keep the UI
   copy neutral (`Invitation sent.`), not "user found".
6. **Deleting memory is a hard delete**; superseded facts are the only rows that linger.
   Do not add client-side "undo" that implies the data still exists.
7. **No secrets on the device**: never persist voice text, API keys, or the raw provider
   tokens. The app only ever holds the SAGE JWT.
8. **`DELETE /api/memory` needs `{"confirm": true}`** — keep a real
   confirmation dialog in front of it.

---

## 13. Environment variables that change API behaviour

| Variable | Default | Effect on this API |
| --- | --- | --- |
| `TH_API_KEY` | — | required; without it chat answers `500` |
| `JWT_SECRET` | random per boot | tokens invalid after restart if unset |
| `JWT_TTL_DAYS` | `7` | `expires_at` in auth payloads |
| `TH_MODEL` | `deepseek-v4.1-flash:free` | model used by the web app |
| `TH_SYSTEM_PROMPT` | — | overrides the persona for every language and mode |
| `TH_DB_PATH` | `instance/app.db` | where users/memory/schedule live |
| `PORT` | `5000` | dev server port |
| `LOG_LEVEL`, `LOG_FORMAT`, `LOG_SQL`, `LOG_SQL_PARAMS`, `LOG_FILE` | see README | logging only; `LOG_SQL_PARAMS` prints bind values (dev only) |
| `ELEVENLABS_REFRESH_TOKEN` | — | enables voice |
| `ELEVENLABS_TOKEN` | — | fallback short-lived voice access token |
| `ELEVENLABS_BASE_URL` | `https://api.us.elevenlabs.io` | regional voice host |
| `ELEVENLABS_VOICE_ID`, `ELEVENLABS_VOICE_ID_AR` | built-in ids | default voices |
| `ELEVENLABS_STABILITY`, `ELEVENLABS_SIMILARITY` | `0.5`, `0.75` | delivery controls |
| `FIREBASE_API_KEY`, `ELEVENLABS_UID` | public key | token refresh plumbing |
