"""Tests for core/core.py using a local mock of the Token Harbor gateway.

Run with:  python -m unittest discover -s tests -v
        or python -m pytest tests -q
"""

from __future__ import annotations

import gc
import json
import os
import sys
import threading
import unittest
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import TokenHarbor, TokenHarborError, parse_tool_calls, tool  # noqa: E402


# --- mock gateway ---------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence the test server
        pass

    def _read_payload(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {"_raw": raw.decode("utf-8", "replace")}

    def _record(self):
        call = {"path": self.path, "payload": self._read_payload(), "headers": dict(self.headers)}
        self.server.calls.append(call)
        return call

    def _send_json(self, status, body, extra_headers=None):
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _send_sse(self, chunks):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for chunk in chunks:
            data = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
            self.wfile.write(data)
            self.wfile.flush()
        self.close_connection = True

    def _handle(self):
        call = self._record()
        result = self.server.responder(len(self.server.calls), call)
        if result.get("sse"):
            self._send_sse(result["chunks"])
        else:
            self._send_json(result.get("status", 200), result.get("body", {}), result.get("headers"))

    def do_POST(self):
        self._handle()

    def do_GET(self):
        self._handle()

    def finish(self):
        """Close the accepted socket eagerly so it is never left to the GC."""
        try:
            super().finish()
        finally:
            try:
                self.connection.close()
            except OSError:
                pass


def start_server(responder):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.responder = responder
    httpd.calls = []
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    httpd.thread = thread
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    return httpd, base


def stop_server(httpd):
    """Shut the mock server down without leaving a half-closed socket (Windows)."""
    httpd.shutdown()
    thread = getattr(httpd, "thread", None)
    if thread is not None:
        thread.join(timeout=5)
    httpd.server_close()
    # unittest tears the test down in stages, so a mock handler's socket can be
    # finalized after this function returns and warn spuriously. Force that
    # finalization here, where we can ignore it. (The app code is unaffected:
    # an end-to-end teardown leaves no open sockets.)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        gc.collect()


def sse_chunk(text, finish_reason=None):
    payload = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def ok_completion(text):
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}


def error_body(code, message):
    return {"error": {"message": message, "type": "invalid_request_error", "code": code}}


def tool_completion(tool_calls, content=""):
    """Non-streaming completion whose assistant message carries tool calls."""
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ]
    }


def raw_tool_call(call_id, name, arguments):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def sse_tool_chunk(index, *, call_id=None, name=None, arguments=None):
    """One streamed ``delta.tool_calls`` fragment."""
    fragment = {"index": index, "type": "function"}
    if call_id:
        fragment["id"] = call_id
    function = {}
    if name:
        function["name"] = name
    if arguments is not None:
        function["arguments"] = arguments
    if function:
        fragment["function"] = function
    payload = {"choices": [{"index": 0, "delta": {"tool_calls": [fragment]}, "finish_reason": None}]}
    return f"data: {json.dumps(payload)}\n\n"


class ClientTestCase(unittest.TestCase):
    """Base: spins up a mock gateway per test and a fast-retry client."""

    responder = staticmethod(lambda n, call: {"status": 200, "body": ok_completion("ok")})

    def setUp(self):
        # The mock gateway uses real sockets and Windows/unittest can finalize one
        # after it becomes unreachable, emitting a spurious "unclosed"
        # ResourceWarning before cleanups run. unittest installs its own per-test
        # filter, so this has to be set here (not at import) to take effect. The
        # client library itself leaves no sockets open.
        warnings.filterwarnings("ignore", message="unclosed", category=ResourceWarning)

        self._servers = []
        self._clients = []
        self.httpd, self.base = start_server(self.responder)
        self._servers.append(self.httpd)
        # LIFO: these run in reverse order, so clients close *before* servers stop.
        self.addCleanup(self._stop_servers)
        self.addCleanup(lambda: [c.close() for c in self._clients])

    def _stop_servers(self):
        for httpd in self._servers:
            stop_server(httpd)

    def new_client(self, base=None, **kwargs):
        kwargs.setdefault("api_key", "thk_live_test")
        kwargs.setdefault("base_url", base or self.base)
        kwargs.setdefault("backoff_base", 0.01)  # keep retries fast
        kwargs.setdefault("backoff_max", 0.05)
        client = TokenHarbor(**kwargs)
        self._clients.append(client)
        return client

    #: tests call this for a client pointed at the mock gateway
    client = new_client

    def start_server(self, responder):
        """Spin up an ad-hoc mock gateway, cleaned up with the test."""
        httpd, base = start_server(responder)
        self._servers.append(httpd)
        return httpd, base


