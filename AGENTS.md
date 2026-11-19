# AGENTS.md

Flask chat app plus a standalone Token Harbor client. `README.md` is the detailed
reference for endpoints, config, client API, and error codes; update it when
behavior changes. This file covers workflow and non-obvious wiring.

## Layout

- `core/core.py` — standalone gateway client (streaming, retries, tools,
  history), re-exported by `core/__init__.py`. It must not import Flask.
- `app.py` — Flask routes, request validation, system-prompt assembly, SSE.
  Uses one lazily created shared client via `get_client()`.
- `auth.py` — SQLite users, Werkzeug scrypt hashes, JWTs, profile/password
  updates, and the `before_request` auth middleware registered by
  `init_app(app)`.
- `memory.py` — personal-memory schema, entity/relationship/fact CRUD, and the
  `save_memory` / `recall_memory` / `get_name` tool schemas and handlers.
- `conversations.py` — server-side transcript persistence: `conversations` /
  `conversation_turns`, the read API helpers, and the request-scoped recorders.
  Writes are best effort and must never fail a chat request.
- `schedule.py` — appointments, tasks, and routines: schema, UTC-normalized
  timestamps, the `today` window, and the `save_appointment` /
  `list_appointments` / `complete_appointment` tool schemas and handlers.
- `places.py` — familiar places and location context: named radius circles,
  server-side haversine matching, and the context payload (place, matching
  memories, next appointment). Never logs or returns coordinates.
- `trusted.py` — trusted contacts: email-keyed invitations, accept/decline,
  per-contact visibility, and the cross-user "last seen" read with the 24-hour
  inactivity warning.
- `voice.py` — ElevenLabs web-request client for call mode: session token
  expiry, payload/chunking, MP3 synthesis, and redacted logging.
- `logging_config.py` — readable text logging by default, JSON option, request
  IDs, request/user correlation, and rotating-file output.
- `templates/` + `static/` — no build step. Tailwind and fonts load from CDNs,
  so the UI is unstyled offline. `theme.js` must stay before the stylesheet
  (test-enforced). Frontend JS is plain IIFEs, not ES modules.
- `prompts/*.md` — SAGE personas, read fresh on every request (edit without
  restarting).

## Commands

```bash
pip install -r requirements.txt
python app.py                  # 127.0.0.1:5000; PORT / FLASK_DEBUG are honored

python -m unittest discover -s tests -v          # offline suite, no key/network
python -m unittest tests.test_app.TestPages.test_login_page_renders -v  # one test
python -m unittest discover -s tests/integration -v   # live; needs TH_API_KEY
```

- There is no pytest, lint, typecheck, formatter, or CI config. Stdlib
  `unittest` is the only verification step.
- `discover -s tests` does not recurse into `tests/integration`: neither `tests/`
  nor `tests/integration/` has an `__init__.py` (dotted single-test paths still
  work via namespace packages). The live suite runs separately and skips without
  a key.
- Live tests use `TH_TEST_MODEL` (default `deepseek-v4.1-flash:free`). On
  PowerShell: `$env:TH_TEST_MODEL = "qwen3.7-max"` before the command.
- Route tests stub the provider with
  `patch.object(app_module, "get_client", lambda: FakeClient())`
  (`FakeClient` lives in `tests/test_app.py`); never call the real gateway.

## Gotchas

### Startup, config, tests

- Importing `app.py` immediately configures logging, then runs `auth.init_db()`
  and `memory.init_db()`. Tests set `TH_DB_PATH`, `JWT_SECRET`, `LOG_LEVEL`, and
  `LOG_FILE` **before** importing `app`/`auth` (see top of `tests/test_app.py`);
  otherwise `instance/app.db` and `instance/app.log` are created as side effects.
- `core/core.py` calls `load_dotenv()` at import, so a developer `.env` leaks
  into tests. `test_app.py` deliberately clears `TH_SYSTEM_PROMPT` so tests
  exercise `prompts/*.md`.
- No migration framework: `auth.init_db()` / `memory.init_db()` only do
  `CREATE TABLE IF NOT EXISTS`. **A new column on an existing table needs an
  explicit `ALTER TABLE`**, or every database already on disk silently misses it
  — `CREATE TABLE IF NOT EXISTS` does nothing to a table that exists.
  `auth.ADDED_USER_COLUMNS` + `_ensure_columns()` (PRAGMA `table_info` then
  `ALTER TABLE ... ADD COLUMN`) is the pattern; keep those columns nullable,
  because `ADD COLUMN` cannot add `NOT NULL` without a default. Memory tables are
  added automatically to an existing `instance/app.db`; user columns are not.
