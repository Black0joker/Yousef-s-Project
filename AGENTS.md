# AGENTS.md

Flask chat app plus a standalone Token Harbor client. `README.md` is the detailed
reference for endpoints, config, client API, and error codes; update it when
behavior changes. This file covers workflow and non-obvious wiring.

## Layout

- `core/core.py` — standalone gateway client (streaming, retries, tools,
  history), re-exported by `core/__init__.py`. It must not import Flask.
- `app.py` — Flask routes, request validation, system-prompt assembly, SSE.
  Uses one lazily created shared client via `get_client()`.
- `auth.py` — SQLite users, scrypt password hashes, JWTs, and the
  `before_request` auth middleware registered by `init_app(app)`.
- `memory.py` — personal-memory schema, entity/relationship/fact CRUD, and the
  `save_memory` / `recall_memory` agent tool schemas and handlers.
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

- Importing `app.py` immediately runs `auth.init_db()` and `memory.init_db()`.
  Tests set `TH_DB_PATH` and `JWT_SECRET` **before** importing `app`/`auth`
  (see top of `tests/test_app.py`); otherwise `instance/app.db` is created as a
  side effect.
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
- Memory query results always include `created_at` for every entity,
  relationship, and fact; facts also include `observed_at` and `valid_until`.
  Keep those date fields when changing the row serializers.
- All routes are guarded by the auth middleware except `PUBLIC_PATHS` and
  `PUBLIC_PREFIXES` in `auth.py`. Add new public routes there; API paths get a
  JSON 401, page paths redirect to `/login`.
- `/chat` is intentionally a public, data-free HTML shell; authentication and
  user data come from `/api/auth/me`, and chat calls go through the guarded API.
- The client may only send `user`/`assistant` messages; `system` is rejected.
  The server always inserts the system prompt at index 0 in `build_payload()`.
- Adding a language requires coordinated edits to `LANGUAGE_FILES`,
  `LANGUAGE_ALIASES`, and `LANGUAGE_DIRECTIVES` in `app.py`, plus `STRINGS` and
  `LANG_ALIASES` in `static/chat.js`. `TH_SYSTEM_PROMPT` overrides all languages.
- Both prompt files contain a “Memory Tool Protocol” naming `save_memory` and
  `recall_memory`; keep those names in sync with `memory.py` and `static/chat.js`.
- Default models differ: `app.py` uses `deepseek-v4.1-flash:free` (no balance
  needed), while `core` defaults to `deepseek-v4.1-flash`. Both honor `TH_MODEL`.
- SSE clients expect `{"delta"}` / `{"reasoning"}` frames, transient
  `{"tool": {"name": ..., "status": "start"|"end", "ok": ...}}` frames, a final
  `{"done": ...}` frame, and the literal `data: [DONE]` terminator.
  `TokenHarbor.run_tools_events()` emits the tool lifecycle; the app forwards
  names/status only, never tool arguments or results.
- Tool status labels live in `static/chat.js` (`STRINGS.tool*`) and are shown in
  a transient element under the assistant bubble. They must not be written to
  `th_chat_history_*`; clear them when answer text starts or an error occurs,
  and let the brief completion label expire on its own.
- Frontend API calls must use `Session.authFetch` from `static/session.js`; plain
  `fetch` skips the Bearer header and the 401 redirect.
- Browser state keys: `th_token`, `th_expires_at`, `th_user`, `th_language`, and
  per-account `th_chat_history_<user id>`.
- `static/welcome.js` is unreferenced leftover code; `/` redirects to `/login`.