# --- core behaviour -------------------------------------------------------


class TestHappyPath(ClientTestCase):
    responder = staticmethod(lambda n, call: {"status": 200, "body": ok_completion("Hello, sailor.")})

    def test_chat_returns_text_and_sends_bearer_key(self):
        client = self.client()
        self.assertEqual(client.chat("Hi"), "Hello, sailor.")
        call = self.httpd.calls[-1]
        self.assertEqual(call["path"], "/v1/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], "Bearer thk_live_test")
        self.assertEqual(call["payload"]["model"], "deepseek-v4.1-flash")
        self.assertEqual(call["payload"]["messages"], [{"role": "user", "content": "Hi"}])
        self.assertNotIn("stream", call["payload"])

    def test_ask_grows_history(self):
        client = self.client()
        client.ask("one")
        client.ask("two")
        self.assertEqual(
            client.history,
            [
                {"role": "user", "content": "one"},
                {"role": "assistant", "content": "Hello, sailor."},
                {"role": "user", "content": "two"},
                {"role": "assistant", "content": "Hello, sailor."},
            ],
        )
        self.assertEqual(client.messages[0], {"role": "user", "content": "one"})

    def test_history_helpers_and_reset(self):
        client = self.client()
        client.system("be brief").user("hi").assistant("ok")
        self.assertEqual(len(client.messages), 3)
        client.reset()
        self.assertEqual(client.messages, [])
        client.load([{"role": "user", "content": "seeded"}])
        self.assertEqual(client.messages, [{"role": "user", "content": "seeded"}])

    def test_params_are_passed_and_none_filtered(self):
        client = self.client()
        client.chat("Hi", temperature=0.2, max_tokens=64, top_p=None, stream_options=None)
        payload = self.httpd.calls[-1]["payload"]
        self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(payload["max_tokens"], 64)
        self.assertNotIn("top_p", payload)

    def test_list_models(self):
        def responder(n, call):
            return {"status": 200, "body": {"data": [{"id": "m1"}, {"id": "m2"}]}}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        self.assertEqual([m["id"] for m in client.list_models()], ["m1", "m2"])
        self.assertEqual(httpd.calls[-1]["path"], "/v1/models")


class TestStreaming(ClientTestCase):
    def test_stream_yields_deltas_in_order(self):
        def responder(n, call):
            return {
                "sse": True,
                "chunks": [
                    ": keep-alive\n\n",  # comment line, must be ignored
                    sse_chunk("Hello"),
                    sse_chunk(", "),
                    sse_chunk("world"),
                    sse_chunk("", finish_reason="stop"),
                    "data: [DONE]\n\n",
                ],
            }

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        self.assertEqual(list(client.stream("Hi")), ["Hello", ", ", "world"])
        self.assertTrue(httpd.calls[-1]["payload"]["stream"])

    def test_stream_events_reports_done_and_usage(self):
        def responder(n, call):
            usage = {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
            return {
                "sse": True,
                "chunks": [
                    sse_chunk("hey"),
                    f"data: {json.dumps(usage)}\n\n",
                    sse_chunk("", finish_reason="stop"),
                    "data: [DONE]\n\n",
                ],
            }

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        events = list(client.stream_events("Hi", include_usage=True))
        self.assertEqual([e["type"] for e in events], ["delta", "done"])
        self.assertEqual(events[-1]["finish_reason"], "stop")
        self.assertEqual(events[-1]["usage"]["total_tokens"], 5)
        self.assertEqual(client.history, [])  # stream_events does not touch history

    def test_stream_ask_records_full_reply(self):
        def responder(n, call):
            return {"sse": True, "chunks": [sse_chunk("a"), sse_chunk("b", finish_reason="stop"), "data: [DONE]\n\n"]}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        self.assertEqual("".join(client.stream_ask("go")), "ab")
        self.assertEqual(
            client.history,
            [{"role": "user", "content": "go"}, {"role": "assistant", "content": "ab"}],
        )

    # -- hardening: edge cases --------------------------------------------

    def test_empty_stream_yields_only_done(self):
        def responder(n, call):
            return {"sse": True, "chunks": ["data: [DONE]\n\n"]}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        self.assertEqual(list(client.stream("Hi")), [])
        events = list(client.stream_events("Hi"))
        self.assertEqual([e["type"] for e in events], ["done"])
        self.assertIsNone(events[-1]["finish_reason"])
        self.assertEqual(events[-1]["tool_calls"], [])

    def test_finish_reason_length_is_surfaced(self):
        def responder(n, call):
            return {"sse": True, "chunks": [sse_chunk("trunc", finish_reason="length"), "data: [DONE]\n\n"]}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        events = list(client.stream_events("Hi", max_tokens=1))
        self.assertEqual("".join(e["content"] for e in events if e["type"] == "delta"), "trunc")
        self.assertEqual(events[-1]["finish_reason"], "length")

    def test_finish_reason_tool_calls_is_surfaced(self):
        def responder(n, call):
            return {
                "sse": True,
                "chunks": [
                    sse_tool_chunk(0, call_id="c1", name="f", arguments="{}"),
                    sse_chunk("", finish_reason="tool_calls"),
                    "data: [DONE]\n\n",
                ],
            }

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        events = list(client.stream_events("Hi"))
        self.assertEqual(events[-1]["finish_reason"], "tool_calls")

    def test_mid_stream_error_chunk_raises(self):
        def responder(n, call):
            return {
                "sse": True,
                "chunks": [
                    sse_chunk("partial"),
                    'data: {"error": {"message": "upstream exploded"}}\n\n',
                    "data: [DONE]\n\n",
                ],
            }

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        seen = []
        with self.assertRaises(TokenHarborError) as ctx:
            for piece in client.stream("Hi"):
                seen.append(piece)
        self.assertEqual(ctx.exception.code, "stream_error")
        self.assertIn("upstream exploded", str(ctx.exception))
        self.assertEqual(seen, ["partial"])  # deltas before the error are kept

    def test_multibyte_split_across_byte_chunks(self):
        line = (
            "data: "
            + json.dumps(
                {"choices": [{"index": 0, "delta": {"content": "héllo 😀"}}]},
                ensure_ascii=False,
            )
            + "\n\n"
        )
        encoded = line.encode("utf-8")
        split = encoded.index("😀".encode("utf-8")) + 2  # cut inside the 4-byte emoji

        def responder(n, call):
            return {"sse": True, "chunks": [encoded[:split], encoded[split:], "data: [DONE]\n\n"]}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        text = "".join(client.stream("Hi"))
        self.assertEqual(text, "héllo 😀")

    def test_malformed_json_line_is_skipped(self):
        def responder(n, call):
            return {
                "sse": True,
                "chunks": [
                    "data: {not valid json}\n\n",
                    sse_chunk("ok"),
                    sse_chunk("", finish_reason="stop"),
                    "data: [DONE]\n\n",
                ],
            }

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        self.assertEqual("".join(client.stream("Hi")), "ok")


# --- tool calling ---------------------------------------------------------


class TestToolCalling(ClientTestCase):
    responder = staticmethod(lambda n, call: {"status": 200, "body": ok_completion("done")})

    def test_tool_decorator_builds_schema(self):
        @tool
        def add(a: int, b: int = 2) -> int:
            """Add two integers."""
            return a + b

        schema = add.tool_schema
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "add")
        self.assertEqual(schema["function"]["description"], "Add two integers.")
        params = schema["function"]["parameters"]
        self.assertEqual(params["properties"]["a"]["type"], "integer")
        self.assertEqual(params["properties"]["b"]["type"], "integer")
        self.assertEqual(params["required"], ["a"])
        self.assertEqual(add.tool_name, "add")

    def test_tool_decorator_with_custom_name(self):
        @tool(name="search_web", description="Search the web.")
        def search(query: str) -> str:
            return query

        self.assertEqual(search.tool_schema["function"]["name"], "search_web")
        self.assertEqual(search.tool_schema["function"]["description"], "Search the web.")

    def test_parse_tool_calls(self):
        calls = parse_tool_calls([raw_tool_call("id1", "f", '{"a": 1}')])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].id, "id1")
        self.assertEqual(calls[0].name, "f")
        self.assertEqual(calls[0].arguments, {"a": 1})
        self.assertIsNone(calls[0].parse_error)

    def test_parse_tool_calls_invalid_json(self):
        calls = parse_tool_calls([raw_tool_call("id1", "f", "{oops")])
        self.assertEqual(calls[0].arguments, {})
        self.assertIsNotNone(calls[0].parse_error)
        self.assertEqual(calls[0].raw_arguments, "{oops")

    def test_chat_message_exposes_tool_calls(self):
        def responder(n, call):
            body = tool_completion(
                [raw_tool_call("call_1", "get_weather", '{"city": "Paris"}')], content="let me check"
            )
            return {"status": 200, "body": body}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        message = client.chat_message("Weather?")
        self.assertEqual(message["content"], "let me check")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "get_weather")

    def test_run_tools_executes_handler_and_feeds_result_back(self):
        seen = []

        @tool
        def get_weather(city: str) -> dict:
            """Get the weather."""
            seen.append(city)
            return {"city": city, "temp_c": 21}

        def responder(n, call):
            if n == 1:
                return {
                    "status": 200,
                    "body": tool_completion([raw_tool_call("call_1", "get_weather", '{"city": "Paris"}')]),
                }
            return {"status": 200, "body": ok_completion("It is 21C in Paris.")}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        text = client.run_tools("Weather in Paris?", tools=[get_weather])

        self.assertEqual(text, "It is 21C in Paris.")
        self.assertEqual(seen, ["Paris"])
        second = httpd.calls[1]["payload"]
        self.assertEqual(second["tools"][0]["function"]["name"], "get_weather")
        self.assertEqual([m["role"] for m in second["messages"]], ["user", "assistant", "tool"])
        self.assertEqual(second["messages"][1]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(second["messages"][2]["tool_call_id"], "call_1")
        self.assertIn("21", second["messages"][2]["content"])
        self.assertEqual(
            [m["role"] for m in client.messages],
            ["user", "assistant", "tool", "assistant"],
        )

    def test_run_tools_unknown_tool_reports_error(self):
        schema = {
            "type": "function",
            "function": {
                "name": "ghost",
                "description": "",
                "parameters": {"type": "object", "properties": {}},
            },
        }

        def responder(n, call):
            if n == 1:
                return {"status": 200, "body": tool_completion([raw_tool_call("c1", "ghost", "{}")])}
            return {"status": 200, "body": ok_completion("recovered")}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        self.assertEqual(client.run_tools("go", tools=[schema]), "recovered")
        tool_message = httpd.calls[1]["payload"]["messages"][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertIn("Unknown tool", tool_message["content"])

    def test_run_tools_handler_exception_is_reported_to_model(self):
        @tool
        def boom(x: str) -> str:
            """Always fails."""
            raise ValueError("nope")

        def responder(n, call):
            if n == 1:
                return {"status": 200, "body": tool_completion([raw_tool_call("c1", "boom", '{"x": "y"}')])}
            return {"status": 200, "body": ok_completion("sorry")}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        self.assertEqual(client.run_tools("go", tools=[boom]), "sorry")
        tool_message = httpd.calls[1]["payload"]["messages"][-1]
        self.assertIn("ValueError: nope", tool_message["content"])

    def test_run_tools_invalid_arguments_reported_to_model(self):
        schema = {
            "type": "function",
            "function": {
                "name": "f",
                "description": "",
                "parameters": {"type": "object", "properties": {}},
            },
        }

        def responder(n, call):
            if n == 1:
                return {"status": 200, "body": tool_completion([raw_tool_call("c1", "f", "{bad json")])}
            return {"status": 200, "body": ok_completion("ok then")}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        self.assertEqual(client.run_tools("go", tools=[schema], handlers={"f": lambda **kw: "x"}), "ok then")
        tool_message = httpd.calls[1]["payload"]["messages"][-1]
        self.assertIn("Invalid JSON arguments", tool_message["content"])

    def test_run_tools_stops_at_max_rounds(self):
        schema = {
            "type": "function",
            "function": {
                "name": "f",
                "description": "",
                "parameters": {"type": "object", "properties": {}},
            },
        }

        def responder(n, call):
            return {"status": 200, "body": tool_completion([raw_tool_call(f"c{n}", "f", "{}")])}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base, max_retries=0)
        with self.assertRaises(TokenHarborError) as ctx:
            client.run_tools("go", tools=[schema], handlers={"f": lambda **kw: "ok"}, max_rounds=1)
        self.assertEqual(ctx.exception.code, "max_tool_rounds")
        self.assertEqual(len(httpd.calls), 2)

    def test_run_tools_rejects_undecorated_callable(self):
        client = self.new_client()
        with self.assertRaises(TokenHarborError) as ctx:
            client.run_tools("go", tools=[lambda: None])
        self.assertEqual(ctx.exception.code, "invalid_tool")

    # -- streamed tool calls ----------------------------------------------

    def test_stream_events_parse_tool_call_fragments(self):
        def responder(n, call):
            return {
                "sse": True,
                "chunks": [
                    sse_tool_chunk(0, call_id="call_1", name="get_weather"),
                    sse_tool_chunk(0, arguments='{"city":'),
                    sse_tool_chunk(0, arguments=' "Oslo"}'),
                    sse_chunk("", finish_reason="tool_calls"),
                    "data: [DONE]\n\n",
                ],
            }

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        events = list(client.stream_events("Hi"))
        done = events[-1]
        self.assertEqual(done["finish_reason"], "tool_calls")
        self.assertEqual(len(done["tool_calls"]), 1)
        call = done["tool_calls"][0]
        self.assertEqual((call.id, call.name, call.arguments), ("call_1", "get_weather", {"city": "Oslo"}))
        self.assertEqual(call.raw_arguments, '{"city": "Oslo"}')

    def test_stream_events_handle_interleaved_tool_calls(self):
        def responder(n, call):
            return {
                "sse": True,
                "chunks": [
                    sse_tool_chunk(0, call_id="a", name="f"),
                    sse_tool_chunk(1, call_id="b", name="g"),
                    sse_tool_chunk(0, arguments='{"x": 1}'),
                    sse_tool_chunk(1, arguments='{"y": 2}'),
                    sse_chunk("", finish_reason="tool_calls"),
                    "data: [DONE]\n\n",
                ],
            }

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        done = list(client.stream_events("Hi"))[-1]
        calls = done["tool_calls"]
        self.assertEqual([c.id for c in calls], ["a", "b"])
        self.assertEqual(calls[0].arguments, {"x": 1})
        self.assertEqual(calls[1].arguments, {"y": 2})

    def test_streamed_invalid_arguments_set_parse_error(self):
        def responder(n, call):
            return {
                "sse": True,
                "chunks": [
                    sse_tool_chunk(0, call_id="c1", name="f"),
                    sse_tool_chunk(0, arguments="{oops"),
                    sse_chunk("", finish_reason="tool_calls"),
                    "data: [DONE]\n\n",
                ],
            }

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        done = list(client.stream_events("Hi"))[-1]
        self.assertIsNotNone(done["tool_calls"][0].parse_error)

    def test_run_tools_stream_executes_tool_and_continues(self):
        seen = []

        @tool
        def get_weather(city: str) -> dict:
            """Get the weather."""
            seen.append(city)
            return {"city": city, "temp_c": 21}

        def responder(n, call):
            if n == 1:
                return {
                    "sse": True,
                    "chunks": [
                        sse_chunk("Checking. "),
                        sse_tool_chunk(0, call_id="call_s", name="get_weather"),
                        sse_tool_chunk(0, arguments='{"city"'),
                        sse_tool_chunk(0, arguments=': "Tokyo"}'),
                        sse_chunk("", finish_reason="tool_calls"),
                        "data: [DONE]\n\n",
                    ],
                }
            return {
                "sse": True,
                "chunks": [sse_chunk("It is 21C"), sse_chunk(".", finish_reason="stop"), "data: [DONE]\n\n"],
            }

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base)
        text = "".join(client.run_tools_stream("Weather in Tokyo?", tools=[get_weather]))
        self.assertIn("It is 21C", text)
        self.assertEqual(seen, ["Tokyo"])
        self.assertEqual(
            [m["role"] for m in client.messages],
            ["user", "assistant", "tool", "assistant"],
        )
        assistant = client.messages[1]
        self.assertEqual(assistant["tool_calls"][0]["function"]["arguments"], '{"city": "Tokyo"}')
        self.assertIn("21", client.messages[2]["content"])