- `users` carries `last_message_at` (stamped by `auth.touch_last_message()` in
  both chat routes) and `last_latitude` / `last_longitude` / `last_location_at`
  (overwritten by `POST /api/location`). They are internal: `_row_to_user()` and
  `public_user()` deliberately omit them, so `/api/auth/me` and the JWT claims
  never leak coordinates. Coordinates are personal data — never log them, not
  even in an error path; `location.update` logs only user/outcome/duration.
- Default models differ: `app.py` uses `deepseek-v4.1-flash:free` (no balance
  needed), while `core` defaults to `deepseek-v4.1-flash`. Both honor `TH_MODEL`.

### Memory

- Memory tools are always scoped by the JWT user id. Never add a `user_id`
  parameter to a tool schema: handlers from `memory.toolset()` bind it, and
  `auth.connect()` enables SQLite foreign keys for cascade deletes.
- Every SQL read and write in `memory.py` must filter by `user_id`; tests cover
  same-named entities belonging to different users. Do not add an unscoped
  lookup or trust a model-supplied user id.
- The memory tools use raw OpenAI schemas, not `@tool`, because their arguments
  contain nested entity/relationship/fact objects. Add new tools through
  `memory.toolset()` and the handler registry.
- `get_name` is a no-argument tool: the handler is closed over the account name
  taken from the request's JWT claims (`memory_toolset()` in `app.py` passes
  `display_name`, falling back to the DB for older tokens). It must never read a
  user id or name supplied by the model, and `tests/test_memory.py` asserts a
  model-supplied `user_id` is ignored.
- Memory query results always include `created_at` for every entity,
  relationship, and fact; facts also include `observed_at` and `valid_until`.
  Keep those date fields when changing the row serializers.
- Facts are never updated in place: a superseded value keeps its row and gets
  `valid_until`. `SINGLE_VALUED_PREDICATES` (name, birthday, age, email, …) in
  `memory.py` supersedes implicitly so a corrected name does not leave two
  active facts; everything else needs `replace: true`. Do not add multi-valued
  predicates (`likes`, `hobby`, `pet`) to that set — it would silently discard
  real data. Identical re-saves are a no-op, checked *before* the expire step so
  an unchanged value keeps its original row.
- **User-initiated deletion is a hard delete**, unlike supersession: `DELETE
  FROM` on the row, with entities cascading to their facts and relationships.
  Do not "soft delete" user-requested removal — the requirement is that the data
  is gone. `delete_all_memory()` clears all three stores and leaves `consents`
  intact.
- `_delete_owned()` is the only deletion path and always filters by `user_id`;
  an unknown or foreign id returns `False` so the route answers `404` rather
  than confirming the row exists. Never delete by id alone.
- `export_memory()` includes expired facts and consents, is generated on demand,
  and is never persisted — there is no second copy to secure.
- `consents` rows are keyed `(user_id, kind)` with `CONSENT_KINDS` in
  `memory.py`. `DEFAULT_GRANTED_CONSENTS` holds the kinds that are on without a
  row — currently `location` only; everything else requires an explicit opt-in.
  A **revoke row always wins over the default**, so turning a default-on kind off
  sticks. Revoking keeps the row with `revoked_at` for the audit trail. Consent
  is checked server-side in the route (`memory.get_consent`), never read from the
  client. `POST /api/location` answers `403 consent_required` when revoked, and
  `tests/test_app.py` `TestLocationApi.setUp` grants it explicitly so the storage
  tests do not depend on the default.

### Conversation persistence

- `app.py` imports and initialises `conversations` right after `memory`:
  `auth.init_db()` → `memory.init_db()` → `conversations.init_db()`. Add a new
  table here, not in `memory.py`.
- Recording is **best effort by design**. `record_user_turn` /
  `record_assistant_turn` / `append_turn` swallow and log failures and return
  `None`; never make them raise, or a logging bug breaks the chat reply.
