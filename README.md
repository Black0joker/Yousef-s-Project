# Youssef's Project

A small Python client for [Token Harbor](https://tokenharbor.ai) — one
OpenAI-compatible gateway for many models. It handles message passing, token
streaming, conversation history, tool calling, and typed error handling.

- **Gateway:** `https://tokenharbor.ai/v1` (OpenAI-compatible `/v1/chat/completions`)
- **Auth:** universal key `thk_live_...`, read from the environment
- **Streaming:** Server-Sent Events, exposed as an iterator of text deltas
- **Tools:** `@tool` decorator + automatic tool-calling loop
- **Errors:** every HTTP status mapped to a clear code, with retries and backoff

## Project layout

```
.
├── app.py                 # Flask chat app (welcome + chat + streaming API)
├── templates/
│   ├── welcome.html       # name form
│   └── chat.html          # greeting + chat UI
├── static/
│   ├── style.css
│   ├── welcome.js         # saves the name to localStorage, then goes to /chat
│   └── chat.js            # streams replies, keeps history in localStorage
├── core/
│   ├── __init__.py        # package exports
│   └── core.py            # TokenHarbor client, streaming, history, tools
├── tests/
│   ├── test_app.py        # Flask app tests (stubbed client, no network)
│   ├── test_core.py       # client unit tests (local mock gateway, no network)
│   └── integration/
│       └── test_integration.py   # live tests against tokenharbor.ai
├── requirements.txt
├── .env.example           # template for your API key
├── .gitignore
└── README.md
```

## Requirements

- Python 3.10+
- `requests` and `python-dotenv`

```bash
pip install requests python-dotenv
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

1. **`/`** — welcome page. You enter your name; it is saved in the browser's
   `localStorage` (not on the server).
2. **`/chat`** — greets you with your name, then lets you chat. If no name is
   saved you are sent back to `/`.
3. **`POST /api/chat/stream`** — the server calls Token Harbor and streams the
   reply back token by token over SSE.
4. **`POST /api/chat`** — non-streaming fallback returning `{"reply": "..."}`.

The browser never sees the API key; all calls go through Flask. The server
prepends the system prompt so the message sent to the model always looks like:

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

The UI also has:

- **Light / dark mode** — a toggle in the chat header and on the welcome page.
  It follows your OS theme until you pick one, then remembers the choice.
- **Delete your message** — hover one of *your* messages and click the trash
  icon; it removes that message together with the assistant's reply. `Clear`
  wipes the whole conversation. Message ids stay in the browser, so the API only
  ever receives `role` and `content`.

Config (all optional except the key):

| Variable | Default | Purpose |
| --- | --- | --- |
| `TH_API_KEY` | — | required; the `thk_live_...` key |
| `TH_MODEL` | `deepseek-v4.1-flash:free` | model used by the web app |
| `TH_SYSTEM_PROMPT` | built-in text | system prompt injected at index 0 |
| `PORT` | `5000` | dev server port |
| `FLASK_DEBUG` | `1` | set to `0` to disable the debug reloader |

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
# unit tests — offline (mock gateway + stubbed client), no key or network
python -m unittest discover -s tests -v

# integration tests — live calls against tokenharbor.ai (needs TH_API_KEY)
python -m unittest discover -s tests/integration -v
```

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
| `@tool` / `function_tool` | Build a tool schema from a function |
| `parse_tool_calls(...)` | Convert raw `tool_calls` to `ToolCall` objects |
| `.system/.user/.assistant/.tool` | Append a message |
| `.messages` / `.history` / `.load` / `.reset` | History access |
| `.list_models()` | `GET /v1/models` |
| `.close()` / `with TokenHarbor() as c:` | Clean up the session |