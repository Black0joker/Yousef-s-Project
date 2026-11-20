/* Regression harness for the /call microphone state machine.
 *
 * There is no JS test runner in this project, so this is a plain Node script
 * that loads static/call.js into a VM with a fake SpeechRecognition. It exists
 * because the "mic is granted but nothing happens" bug cannot be reproduced
 * with the Python suite: the audio never reaches the server, so a wedged
 * recognizer leaves no server-side trace at all.
 *
 *   node tests/js/call_mic_harness.js
 *
 * It models the Chrome rules the client depends on:
 *   - start() is asynchronous; onstart arrives later (slower than
 *     RESTART_DELAY_MS, which is what makes a naive restart race).
 *   - start() while a session is starting, running, or still closing throws
 *     InvalidStateError.
 *   - abort() discards the in-flight utterance; stop() flushes it as a final
 *     result (the source of duplicate turns).
 *
 * Exit code is non-zero when any check fails.
 */
"use strict";

var fs = require("fs");
var path = require("path");
var vm = require("vm");

var CALL_JS = path.join(__dirname, "..", "..", "static", "call.js");

function makeElement(id) {
  var classes = {};
  return {
    id: id,
    textContent: "",
    value: "",
    disabled: false,
    title: "",
    style: {},
    _attrs: {},
    _listeners: {},
    classList: {
      add: function () {
        for (var i = 0; i < arguments.length; i++) classes[arguments[i]] = true;
      },
      remove: function () {
        for (var i = 0; i < arguments.length; i++) delete classes[arguments[i]];
      },
      contains: function (c) {
        return !!classes[c];
      },
      toggle: function (c, on) {
        if (on) classes[c] = true;
        else delete classes[c];
      }
    },
    setAttribute: function (k, v) {
      this._attrs[k] = String(v);
    },
    getAttribute: function (k) {
      return this._attrs[k];
    },
    addEventListener: function (type, fn) {
      (this._listeners[type] = this._listeners[type] || []).push(fn);
    },
    click: function () {
      (this._listeners.click || []).forEach(function (fn) {
        fn({ preventDefault: function () {} });
      });
    },
    appendChild: function () {},
    removeChild: function () {},
    innerHTML: "",
    scrollTop: 0,
    scrollHeight: 0,
    parentNode: null
  };
}

var ELEMENT_IDS = [
  "greeting", "avatar", "logout", "logout-label", "lang-toggle", "lang-label",
  "start-call", "start-label", "hangup", "hangup-label", "mic-toggle", "mic-label",
  "call-title", "call-subtitle", "call-status", "call-orb", "voice-banner",
  "transcript", "composer", "input", "send", "status", "status-dot", "call-badge",
  "back-to-chat"
];