- The streaming route records the assistant turn from `generate()`'s `finally`,
  using the accumulated `answer` list. It must run on every exit path — normal,
  stopped, errored, `GeneratorExit`, and `BrokenPipeError` — so a stopped turn is
  stored with `outcome="stopped"` instead of vanishing.
- `conversations.resolve_conversation()` honors a client-supplied
  `conversation_id` only when the row belongs to the caller; anything else
  silently starts a new thread. Never trust the id as an authorization check.
- Every conversation and turn row is filtered by `user_id`, and the insert path
  re-checks conversation ownership inside the same transaction.
- Message text is personal data: `conversations._execute` logs statement text
  with `?` placeholders only and never bind values, mirroring `memory._execute`.
  Tool arguments/results are not stored — only the tool *name* and its ok flag.
- `tests/test_conversations.py` covers the module and
  `tests/test_app.py::TestConversationPersistence` covers the routes.

### Schedule

- `app.py` initialises four stores in order: `auth` → `memory` →
  `conversations` → `schedule`. New appointment/routine tables belong in
  `schedule.SCHEMA_SQL`, not `memory.py`.
- `agent_toolset()` in `app.py` merges the memory and schedule tools and raises
  on a duplicate tool name. Add a new tool group there, not by hand in each
  chat route; both routes call `agent_toolset()`.
- **Every timestamp is stored UTC** and normalized through
  `memory._timestamp()`. The client renders local time. `today_appointments()`
  takes `tz_offset` in minutes **east** of UTC (what `-getTimezoneOffset()`
  returns); never use the server's local timezone to decide what "today" means.
- `list_appointments()` hides `done` and `cancelled` rows unless asked. An
  appointment overlaps a window when it starts before the upper bound and ends
  after the lower one, so multi-day events are not dropped.
- Schedule tool handlers filter arguments through `_tool_args()` before calling
  the model-facing functions. A model that invents `user_id` must not reach the
  handler — `tests/test_schedule.py` asserts the spoofed id is ignored. Follow
  the same pattern for any new tool.
- Titles, places, and notes are personal data: SQL goes through
  `memory._execute`, and the schedule logs carry counts and flags only.
- `tests/test_app.py::TestMemoryTools` pins the exact tool-name list offered to
  the model. Adding a tool means updating that assertion.

### Places and location context

- `app.py` initialises five stores: `auth` → `memory` → `conversations` →
  `schedule` → `places`. Place tables belong in `places.SCHEMA_SQL`.
- Matching is server-side on purpose. Never return a place's
  `latitude`/`longitude` to the client: `_place_dict(..., include_coordinates=False)`
  is what `find_place()` uses. `tests/test_places.py` asserts the payload has no
  coordinates.
- An **unfamiliar location must stay neutral**. `context_at()` returns `None`,
  the route answers `204`, and nothing is logged beyond `has_place=False`. Do
  not add a "you seem lost" branch — the requirements forbid inferring danger
  or confusion from an unfamiliar place.
- `POST /api/location` returns `204` with no body or `200 {"context": ...}`;
  `static/location.js` branches on the status. Context failures are swallowed
  and logged as `location.context.failed`, because a broken context must not
  break the ping.
- `GET /api/location/context` reads `latitude`/`longitude` from the **query
  string**, so they arrive as text. Use `_query_float()` before
  `clean_coordinate()`, or every valid request is a 400.
- Places reuse memory by name: a place called "Mom's house" surfaces the facts
  of the entity with that name. There is no separate place-memory table.
- Coordinates are personal data and never appear in logs, in `/api/auth/me`, or
  in the context payload.

### Trusted contacts

- `trusted.py` is the **only** place that reads one user's row for another. Every
  read must re-check all three gates — accepted, not revoked, and the owner's
  `trusted_sharing` consent — inside `peek()`. Never cache the result, never
  accept a client-supplied owner id, and never widen the query past
  `email = <caller's email>`.
- Invitations are keyed on **email**, not `contact_user_id`, so an invite works
  before the other person registers. `_owner_email()` resolves the caller's
  current address on each call.
- `POST /api/trusted` must keep answering `201` for an address with no account.
  Making the response depend on existence turns it into a user-enumeration
  oracle, and `tests/test_trusted.py` pins the behaviour.
