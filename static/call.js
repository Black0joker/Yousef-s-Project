/* Voice call with SAGE.
   Microphone input (where supported) -> /api/chat/stream in call mode.
   Assistant text -> /api/voice/speak, queued and played sentence by sentence. */
(function () {
  "use strict";

  var LANG_KEY = "th_language";
  var LANG_ALIASES = { en: "en", eng: "en", english: "en", ar: "ar", ara: "ar", arabic: "ar" };

  var STRINGS = {
    en: {
      code: "EN",
      badge: "Voice call",
      title: "Voice call with SAGE",
      subtitle: "Speak naturally, or type if you prefer.",
      start: "Start call",
      hangup: "Hang up",
      mute: "Mute",
      unmute: "Unmute",
      ready: "Ready when you are.",
      connecting: "Connecting...",
      listening: "Listening...",
      thinking: "Thinking...",
      speaking: "Speaking...",
      muted: "Microphone muted.",
      ended: "Call ended.",
      placeholder: "Type instead of speaking...",
      you: "You",
      sage: "SAGE",
      voiceUnavailable: "Voice is unavailable. Set a fresh ELEVENLABS_TOKEN to hear replies.",
      voiceExpired: "The voice token has expired. Refresh ELEVENLABS_TOKEN to hear replies.",
      micDenied: "Microphone permission was denied. You can still type.",
      noSpeech: "Speech recognition is not available in this browser. You can still type.",
      audioBlocked: "The browser blocked audio. Tap Start call once, then try again.",
      error: "Something went wrong.",
      requestFailed: "Request failed"
    },
    ar: {
      code: "AR",
      badge: "مكالمة صوتية",
      title: "مكالمة صوتية مع SAGE",
      subtitle: "تحدّث بشكل طبيعي، أو اكتب إن كنت تفضّل ذلك.",
      start: "بدء المكالمة",
      hangup: "إنهاء المكالمة",
      mute: "كتم",
      unmute: "إلغاء الكتم",
      ready: "جاهز عندما تريد.",
      connecting: "جارٍ الاتصال...",
      listening: "أستمع...",
      thinking: "أفكر...",
      speaking: "أتحدث...",
      muted: "الميكروفون مكتوم.",
      ended: "انتهت المكالمة.",
      placeholder: "اكتب بدلًا من التحدث...",
      you: "أنت",
      sage: "SAGE",
      voiceUnavailable: "الصوت غير متاح. أضف ELEVENLABS_TOKEN جديدًا لسماع الردود.",
      voiceExpired: "انتهت صلاحية رمز الصوت. حدّث ELEVENLABS_TOKEN لسماع الردود.",
      micDenied: "تم رفض إذن الميكروفون. يمكنك الكتابة بدلًا من ذلك.",
      noSpeech: "التعرف على الكلام غير متاح في هذا المتصفح. يمكنك الكتابة.",
      audioBlocked: "منع المتصفح تشغيل الصوت. اضغط «بدء المكالمة» مرة ثم حاول مجددًا.",
      error: "حدث خطأ ما.",
      requestFailed: "فشل الطلب"
    }
  };

  function normalizeLang(value) {
    return LANG_ALIASES[String(value || "").trim().toLowerCase()] || "en";
  }

  var lang = (function () {
    try {
      return normalizeLang(localStorage.getItem(LANG_KEY));
    } catch (err) {
      return "en";
    }
  })();

  function str() {
    return STRINGS[lang] || STRINGS.en;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function errorText(payload, status) {
    var err = payload && payload.error;
    if (err && err.message) return err.code ? err.code + ": " + err.message : err.message;
    return str().requestFailed + " (" + status + ")";
  }

  function readJsonSafe(response) {
    return response.json().catch(function () {
      return null;
    });
  }

  // --- elements -----------------------------------------------------------

  var greetingEl = document.getElementById("greeting");
  var avatarEl = document.getElementById("avatar");
  var logoutBtn = document.getElementById("logout");
  var langToggle = document.getElementById("lang-toggle");
  var langLabel = document.getElementById("lang-label");
  var startBtn = document.getElementById("start-call");
  var startLabel = document.getElementById("start-label");
  var hangupBtn = document.getElementById("hangup");
  var hangupLabel = document.getElementById("hangup-label");
  var micBtn = document.getElementById("mic-toggle");
  var micLabel = document.getElementById("mic-label");
  var callTitle = document.getElementById("call-title");
  var callSubtitle = document.getElementById("call-subtitle");
  var callStatus = document.getElementById("call-status");
  var callOrb = document.getElementById("call-orb");
  var voiceBanner = document.getElementById("voice-banner");
  var transcript = document.getElementById("transcript");
  var form = document.getElementById("composer");
  var input = document.getElementById("input");
  var sendBtn = document.getElementById("send");
  var statusEl = document.getElementById("status");
  var statusDot = document.getElementById("status-dot");
  var callBadge = document.getElementById("call-badge");
  var backToChat = document.getElementById("back-to-chat");

  // --- state --------------------------------------------------------------

  var active = false;
  var busy = false;
  var listening = false;
  var manualStop = false;
  var voice = { available: false, expired: false, configured: false };
  var voiceReady = Promise.resolve();
  var recognitionPaused = false;
  var audioUnlocked = false;

  // A short silent WAV. Playing it inside the Start-call gesture unlocks later
  // programmatic playback of the real voice clips in stricter browsers.
  var SILENT_WAV =
    "data:audio/wav;base64,UklGRkQDAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YSADAAAA" +
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" +
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" +
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" +
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" +
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" +
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" +
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" +
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" +
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" +
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" +
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" +
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" +
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" +
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" +
    "AAAAAA==";

  function unlockAudio() {
    if (audioUnlocked) return;
    try {
      var warmup = new Audio(SILENT_WAV);
      var promise = warmup.play();
      if (promise && promise.then) {
        promise
          .then(function () {
            audioUnlocked = true;
          })
          .catch(function () {
            /* the real play() will surface a clearer error */
          });
      } else {
        audioUnlocked = true;
      }
    } catch (err) {
      /* browsers without Audio still fail later with a visible message */
    }
  }
  var history = []; // {role, content} for this call only; never persisted
  var streamAbort = null;
  var streamId = null;
  var pendingSpeechText = [];
  var speechQueue = [];
  var speechBusy = false;
  var speechGeneration = 0;
  var currentAudio = null;
  var recognition = null;
  var recognitionAvailable = false;
  var currentAssistantRow = null;

  function applyLanguage() {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
    document.body.classList.toggle("lang-ar", lang === "ar");
    callTitle.textContent = str().title;
    callSubtitle.textContent = str().subtitle;
    startLabel.textContent = str().start;
    hangupLabel.textContent = str().hangup;
    micLabel.textContent = micBtn.getAttribute("aria-pressed") === "true" ? str().mute : str().unmute;
    input.setAttribute("placeholder", str().placeholder);
    callBadge.textContent = str().badge;
    if (langLabel) langLabel.textContent = str().code;
    if (!active) callStatus.textContent = str().ready;
    renderVoiceBanner();
  }

  function setBusy(value) {
    busy = value;
    sendBtn.disabled = value;
    if (value) {
      statusEl.textContent = str().thinking;
      statusDot.style.display = "inline-block";
    } else {
      statusEl.textContent = "";
      statusDot.style.display = "none";
    }
  }

  function setCallStatus(text, orbState) {
    callStatus.textContent = text || "";
    callOrb.classList.remove("call-orb--listening", "call-orb--speaking");
    if (orbState) callOrb.classList.add("call-orb--" + orbState);
  }

  function renderVoiceBanner() {
    if (!voice.configured) {
      voiceBanner.textContent = str().voiceUnavailable;
      voiceBanner.classList.remove("hidden");
    } else if (voice.expired) {
      voiceBanner.textContent = str().voiceExpired;
      voiceBanner.classList.remove("hidden");
    } else {
      voiceBanner.textContent = "";
      voiceBanner.classList.add("hidden");
    }
  }

  function addTurn(role, text) {
    var row = el("div", "call-turn call-turn--" + role);
    row.appendChild(el("span", "call-speaker", role === "user" ? str().you : str().sage));
    var body = el("div", "call-text" + (text ? "" : " call-text--pending"), text || "");
    row.appendChild(body);
    transcript.appendChild(row);
    transcript.scrollTop = transcript.scrollHeight;
    return body;
  }

  function showError(message) {
    setCallStatus(message || str().error, null);
  }

  // --- speech recognition -------------------------------------------------

  var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  function setupRecognition() {
    if (!SpeechRecognition) {
      recognitionAvailable = false;
      micBtn.disabled = true;
      return;
    }
    recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = lang === "ar" ? "ar-EG" : "en-US";

    recognition.onstart = function () {
      listening = true;
      micBtn.setAttribute("aria-pressed", "true");
      micLabel.textContent = str().mute;
      if (!busy && !speechBusy) setCallStatus(str().listening, "listening");
    };

    recognition.onend = function () {
      listening = false;
      if (active && !manualStop && !recognitionPaused && !speechBusy) {
        try {
          recognition.start();
        } catch (err) {
          /* already restarting */
        }
      } else if (!active) {
        micBtn.setAttribute("aria-pressed", "false");
        micLabel.textContent = str().unmute;
      }
    };

    recognition.onerror = function (event) {
      if (event && event.error === "not-allowed") {
        recognitionAvailable = false;
        manualStop = true;
        micBtn.disabled = true;
        showError(str().micDenied);
      }
    };

    recognition.onresult = function (event) {
      var heard = "";
      for (var i = event.resultIndex; i < event.results.length; i++) {
        heard += event.results[i][0].transcript;
      }
      // barge-in: the user started talking, so stop the assistant's audio
      if (heard.trim() && (currentAudio || speechBusy)) {
        stopSpeech();
      }

      var finalText = "";
      for (var j = event.resultIndex; j < event.results.length; j++) {
        if (event.results[j].isFinal) {
          finalText += event.results[j][0].transcript;
        }
      }
      if (!finalText.trim()) return;
      if (busy) {
        pendingSpeechText.push(finalText.trim());
        return;
      }
      sendTurn(finalText.trim(), false);
    };

    recognitionAvailable = true;
  }

  function startListening() {
    if (!recognitionAvailable || !recognition) return;
    manualStop = false;
    try {
      recognition.start();
    } catch (err) {
      /* already running */
    }
  }

  function stopListening() {
    manualStop = true;
    if (recognition) {
      try {
        recognition.stop();
      } catch (err) {
        /* already stopped */
      }
    }
    listening = false;
  }

  // The microphone can otherwise hear the assistant's own audio and cancel
  // playback through the barge-in handler. Pause it while a clip is playing.
  function pauseRecognition() {
    if (!recognition) return;
    recognitionPaused = true;
    if (listening) {
      try {
        recognition.stop();
      } catch (err) {
        /* already stopped */
      }
    }
  }

  function resumeRecognition() {
    recognitionPaused = false;
    if (active && !manualStop && recognitionAvailable && !listening && !speechBusy) {
      try {
        recognition.start();
      } catch (err) {
        /* already running */
      }
    }
  }

  // --- streaming ----------------------------------------------------------

  function consume(body, onEvent) {
    var reader = body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";

    function pump() {
      return reader.read().then(function (result) {
        if (result.done) {
          // Release the SSE HTTP connection promptly so the follow-up
          // /api/voice/speak POST opens a fresh connection instead of reusing
          // one the server is closing.
          try {
            reader.releaseLock();
          } catch (err) {
            /* already released */
          }
          return;
        }
        buffer += decoder.decode(result.value, { stream: true });
        var parts = buffer.split("\n\n");
        buffer = parts.pop();
        for (var i = 0; i < parts.length; i++) {
          var line = parts[i].trim();
          if (line.indexOf("data:") !== 0) continue;
          var data = line.slice(5).trim();
          if (!data || data === "[DONE]") continue;
          var event;
          try {
            event = JSON.parse(data);
          } catch (err) {
            continue;
          }
          onEvent(event);
        }
        return pump();
      });
    }

    return pump();
  }

  function sendTurn(text, greeting) {
    if (!active || busy) return;
    setBusy(true);
    setCallStatus(str().thinking, null);

    if (text) {
      history.push({ role: "user", content: text });
      addTurn("user", text);
    }

    var answer = "";
    var speechSegment = "";
    var assistantBody = addTurn("assistant", "");
    var controller = new AbortController();
    streamAbort = controller;

    var body = {
      messages: history.map(function (m) {
        return { role: m.role, content: m.content };
      }),
      language: lang,
      mode: "call"
    };
    if (greeting) body.greeting = true;

    // Speak whole text segments, not sentences. A tool call is a natural
    // boundary, so the text before it is sent while the tool runs and the text
    // after it is sent when the reply completes.
    function flushSpeechSegment() {
      if (!active) return;
      var text = speechSegment.trim();
      speechSegment = "";
      if (text) enqueueSpeech(text);
    }

    Session.authFetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal
    })
      .then(function (response) {
        streamId = response.headers.get("X-Stream-ID");
        if (!response.ok || !response.body) {
          return readJsonSafe(response).then(function (payload) {
            throw new Error(errorText(payload, response.status));
          });
        }
        return consume(response.body, function (event) {
          if (event.delta) {
            answer += event.delta;
            speechSegment += event.delta;
            assistantBody.textContent = answer;
            assistantBody.classList.remove("call-text--pending");
            transcript.scrollTop = transcript.scrollHeight;
          } else if (event.tool) {
            if (event.tool.status === "start") {
              flushSpeechSegment();
            }
            setCallStatus(event.tool.name === "recall_memory" ? str().thinking : str().thinking, null);
          } else if (event.error) {
            showError(errorText({ error: event.error }, 0));
          }
        });
      })
      .then(function () {
        // Speech segments are flushed at tool boundaries, so only the tail
        // after the last tool call is still pending here.
        if (answer.trim()) {
          history.push({ role: "assistant", content: answer });
          // Defer the tail segment so its POST opens a fresh connection after
          // the SSE response closes. Tool-boundary segments are unaffected.
          window.setTimeout(flushSpeechSegment, 50);
        } else if (assistantBody && assistantBody.parentNode) {
          assistantBody.parentNode.parentNode.removeChild(assistantBody.parentNode);
        }
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        showError(err && err.message ? err.message : str().error);
      })
      .then(function () {
        streamAbort = null;
        streamId = null;
        setBusy(false);
        if (active && !speechBusy && !speechQueue.length) {
          setCallStatus(listening ? str().listening : str().ready, listening ? "listening" : null);
        }
        if (pendingSpeechText.length && !busy) {
          var next = pendingSpeechText.shift();
          sendTurn(next, false);
        }
      });
  }

  // --- text to speech -----------------------------------------------------

  function fetchAudio(text) {
    var controller = new AbortController();
    var timer = setTimeout(function () {
      controller.abort();
    }, 30000);
    return Session.authFetch("/api/voice/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, language: lang }),
      signal: controller.signal
    })
      .then(function (response) {
        if (!response.ok) {
          return readJsonSafe(response).then(function (payload) {
            throw new Error(errorText(payload, response.status));
          });
        }
        return response.blob();
      })
      .finally(function () {
        clearTimeout(timer);
      });
  }

  function finishSpeech(url) {
    if (url) URL.revokeObjectURL(url);
    currentAudio = null;
    speechBusy = false;
    if (!speechQueue.length) {
      resumeRecognition();
      if (active) {
        setCallStatus(listening ? str().listening : str().ready, listening ? "listening" : null);
      }
    }
    pumpSpeech();
  }

  function pumpSpeech() {
    if (speechBusy || !speechQueue.length) return;
    var item = speechQueue.shift();
    speechBusy = true;
    var generation = speechGeneration;
    pauseRecognition();
    setCallStatus(str().speaking, "speaking");

    item.promise
      .then(function (blob) {
        if (generation !== speechGeneration) {
          speechBusy = false;
          return;
        }
        var url = URL.createObjectURL(blob);
        var audio = new Audio(url);
        currentAudio = audio;
        var finished = false;
        function done() {
          if (finished) return;
          finished = true;
          finishSpeech(url);
        }
        audio.onended = done;
        audio.onerror = done;
        var playback = audio.play();
        if (playback && playback.catch) {
          playback.catch(function () {
            showError(str().audioBlocked);
            done();
          });
        }
      })
      .catch(function (err) {
        if (err && err.message && !/abort/i.test(err.name || "")) {
          showError(err.message);
        }
        speechBusy = false;
        if (!speechQueue.length) resumeRecognition();
        pumpSpeech();
      });
  }

  function enqueueSpeech(text) {
    var cleaned = String(text || "").trim();
    if (!cleaned) return;
    var promise = voiceReady.then(function () {
      if (!voice.available) throw new Error("voice unavailable");
      return fetchAudio(cleaned);
    });
    speechQueue.push({ text: cleaned, promise: promise });
    pumpSpeech();
  }

  function stopSpeech() {
    speechGeneration += 1;
    speechQueue = [];
    if (currentAudio) {
      var audio = currentAudio;
      currentAudio = null;
      audio.onended = null;
      audio.onerror = null;
      try {
        audio.pause();
        audio.currentTime = 0;
      } catch (err) {
        /* already stopped */
      }
    }
    speechBusy = false;
    resumeRecognition();
  }

  // --- call lifecycle -----------------------------------------------------

  function startCall(firstText) {
    if (active) return;
    unlockAudio();
    active = true;
    history = [];
    transcript.innerHTML = "";
    startBtn.classList.add("hidden");
    hangupBtn.classList.remove("hidden");
    micBtn.classList.remove("hidden");
    micBtn.setAttribute("aria-pressed", recognitionAvailable ? "true" : "false");
    micLabel.textContent = recognitionAvailable ? str().mute : str().unmute;
    setCallStatus(str().connecting, "listening");
    if (recognitionAvailable) startListening();
    if (firstText) sendTurn(firstText, false);
    else sendTurn(null, true);
  }

  function stopCall() {
    active = false;
    manualStop = true;
    setBusy(false);
    stopSpeech();
    stopListening();
    if (streamId) {
      Session.authFetch("/api/chat/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: streamId })
      }).catch(function () {
        /* best effort */
      });
    }
    if (streamAbort) streamAbort.abort();
    streamAbort = null;
    streamId = null;
    pendingSpeechText = [];
    startBtn.classList.remove("hidden");
    hangupBtn.classList.add("hidden");
    micBtn.classList.add("hidden");
    setCallStatus(str().ended, null);
  }

  // --- events -------------------------------------------------------------

  startBtn.addEventListener("click", function () {
    startCall();
  });
  hangupBtn.addEventListener("click", stopCall);

  micBtn.addEventListener("click", function () {
    if (!recognitionAvailable) return;
    if (listening) {
      stopListening();
      micBtn.setAttribute("aria-pressed", "false");
      micLabel.textContent = str().unmute;
      setCallStatus(str().muted, null);
    } else {
      stopSpeech(); // tapping the mic while SAGE speaks interrupts the audio
      startListening();
      micBtn.setAttribute("aria-pressed", "true");
      micLabel.textContent = str().mute;
    }
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = input.value.trim();
    if (!text || busy) return;
    input.value = "";
    unlockAudio();
    if (!active) startCall(text);
    else sendTurn(text, false);
  });

  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.dispatchEvent(new Event("submit", { cancelable: true }));
    }
  });

  logoutBtn.addEventListener("click", function () {
    if (active) stopCall();
    Session.clear();
    window.location.replace("/login");
  });

  langToggle.addEventListener("click", function () {
    lang = lang === "ar" ? "en" : "ar";
    try {
      localStorage.setItem(LANG_KEY, lang);
    } catch (err) {
      /* storage blocked */
    }
    if (recognition) recognition.lang = lang === "ar" ? "ar-EG" : "en-US";
    applyLanguage();
  });

  if (backToChat) {
    backToChat.setAttribute("href", "/chat");
  }

  // --- boot ---------------------------------------------------------------

  if (!Session.requireSession()) return;
  applyLanguage();
  setupRecognition();
  if (!recognitionAvailable) showError(str().noSpeech);

  Session.authFetch("/api/auth/me")
    .then(function (response) {
      return response.ok ? response.json() : null;
    })
    .then(function (data) {
      if (!data || !data.user) return;
      Session.save({ user: data.user });
      var name = (data.user.first_name || "").trim();
      if (!name) return;
      greetingEl.textContent = name;
      avatarEl.textContent = name.charAt(0).toUpperCase();
    })
    .catch(function () {
      /* the session guard already handled invalid tokens */
    });

  voiceReady = Session.authFetch("/api/voice/status")
    .then(function (response) {
      return response.ok ? response.json() : null;
    })
    .then(function (data) {
      if (data) {
        voice = data;
        renderVoiceBanner();
      }
      return data;
    })
    .catch(function () {
      /* leave voice disabled; text still works */
      return null;
    });
})();
