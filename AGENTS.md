# AGENTS.md

Flask chat app plus a standalone Token Harbor client. `README.md` is the detailed
reference for endpoints, config, client API, and error codes; update it when
behavior changes. This file covers workflow and non-obvious wiring.

## Layout

- `core/core.py` — standalone gateway client (streaming, retries, tools,
  history), re-exported by `core/__init__.py`. It must not import Flask.
- `app.py` — Flask routes, request validation, system-prompt assembly, SSE.
  Uses one lazily created shared client via `get_client()`.
- `auth.py` — SQLite users, scrypt password hashes, JWTs, profile/password
  updates, and the `before_request` auth middleware registered by
  `init_app(app)`.
- `memory.py` — personal-memory schema, entity/relationship/fact CRUD, and the
  `save_memory` / `recall_memory` agent tool schemas and handlers.
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
- `discover -s tests` does not recurse into `tests/integration` (no
  `__init__.py`); that suite runs separately and skips without a key.
- Live tests use `TH_TEST_MODEL` (default `deepseek-v4.1-flash:free`). On
  PowerShell: `$env:TH_TEST_MODEL = "qwen3.7-max"` before the command.

## Gotchas

- Importing `app.py` immediately configures logging, then runs `auth.init_db()`
  and `memory.init_db()`. Tests set `TH_DB_PATH`, `JWT_SECRET`, `LOG_LEVEL`, and
  `LOG_FILE` **before** importing `app`/`auth` (see top of `tests/test_app.py`);
  otherwise `instance/app.db` and `instance/app.log` are created as side effects.
- `core/core.py` calls `load_dotenv()` at import, so a developer `.env` leaks
  into tests. `test_app.py` deliberately clears `TH_SYSTEM_PROMPT` so tests
  exercise `prompts/*.md`.
- No migration framework: `auth.init_db()` / `memory.init_db()` only do
  `CREATE TABLE IF NOT EXISTS`. Schema changes need explicit handling for
  existing databases. Memory tables are added automatically to an existing
  `instance/app.db`.
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
  voice is down and callers can still type. A provider `401` for
  `detected_unusual_activity` maps to `voice_account_restricted`, not
  `voice_token_expired`.
- A refreshed token can be valid (`/v1/user/subscription` returns 200) while
  text-to-dialogue still returns `detected_unusual_activity`; that is an
  ElevenLabs account restriction, not a refresh failure. Compare Firebase
  `firebase.sign_in_provider` between the working browser token and the refresh
  token: capture the refresh token from the same provider (for example sign in
  with Google before copying `stsTokenManager.refreshToken`).
- Voice routes are authenticated (`GET /api/voice/status`,
  `POST /api/voice/speak`); only `/call` is a public shell. Never log voice
  text, tokens, voice ids, or audio. `voice.speak` logs only `text_chars`,
  `chunks`, `duration_ms`, and `outcome`.
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
- All routes are guarded by the auth middleware except `PUBLIC_PATHS` and
  `PUBLIC_PREFIXES` in `auth.py`. Add new public routes there; API paths get a
  JSON 401, page paths redirect to `/login`.
- `/chat`, `/call`, and `/settings` are intentionally public, data-free HTML
  shells; authentication and user data come from `/api/auth/me`, and every write
  goes through the guarded API (`PATCH /api/auth/profile`, `POST /api/auth/password`).
- Profile updates reissue the JWT because `full_name`/`email` live in the claims;
  the client must `Session.save(...)` the returned token. Changing the email
  requires `current_password`, and both update paths use `auth.AuthError` codes.
  Password changes do not revoke other tokens (stateless JWTs, 7-day TTL), so
  never log names, emails, or passwords to compensate.
- The client may only send `user`/`assistant` messages; `system` is rejected.
  The server always inserts the system prompt at index 0 in `build_payload()`.
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
- Default models differ: `app.py` uses `deepseek-v4.1-flash:free` (no balance
  needed), while `core` defaults to `deepseek-v4.1-flash`. Both honor `TH_MODEL`.
- SSE clients expect `{"delta"}` / `{"reasoning"}` frames, transient
  `{"tool": {"name": ..., "status": "start"|"end", "ok": ...}}` frames, a final
  `{"done": ...}` frame, and the literal `data: [DONE]` terminator.
  `TokenHarbor.run_tools_events()` emits the tool lifecycle; the app forwards
  names/status only, never tool arguments or results.
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
  `fetch` skips the Bearer header and the 401 redirect.
- Browser state keys: `th_token`, `th_expires_at`, `th_user`, `th_language`, and
  per-account `th_chat_history_<user id>`.
- `static/welcome.js` is unreferenced leftover code; `/` redirects to `/login`.