- `trusted_sharing` is a consent kind and is **not** in
  `DEFAULT_GRANTED_CONSENTS`: sharing with another person needs an explicit
  opt-in even though `location` is on by default. It has a toggle on `/trusted`
  as well as on `/memory` — an owner who never opens `/memory` would otherwise
  never find it and the feature would look broken.
- `watching()` returns accepted contacts **even when `sharing` is false**, and
  `renderWatching()` shows them as "sharing paused". Filtering them out made an
  accepted invitation look like it had vanished; the viewer cannot turn another
  account's consent on. `peek()` still refuses, so the flag leaks nothing.
- `status` is `unknown` when `last_seen` is null. A never-active user must not
  report `stale`/`warning`; that would be a false alarm on a brand-new account.
  The warning is a prompt to check in, never a claim that something is wrong.
- `peek()` returns coordinates to the viewer by design — that is the feature, and
  it is the documented exception to the "never return coordinates" rule that
  applies to `places.py` and `/api/auth/me`.
- Accepting and declining verify the invitation belongs to the caller's email;
  `decline()` updates by `email`, not `owner_id`, because the caller is the
  viewer rather than the owner.

### Logging

- Route every memory SQL statement through `_execute(...)`: it emits
  `memory.sql` with normalized statement text, operation, and duration. Log SQL
  with `?` placeholders only; never add bind values. `LOG_SQL=0` disables it.
- Bind values are the sole opt-in exception: `_execute` adds a sanitized
  `sql_params` string only when `LOG_SQL_PARAMS` is truthy (off by default).
  Keep the default redaction intact: values go through `repr()` (escapes
  newlines) and are truncated per value and overall. `TextFormatter` renders a
  single `PARAMS:` line, `JsonFormatter` a `sql_params` field. If you change the
  renderer, keep `tests/test_memory.py`'s redaction test passing with the flag
  off (`LOG_SQL_PARAMS=0` / unset) and its enabled test passing with
  `LOG_SQL_PARAMS=1`.
- Log through `logging.getLogger(__name__)` with structured `extra={...}`
  fields (`event`, counts, timings, `outcome`), not interpolated message text.
  `RequestContextFilter` adds `request_id`/`user_id` to records inside a Flask
  request.
- Never log chat text, entity names, predicates, fact values, query text, tool
  arguments/results, JWTs, API keys, cookies, or request bodies. Tool logging
  reports only name/ok/duration/error type; memory logging reports counts and
  filter flags. `tests/test_logging.py` asserts this redaction.
- `logging_config.init_request_logging(app)` must be registered before
  `auth.init_app(app)` so requests rejected by auth still produce request logs.
  `LOG_FILE=""` disables the rotating file; default is `instance/app.log`.

### Auth

- All routes are guarded by the auth middleware except `PUBLIC_PATHS` and
  `PUBLIC_PREFIXES` in `auth.py`. Add new public routes there; API paths get a
  JSON 401, page paths redirect to `/login`.
- `/chat`, `/call`, `/settings`, `/memory`, `/schedule`, and `/trusted` are
  intentionally public, data-free HTML shells; authentication and user data come
  from `/api/auth/me`, and every write goes through the guarded API
  (`PATCH /api/auth/profile`, `POST /api/auth/password`, `DELETE /api/memory/...`,
  `POST /api/appointments`, `POST /api/trusted`).
- Profile updates reissue the JWT because `full_name`/`email` live in the claims;
  the client must `Session.save(...)` the returned token. Changing the email
  requires `current_password`, and both update paths use `auth.AuthError` codes.
  Password changes do not revoke other tokens (stateless JWTs, 7-day TTL), so
  never log names, emails, or passwords to compensate.
- The client may only send `user`/`assistant` messages; `system` is rejected.
  The server always inserts the system prompt at index 0 in `build_payload()`.

### Prompts and modes

- Adding a language requires coordinated edits to `LANGUAGE_FILES`,
  `LANGUAGE_ALIASES`, and `LANGUAGE_DIRECTIVES` in `app.py`, plus `STRINGS` and
  `LANG_ALIASES` in `static/chat.js`. `TH_SYSTEM_PROMPT` overrides all languages.
- Both chat prompt files contain a “Memory Tool Protocol” naming `save_memory`,
  `recall_memory`, and `get_name`; keep those names in sync with `memory.py`,
  the generated `prompt_brief()`, and the `STRINGS.tool*` labels in
  `static/chat.js`.