# --- error handling -------------------------------------------------------


class TestErrors(ClientTestCase):
    def _expect_error(self, status, code, retries=0):
        def responder(n, call):
            return {"status": status, "body": error_body(code, f"boom {status}")}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base, max_retries=retries, backoff_base=0.01)
        with self.assertRaises(TokenHarborError) as ctx:
            client.chat("Hi")
        return ctx.exception, httpd

    def test_401_invalid_api_key(self):
        err, _ = self._expect_error(401, "invalid_api_key")
        self.assertEqual(err.status_code, 401)
        self.assertEqual(err.code, "invalid_api_key")
        self.assertIn("boom 401", str(err))
        self.assertFalse(err.retryable)

    def test_402_insufficient_credit(self):
        err, _ = self._expect_error(402, "insufficient_credit")
        self.assertEqual(err.code, "insufficient_credit")
        self.assertFalse(err.retryable)

    def test_404_unknown_model(self):
        err, _ = self._expect_error(404, "model_not_found")
        self.assertEqual(err.status_code, 404)

    def test_409_conflict_is_retried_then_raised(self):
        attempts = {"n": 0}

        def responder(n, call):
            attempts["n"] += 1
            return {"status": 409, "body": error_body("conflict", "request already in progress")}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base, max_retries=2, backoff_base=0.01)
        with self.assertRaises(TokenHarborError) as ctx:
            client.chat("Hi")
        self.assertEqual(ctx.exception.code, "conflict")
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(attempts["n"], 3)  # initial + 2 retries
        self.assertIn("conflicts with server state", str(ctx.exception))

    def test_429_retries_then_succeeds_and_honours_retry_after(self):
        def responder(n, call):
            if n == 1:
                return {
                    "status": 429,
                    "body": error_body("rate_limited", "slow down"),
                    "headers": {"Retry-After": "0"},
                }
            return {"status": 200, "body": ok_completion("finally")}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base, max_retries=2, backoff_base=0.01)
        self.assertEqual(client.chat("Hi"), "finally")
        self.assertEqual(len(httpd.calls), 2)

    def test_500_is_retryable_and_gives_up_after_max_retries(self):
        def responder(n, call):
            return {"status": 500, "body": error_body("internal_error", "kaboom")}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base, max_retries=1, backoff_base=0.01)
        with self.assertRaises(TokenHarborError) as ctx:
            client.chat("Hi")
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(len(httpd.calls), 2)

    def test_unknown_status_gets_generic_client_error(self):
        err, _ = self._expect_error(418, "teapot")
        self.assertEqual(err.status_code, 418)
        self.assertFalse(err.retryable)

    def test_non_json_error_body_does_not_crash(self):
        def responder(n, call):
            return {"status": 502, "body": {}, "headers": {}}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base, max_retries=0)
        with self.assertRaises(TokenHarborError) as ctx:
            client.chat("Hi")
        self.assertEqual(ctx.exception.status_code, 502)

    def test_streaming_error_status_raises_before_any_delta(self):
        def responder(n, call):
            return {"status": 409, "body": error_body("conflict", "router busy")}

        httpd, base = self.start_server(responder)
        client = self.new_client(base=base, max_retries=0)
        with self.assertRaises(TokenHarborError) as ctx:
            list(client.stream("Hi"))
        self.assertEqual(ctx.exception.status_code, 409)

    def test_invalid_key_format_is_reported_from_gateway(self):
        def responder(n, call):
            return {"status": 401, "body": {"error": {"message": "invalid_api_key"}}}

        httpd, base = start_server(responder)
        self.addCleanup(httpd.shutdown)
        client = TokenHarbor(api_key="bad", base_url=base, max_retries=0)
        with self.assertRaises(TokenHarborError) as ctx:
            client.chat("Hi")
        self.assertEqual(ctx.exception.code, "invalid_api_key")


class TestConstruction(unittest.TestCase):
    def test_missing_key_raises(self):
        saved = {name: os.environ.pop(name, None) for name in ("TH_API_KEY", "TOKENHARBOR_API_KEY", "TOKENHARBOR_KEY")}
        try:
            with self.assertRaises(TokenHarborError) as ctx:
                TokenHarbor()
            self.assertEqual(ctx.exception.code, "missing_api_key")
        finally:
            for name, value in saved.items():
                if value is not None:
                    os.environ[name] = value

    def test_key_from_env(self):
        os.environ["TH_API_KEY"] = "thk_live_from_env"
        try:
            self.assertEqual(TokenHarbor().api_key, "thk_live_from_env")
        finally:
            os.environ.pop("TH_API_KEY", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