function buildEnv(opts) {
  opts = opts || {};
  var elements = {};
  ELEMENT_IDS.forEach(function (id) {
    elements[id] = makeElement(id);
  });

  var trace = [];
  var timers = [];
  var now = 0;
  var clock = 0;
  var nextTimerId = 1;
  var store = { th_call_trace: "1" };

  // Virtual Date so START_TIMEOUT_MS arithmetic uses the harness clock.
  function VirtualDate() {
    if (!(this instanceof VirtualDate)) return new Date(clock).toString();
    if (arguments.length) return new Date(arguments[0]);
    return new Date(clock);
  }
  VirtualDate.now = function () {
    return clock;
  };
  VirtualDate.parse = Date.parse;
  VirtualDate.UTC = Date.UTC;
  VirtualDate.prototype = Date.prototype;

  function FakeRecognition() {
    this.continuous = false;
    this.interimResults = false;
    this.lang = "";
    this._state = "idle"; // idle | starting | running | closing
    this._cancelled = false;
    FakeRecognition.instances.push(this);
  }
  FakeRecognition.instances = [];

  FakeRecognition.prototype.start = function () {
    if (this._state !== "idle") {
      trace.push("start:InvalidStateError");
      var err = new Error("already started");
      err.name = "InvalidStateError";
      throw err;
    }
    trace.push("start");
    this._state = "starting";
    this._cancelled = false;
    var self = this;
    // Slower than RESTART_DELAY_MS on purpose: that is the window in which a
    // naive resume fires a second start and Chrome rejects it.
    timers.push({
      at: now + 500,
      fn: function () {
        if (self._cancelled) {
          self._state = "idle";
          trace.push("onend:cancelled");
          if (self.onend) self.onend();
          return;
        }
        self._state = "running";
        trace.push("onstart");
        if (self.onstart) self.onstart();
      }
    });
  };

  FakeRecognition.prototype._settle = function (flushFinal) {
    var wasActive = this._state !== "idle";
    var wasRunning = this._state === "running";
    if (!wasActive) return;
    if (this._state === "starting") {
      // A stop during the handshake does not resolve instantly.
      this._cancelled = true;
      return;
    }
    this._state = "closing";
    var self = this;
    timers.push({
      at: now + 1,
      fn: function () {
        if (flushFinal && wasRunning && self.onresult) {
          trace.push("onresult:final-flush");
          self.onresult({
            resultIndex: 0,
            results: [{ 0: { transcript: "flushed words" }, isFinal: true }]
          });
        }
        self._state = "idle";
        trace.push("onend");
        if (self.onend) self.onend();
      }
    });
  };

  FakeRecognition.prototype.abort = function () {
    trace.push("abort");
    this._settle(false);
  };
  FakeRecognition.prototype.stop = function () {
    trace.push("stop");
    this._settle(true);
  };

  var window = {
    SpeechRecognition: FakeRecognition,
    isSecureContext: opts.secure !== false,
    setTimeout: function (fn, ms) {
      var t = { at: now + (ms || 0), fn: fn, id: nextTimerId++ };
      timers.push(t);
      return t.id;
    },
    clearTimeout: function (id) {
      for (var i = 0; i < timers.length; i++) {
        if (timers[i].id === id) {
          timers.splice(i, 1);
          return;
        }
      }
    },
    setInterval: function (fn, ms) {
      var t = { at: now + ms, fn: fn, id: nextTimerId++, every: ms };
      timers.push(t);
      return t.id;
    },
    clearInterval: function (id) {
      for (var i = 0; i < timers.length; i++) {
        if (timers[i].id === id) {
          timers.splice(i, 1);
          return;
        }
      }
    },
    location: { replace: function () {} },
    addEventListener: function () {}
  };

  var document = {
    getElementById: function (id) {
      return elements[id] || null;
    },
    createElement: function (tag) {
      return makeElement(tag);
    },
    addEventListener: function () {},
    documentElement: { lang: "en", dir: "ltr" },
    body: { classList: { toggle: function () {}, add: function () {}, remove: function () {} } },
    hidden: false
  };

  var sandbox = {
    window: window,
    document: document,
    localStorage: {
      getItem: function (k) {
        return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null;
      },
      setItem: function (k, v) {
        store[k] = String(v);
      },
      removeItem: function (k) {
        delete store[k];
      }
    },
    navigator: { userAgent: "Chrome/157", mediaDevices: opts.mediaDevices },
    console: {
      // Keep the trace visible to the harness so assertions can see lifecycle
      // events. call.js gates them on th_call_trace, which we set below.
      info: function () {},
      log: function () {},
      warn: function () {},
      error: function () {}
    },
    Audio: function () {
      return { play: function () { return Promise.resolve(); }, pause: function () {}, currentTime: 0 };
    },
    URL: { createObjectURL: function () { return "blob:x"; }, revokeObjectURL: function () {} },
    AbortController: function () {
      this.signal = {};
      this.abort = function () {};
    },
    Promise: Promise,
    JSON: JSON,
    Date: VirtualDate,
    Object: Object,
    String: String,
    Array: Array,
    Math: Math,
    TextEncoder: TextEncoder,
    setTimeout: window.setTimeout,
    clearTimeout: window.clearTimeout,
    setInterval: window.setInterval,
    clearInterval: window.clearInterval
  };

  // Voice is configured and the stream emits one delta, so the greeting really
  // reaches pumpSpeech and pauses the microphone.
  sandbox.Session = {
    requireSession: function () {
      return true;
    },
    save: function () {},
    clear: function () {},
    getToken: function () {
      return "t";
    },
    authFetch: function (url) {
      if (url === "/api/voice/status") {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: { get: function () { return null; } },
          json: function () {
            return Promise.resolve({
              configured: true, available: true, expired: false, reason: "refresh"
            });
          }
        });
      }
      if (url === "/api/voice/speak") {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: { get: function () { return null; } },
          blob: function () {
            return Promise.resolve({ size: 10 });
          }
        });
      }
      if (url !== "/api/chat/stream") return new Promise(function () {});
      var chunks = ['data: {"delta":"Hello, this is SAGE."}\n\n', "data: [DONE]\n\n"];
      var index = 0;
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: {
          get: function (h) {
            if (h === "X-Stream-ID") return "s1";
            if (h === "X-Conversation-ID") return "c1";
            return null;
          }
        },
        body: {
          getReader: function () {
            return {
              read: function () {
                if (index >= chunks.length) return Promise.resolve({ done: true });
                return Promise.resolve({ done: false, value: new TextEncoder().encode(chunks[index++]) });
              },
              releaseLock: function () {}
            };
          }
        }
      });
    }
  };
  window.Session = sandbox.Session;

  var env = {
    sandbox: sandbox,
    elements: elements,
    trace: trace,
    FakeRecognition: FakeRecognition,
    advance: function (ms) {
      var end = now + ms;
      for (;;) {
        timers.sort(function (a, b) {
          return a.at - b.at;
        });
        var t = timers[0];
        if (!t || t.at > end) break;
        now = t.at;
        clock = t.at;
        if (t.every) t.at = now + t.every;
        else timers.shift();
        t.fn();
      }
      now = end;
      clock = end;
    }
  };
  return env;
}