- Call mode is selected by `PROMPT_MODES["call"]` in `app.py` and uses
  `prompts/english-call.md` / `prompts/arabic-call.md`; a missing call file
  falls back to the chat persona. Arabic call mode must stay **Egyptian
  colloquial** (`CALL_LANGUAGE_DIRECTIVES["ar"]`), not Modern Standard or Gulf
  Arabic, and `static/call.js` uses `ar-EG` for recognition. Call turns are
  capped by `CALL_MAX_TOKENS`, and `greeting: true` with no messages inserts
  `GREETING_TURNS` server-side.
- `voice.prompt_brief()` (a delivery-tag cheat sheet from ElevenApi `TAGS.md`)
  is appended to call-mode system prompts, including under `TH_SYSTEM_PROMPT`.
  Keep it in sync with `voice.py` and `tests/test_voice.py` when the tag list
  changes; `tests/test_app.py` asserts it appears in call mode and not in chat.
- `memory.prompt_brief()` is generated from the schema constants and appended to
  every system prompt by `app.system_prompt()`, including when
  `TH_SYSTEM_PROMPT` overrides the persona. Add or rename a tool field in the
  schema, the brief text, and the drift test in `tests/test_memory.py` together.
  The brief is not a security boundary: `user_id` stays out of every tool schema
  and is bound by `memory.toolset()`.

### Voice

- `voice.py` replays the elevenlabs.io web request with a Firebase access token;
  there is no `xi-api-key` path. Prefer `ELEVENLABS_REFRESH_TOKEN` (long-lived)
  and let `_access_token()` mint/cache one-hour tokens through
  `securetoken.googleapis.com`; `ELEVENLABS_TOKEN` is a short-lived fallback.
  The refresh call must send `Origin`/`Referer: https://elevenlabs.io/` or the
  referrer-restricted Firebase key answers `403`.
- A rotated `refresh_token` from the refresh response must be written back to
  `.env` via `_persist_refresh_token()` (atomic replace, value never logged) and
  into `os.environ`. `/api/voice/status` reports availability/expiry and
  `refresh_configured` without exposing any token; `/call` shows the banner when
  voice is down and callers can still type.
- A provider `401` for `detected_unusual_activity` maps to
  `voice_account_restricted`, not `voice_token_expired`. A refreshed token can
  be valid (`/v1/user/subscription` returns 200) while text-to-dialogue still
  returns that error: it is an account restriction, so compare Firebase
  `sign_in_provider` between the working browser token and the refresh token
  (capture the refresh token from the same provider). README has the diagnosis.
- Voice routes are authenticated (`GET /api/voice/status`,
  `POST /api/voice/speak`); only `/call` is a public shell. Never log voice
  text, tokens, voice ids, or audio. `voice.speak` logs only `text_chars`,
  `chunks`, `duration_ms`, and `outcome`. Segments over the provider's
  2,000-character limit are split server-side with continuity metadata (the
  client never sends many prompts); `synthesize` rejects text over 4,000 chars.
- The call client streams through the existing `/api/chat/stream` with
  `mode: "call"` and synthesises whole text segments, never sentences. A
  `tool_start` frame flushes the pre-tool text so it plays while the tool runs;
  the text after the last tool call is flushed when the reply completes. Tool
  status frames are not spoken. Hang-up must abort the stream, stop recognition
  and audio, and call `/api/chat/stop` with the `X-Stream-ID`.
- Release the SSE `ReadableStreamDefaultReader` and defer the final tail
  segment by a tick after the stream ends. Firing it in the stream's completion
  microtask can reuse the closing HTTP connection and hang until the 30s fetch
  timeout. Tool-boundary segments are sent during the stream, so they are not
  affected by this deferral.
- `static/call.js` unlocks playback with a silent clip inside the Start-call
  gesture and calls `pauseRecognition()` while audio plays. Without that, the
  microphone hears SAGE's own voice, the barge-in handler stops playback, and
  the user sees text with no sound. `/chat` must link to `/call` (the voice UI
  only exists there).
- **Never call `recognition.start()` synchronously from `onend` (or from a
  `stop()` in the same task).** Chrome throws `InvalidStateError`, and the old
  `catch { /* already restarting */ }` swallowed it, so the mic stayed granted
  but dead for the rest of the call. Always go through `scheduleRestart()`,
  which defers by `RESTART_DELAY_MS` and re-checks `shouldBeListening()`.
- Recognition state is `recognitionRunning` (set in `onstart`/`onend`), not
  `listening`, which is only a status-line hint. `startListening()` returns
  early while running; `startWatchdog()` restarts a session that vanished
  without firing `onend`.
- `onerror` must handle every code. `not-allowed`, `service-not-allowed`,
  `audio-capture`, and `language-not-supported` are fatal (stop retrying,
  disable the mic, explain why); `network` retries with backoff;
  `no-speech`/`aborted` are routine and left to `onend`. Ignoring a code makes a
  broken recognizer indistinguishable from a working one.

### Streaming and UI

- SSE clients expect `{"delta"}` / `{"reasoning"}` frames, transient
  `{"tool": {"name": ..., "status": "start"|"end", "ok": ...}}` frames, a final
  `{"done": ...}` frame, an optional `{"stopped": true}` frame, and the literal
  `data: [DONE]` terminator. `TokenHarbor.run_tools_events()` emits the tool
  lifecycle; the app forwards names/status only, never tool arguments or results.
- Stopping is id-based: the stream response carries `X-Stream-ID`, and
  `POST /api/chat/stop {"id": ...}` sets that entry's `threading.Event` and calls
  the stored provider `response.close`. `_ACTIVE_STREAMS` in `app.py` is
  lock-guarded, scoped to the JWT user id, and must be unregistered in
  `generate()`'s `finally`. Never close another user's stream; unknown/foreign
  ids return `{"stopped": false}`.
- `core.stream_events` / `run_tools_events` take `cancel=` and `on_open=`; a
  provider exception while cancelled is a normal stop. Keep `events.close()` in
  the route's `finally` so the provider link dies even on disconnect, and give
  the stop request a concurrent server (dev server / threaded workers).
- A stopped turn is rolled back: remove both bubbles, splice the entries out of
  local history, restore the typed text into the composer, and never route an
  `AbortError` through `markError`.
- Tool status labels live in `static/chat.js` (`STRINGS.tool*`) and are shown in
  a transient element under the assistant bubble. They must not be written to
  `th_chat_history_*`; clear them when answer text starts or an error occurs,
  and let the brief completion label expire on its own.
- Frontend API calls must use `Session.authFetch` from `static/session.js`; plain
  `fetch` skips the Bearer header and the 401 redirect. The one exception is
  `static/auth.js`, whose login/register POSTs run before a token exists and
  must not redirect on 401.
- `templates/trusted.html` + `static/trusted-page.js` are the `/trusted` shell:
  the sharing switch, invite by email, accept/decline invitations, and expand a
  person to see their last seen and location. `renderWatching()` must render a
  `sharing: false` contact as "sharing paused" rather than skipping it. The
  "View activity" toggle fetches `/api/trusted/people/<id>`; a `403` means the
  gates closed and must surface as an error, not as an empty card.
- Browser state keys: `th_token`, `th_expires_at`, `th_user`, `th_theme`,
  `th_language`, and per-account `th_chat_history_<user id>`.
- `templates/memory.html` + `static/memory-page.js` are the `/memory` shell:
  list, per-item delete, export, "forget everything", and consent toggles. Every
  call uses `Session.authFetch`; deletion asks for `window.confirm` first, and
  `DELETE /api/memory` must send `{"confirm": true}` or it answers `400`.
- `templates/schedule.html` + `static/schedule-page.js` are the `/schedule`
  shell: today, upcoming, add-appointment, and routines.
  `static/schedule-reminder.js` adds the in-app banner on `/chat` (next
  appointment today) and is silent on any failure. `datetime-local` has no
  zone, so the page converts it with `new Date(value).toISOString()` before
  POSTing, and sends `tz_offset` for the "today" query.
- `static/location.js` is loaded by `/chat` and `/call` only, after
  `session.js`. It pings `/api/location` every 5s, skips a tick while a request
  is in flight, pauses on `visibilitychange`, and stops permanently on
  `PERMISSION_DENIED`. A `200` response carries place context, which it renders
  into `#place-context`; a `204` hides that banner. Geolocation needs a secure
  context: `127.0.0.1` / `localhost` work, a plain-HTTP LAN IP does not.
- `static/welcome.js` is unreferenced leftover code; `/` redirects to `/login`.