function load(env) {
  vm.createContext(env.sandbox);
  vm.runInContext(fs.readFileSync(CALL_JS, "utf8"), env.sandbox, { filename: "call.js" });
  return env;
}

function run(opts) {
  return load(buildEnv(opts));
}

var flush = function () {
  return new Promise(function (resolve) {
    setImmediate(resolve);
  });
};

function report(name, ok, detail) {
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  -- " + detail : ""));
  if (!ok) process.exitCode = 1;
}

function micOptions() {
  return {
    mediaDevices: {
      getUserMedia: function () {
        return Promise.resolve({ getTracks: function () { return []; } });
      }
    }
  };
}

function count(list, value) {
  return list.filter(function (item) {
    return item === value;
  }).length;
}

async function main() {
  // 1. Mute/unmute must round-trip. The old handler tested `listening` while
  //    the button painted from recognitionRunning, so in a wedged state the
  //    button looked live but tapping it did nothing -- no way to recover.
  // 2. The button and its handler must agree, so mute always round-trips.
  {
    var env2 = run(micOptions());
    env2.elements["start-call"].click();
    await flush();
    env2.advance(800);
    await flush();
    env2.elements["mic-toggle"].click();
    var muted = env2.elements["mic-toggle"].getAttribute("aria-pressed");
    env2.elements["mic-toggle"].click();
    var unmuted = env2.elements["mic-toggle"].getAttribute("aria-pressed");
    await flush();
    report("mic button can mute", muted === "false", "aria-pressed=" + muted);
    report("mic button can unmute again", unmuted === "true", "aria-pressed=" + unmuted);
  }

  // 3. A plain-http page must name the real cause, not "this browser".
  {
    var env3 = run({ secure: false });
    await flush();
    var status = env3.elements["call-status"].textContent;
    report(
      "plain-http page reports the secure-context reason",
      /secure page/i.test(status),
      JSON.stringify(status)
    );
    report("mic disabled on an insecure page", env3.elements["mic-toggle"].disabled === true);
  }

  // 4. A start that never answers must time out rather than block every retry.
  {
    var env4 = buildEnv(micOptions());
    var proto = env4.FakeRecognition.prototype;
    var realStart = proto.start;
    var realAbort = proto.abort;
    proto.start = function () {
      this._state = "starting";
      env4.trace.push("start:hung");
    };
    proto.abort = function () {
      this._state = "idle";
    };
    load(env4);
    env4.elements["start-call"].click();
    await flush();
    env4.advance(20000);
    await flush();
    var attempts = count(env4.trace, "start:hung");
    report("a hung start is retried by the watchdog", attempts > 1, "attempts=" + attempts);
    proto.start = realStart;
    proto.abort = realAbort;
  }

  // 5. Pausing must abort(), never stop(): stop() flushes the utterance as a
  //    final result and produces a duplicate answer.
  {
    var env5 = run(micOptions());
    env5.elements["start-call"].click();
    await flush();
    env5.advance(800);
    await flush();
    env5.elements["mic-toggle"].click(); // explicit mute path
    env5.advance(50);
    await flush();
    var aborts = count(env5.trace, "abort");
    var stops = count(env5.trace, "stop");
    var flushes = count(env5.trace, "onresult:final-flush");
    report("pausing uses abort(), never stop()", aborts > 0 && stops === 0, "abort=" + aborts + " stop=" + stops);
    report("no flushed phantom utterance", flushes === 0, "flushes=" + flushes);
  }

  // 6. A recognizer that never produces a single result must say so, once,
  //    instead of leaving "Listening..." as the only feedback.
  {
    var env6 = run(micOptions());
    var hints = 0;
    // Count how many times the silence message is written to the status line.
    var status = env6.elements["call-status"];
    var realText = Object.getOwnPropertyDescriptor(status, "textContent");
    Object.defineProperty(status, "textContent", {
      get: function () {
        return realText && realText.get ? realText.get.call(this) : "";
      },
      set: function (v) {
        if (/nothing is being recognised/i.test(String(v))) hints += 1;
        if (realText && realText.set) realText.set.call(this, v);
        else this._text = v;
      },
      configurable: true
    });
    env6.elements["start-call"].click();
    await flush();
    env6.advance(40000);
    await flush();
    report(
      "a silent recognizer reports that nothing is being recognised",
      hints >= 1,
      "hints=" + hints
    );
    report("the silence hint is shown only once", hints === 1, "hints=" + hints);
  }

  console.log(process.exitCode ? "\nSome checks failed." : "\nAll call-mic checks passed.");}

main().catch(function (err) {
  console.error(err);
  process.exit(1);
});
