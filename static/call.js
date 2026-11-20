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
      logout: "log out",
      placeholder: "Type instead of speaking...",
      you: "You",
      sage: "SAGE",
      voiceUnavailable: "Voice is unavailable. Set a fresh ELEVENLABS_TOKEN to hear replies.",
      voiceExpired: "The voice token has expired. Refresh ELEVENLABS_TOKEN to hear replies.",
      voiceQuota: "ElevenLabs credits are used up for this account, so replies cannot be spoken. Typing still works.",
      micDenied: "Microphone permission was denied. You can still type.",
      micUnavailable: "No microphone was found, or it is in use by another app. You can still type.",
      micLanguage: "Speech recognition does not support this language. You can still type.",
      micNetwork: "Speech recognition lost its connection. Retrying...",
      micNetworkOffline: "Speech recognition is unavailable right now (the browser could not reach its speech service). You can still type, or try Chrome again later.",
      micBraveBlocked: "This browser blocks Chrome's speech service, so voice input cannot work. Open the call page in Google Chrome or Microsoft Edge, or type instead.",
      micInsecure: "Voice input needs a secure page (https or localhost). A plain http:// address blocks the microphone. You can still type.",
      micSilent: "The microphone is on but nothing is being recognised. Check that the right microphone is selected, or type instead.",
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
      logout: "تسجيل الخروج",
      placeholder: "اكتب بدلًا من التحدث...",
      you: "أنت",
      sage: "SAGE",
      voiceUnavailable: "الصوت غير متاح. أضف ELEVENLABS_TOKEN جديدًا لسماع الردود.",
      voiceExpired: "انتهت صلاحية رمز الصوت. حدّث ELEVENLABS_TOKEN لسماع الردود.",
      voiceQuota: "انتهت أرصدة ElevenLabs لهذا الحساب، لذلك لا يمكن نطق الردود. الكتابة ما زالت تعمل.",
      micDenied: "تم رفض إذن الميكروفون. يمكنك الكتابة بدلًا من ذلك.",
      micUnavailable: "لم يتم العثور على ميكروفون أو أنه مستخدم في تطبيق آخر. يمكنك الكتابة.",
      micLanguage: "التعرف على الكلام لا يدعم هذه اللغة. يمكنك الكتابة.",
      micNetwork: "انقطع اتصال التعرف على الكلام. جارٍ إعادة المحاولة...",
      micNetworkOffline: "التعرف على الكلام غير متاح حاليًا (لم يستطع المتصفح الوصول إلى خدمة الكلام). يمكنك الكتابة، أو حاول باستخدام Chrome لاحقًا.",
      micBraveBlocked: "هذا المتصفح يحجب خدمة الكلام الخاصة بـ Chrome، لذا لا يمكن استخدام الإدخال الصوتي. افتح صفحة المكالمة في Google Chrome أو Microsoft Edge، أو اكتب بدلًا من ذلك.",
      micInsecure: "الإدخال الصوتي يحتاج صفحة آمنة (https أو localhost). العنوان http:// العادي يحجب الميكروفون. يمكنك الكتابة بدلًا من ذلك.",
      micSilent: "الميكروفون يعمل لكن لم يتم التعرف على أي كلام. تأكد من اختيار الميكروفون الصحيح، أو اكتب بدلًا من ذلك.",
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

  // Lifecycle tracing for the mic. "I spoke and nothing happened" is
  // undiagnosable from the outside: the audio never reaches our server, so the
  // only record of a failed recognizer is here. Event names, codes and counts
  // only -- never the recognised text.
  var TRACE = (function () {
    try {
      return localStorage.getItem("th_call_trace") === "1";
    } catch (err) {
      return false;
    }
  })();

  function trace(event, detail) {
    if (!TRACE) return;
    try {
      if (detail === undefined) console.info("[call] " + event);
      else console.info("[call] " + event, detail);
    } catch (err) {
      /* console unavailable */
    }
  }

  // --- elements -----------------------------------------------------------

  var greetingEl = document.getElementById("greeting");
  var avatarEl = document.getElementById("avatar");
  var logoutBtn = document.getElementById("logout");
  var logoutLabel = document.getElementById("logout-label") || logoutBtn;
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
  // The conversation this call is bound to, captured from the first stream's
  // X-Conversation-ID. Without it every spoken turn asks the server to start a
  // fresh thread (conversations.resolve_conversation creates one when the id is
  // missing), so one call left dozens of orphan threads in the database.
  var conversationId = null;
  // The server caps a legacy `messages` array at MAX_MESSAGES (50) and rejects
  // the whole request past that, which used to kill the call on ~the 26th spoke
  // turn. Trim to the most recent turns so a long call keeps working.
  var HISTORY_LIMIT = 20;

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
    micLabel.textContent = micBtn.getAttribute("aria-pressed") === "true" ? str().mute : str().unmute;    input.setAttribute("placeholder", str().placeholder);
    callBadge.textContent = str().badge;
    logoutLabel.textContent = str().logout;
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
    // No explicit state means the call is not live: let the orb breathe rather
    // than freeze, so an idle stage still looks alive.
    callOrb.classList.toggle("call-orb--idle", !orbState);
  }

  function renderVoiceBanner() {
    if (!voice.configured) {
      voiceBanner.textContent = str().voiceUnavailable;
      voiceBanner.classList.remove("hidden");
    } else if (voice.reason === "voice_quota_exceeded") {
      // The token is fine, the account has no credits left. Say so plainly.
      voiceBanner.textContent = str().voiceQuota;
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

  // Chrome requires a gap between one session ending and the next starting.
  // Calling start() straight from onend throws InvalidStateError, which used to
  // be swallowed, leaving the microphone granted but permanently idle.
  var RESTART_DELAY_MS = 300;
  // Chrome sometimes ends a continuous session without firing onend at all.
  var WATCHDOG_MS = 1500;
  // A start() that never answers onstart or onend has to time out, or the
  // "starting" flag blocks every restart attempt for the rest of the call.
  var START_TIMEOUT_MS = 8000;
  // A "network" error means Chrome could not reach its own speech service.
  // Retrying forever leaves the user staring at a spinner, so give up after a
  // few attempts and say plainly that typing is the way forward.
  var MAX_NETWORK_RETRIES = 3;

  var restartTimer = null;
  var watchdogTimer = null;
  var recognitionRunning = false;
  // True between calling recognition.start() and the session's onstart/onend.
  // Chrome's start is asynchronous, and a stop() issued in that window cancels
  // the pending session outright. Tracking it separately keeps startRecognitionNow
  // from firing a second start (which Chrome rejects) and keeps the mic button
  // honest while the handshake is in flight.
  var recognitionStarting = false;
  var recognitionStartingAt = 0;
  var recognitionFatal = false;
  var restartAttempts = 0;
  // Fires if a call has run for a while without a single result ever arriving.
  // A call that has heard anything at all is never nagged, so normal pauses
  // between sentences cannot trigger it.
  var silenceTimer = null;
  var SILENCE_HINT_MS = 10000;
  var heardAnyResult = false;
  var silenceHintShown = false;

  function clearRestartTimer() {
    if (restartTimer !== null) {
      window.clearTimeout(restartTimer);
      restartTimer = null;
    }
  }

  function clearSilenceTimer() {
    if (silenceTimer !== null) {
      window.clearTimeout(silenceTimer);
      silenceTimer = null;
    }
  }

  function armSilenceTimer() {
    clearSilenceTimer();
    if (heardAnyResult || silenceHintShown) return;
    silenceTimer = window.setTimeout(function () {
      silenceTimer = null;
      if (heardAnyResult || silenceHintShown) return;
      // Only while the call still wants the mic: a pause for playback or a
      // hang-up is not a fault and must not be reported as one.
      if (!shouldBeListening() && !recognitionRunning) return;
      silenceHintShown = true;
      trace("silent");
      showError(str().micSilent);
    }, SILENCE_HINT_MS);
  }

  function shouldBeListening() {
    // `recognitionPaused` is the playback guard now (pauseRecognition runs
    // right before audio.play()), so the fetch phase keeps the mic live and
    // the user can barge in while SAGE's next clip is still being generated.
    return active && !manualStop && !recognitionPaused && !recognitionFatal;
  }

  // One predicate for both the button's appearance and its click handler. They
  // used to disagree (the handler tested `listening`, the button painted from
  // recognitionRunning), so in a wedged state the button looked live but tap did
  // nothing -- and there was no way to un-wedge the mic.
  function micBusy() {
    return recognitionRunning || recognitionStarting || restartTimer !== null;
  }

  function updateMicButton() {
    var on = micBusy();
    micBtn.setAttribute("aria-pressed", on ? "true" : "false");
    micLabel.textContent = on ? str().mute : str().unmute;
  }

  function scheduleRestart(delay) {
    if (restartTimer !== null) return;
    if (!shouldBeListening()) return;
    restartTimer = window.setTimeout(function () {
      restartTimer = null;
      if (!shouldBeListening()) return;
      startRecognitionNow();
    }, delay == null ? RESTART_DELAY_MS : delay);
  }

  function startRecognitionNow() {
    if (!recognition || recognitionFatal || recognitionRunning || recognitionStarting) return;
    // Mark the handshake before start() so onstart cannot be mistaken for a
    // fresh session and a stop() in the same tick is not swallowed.
    recognitionStarting = true;
    recognitionStartingAt = Date.now();
    try {
      recognition.start();
      trace("start");
      // Repaint now, not on onstart: the handshake can take a moment, and the
      // button must read "Mute" as soon as the mic is being opened or a second
      // tap looks like it did nothing.
      updateMicButton();
    } catch (err) {
      recognitionStarting = false;
      recognitionStartingAt = 0;
      if (err && err.name === "InvalidStateError") {
        // Chrome refuses a start while another is still in flight. Do NOT mark
        // the session running here: doing so left recognitionRunning stuck true
        // with no live session, which early-returned startListening(), skipped
        // the watchdog, and left the mic granted but deaf for the whole call.
        trace("start.rejected", { name: err.name });
        scheduleRestart(RESTART_DELAY_MS * 2);
      } else {
        trace("start.error", { name: err && err.name });
        scheduleRestart(RESTART_DELAY_MS * 2);
      }
      updateMicButton();
    }
  }

  function fatalRecognition(message) {
    recognitionFatal = true;
    manualStop = true;
    clearRestartTimer();
    clearSilenceTimer();
    recognitionStarting = false;
    micBtn.disabled = true;
    recognitionRunning = false;
    updateMicButton();
    showError(message);
  }

  // Chrome's speech recognition talks to Google's service. Brave and many
  // Chromium builds ship without the API keys for it, which surfaces as a
  // permanent "network" error no amount of retrying can fix.
  function speechProneToNetworkFailure() {
    try {
      if (window.navigator && window.navigator.brave) return true;
      var ua = (window.navigator && window.navigator.userAgent) || "";
      return /\bBrave\b/.test(ua);
    } catch (err) {
      return false;
    }
  }

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
      recognitionRunning = true;
      recognitionStarting = false;
      recognitionStartingAt = 0;
      updateMicButton();
      trace("onstart", { wanted: shouldBeListening() });
      // A start that was already superseded (muted, hung up, or paused for
      // playback) must not stay open: the microphone would be live while SAGE
      // speaks and barge-in would cut the reply off.
      if (!shouldBeListening()) {
        try {
          recognition.abort();
        } catch (err) {
          /* already stopped */
        }
        recognitionRunning = false;
        listening = false;
        updateMicButton();
        return;
      }
      armSilenceTimer();
      if (!busy && !speechBusy) setCallStatus(str().listening, "listening");
    };

    recognition.onend = function () {
      listening = false;
      recognitionRunning = false;
      recognitionStarting = false;
      recognitionStartingAt = 0;
      clearSilenceTimer();
      updateMicButton();
      trace("onend", { restarting: shouldBeListening() });
      if (shouldBeListening()) {
        // never restart synchronously: Chrome rejects that
        scheduleRestart(RESTART_DELAY_MS);
      } else if (!active) {
        updateMicButton();
      }
    };

    recognition.onerror = function (event) {
      var code = event && event.error ? event.error : "";
      recognitionRunning = false;
      recognitionStarting = false;
      recognitionStartingAt = 0;
      clearSilenceTimer();
      updateMicButton();
      trace("onerror", { code: code });

      if (code === "not-allowed" || code === "service-not-allowed") {
        fatalRecognition(str().micDenied);
        return;
      }
      if (code === "audio-capture") {
        fatalRecognition(str().micUnavailable);
        return;
      }
      if (code === "language-not-supported") {
        fatalRecognition(str().micLanguage);
        return;
      }
      if (code === "network") {
        // Chrome's recognizer needs to reach its speech service. That service
        // is out of this app's control, so a few retries then a clear stop.
        restartAttempts += 1;
        if (restartAttempts === 1 && speechProneToNetworkFailure()) {
          // Brave / Chromium block the endpoint entirely; retrying is futile.
          fatalRecognition(str().micBraveBlocked);
          return;
        }
        if (restartAttempts > MAX_NETWORK_RETRIES) {
          fatalRecognition(str().micNetworkOffline);
          return;
        }
        showError(str().micNetwork);
        scheduleRestart(Math.min(8000, 1000 * restartAttempts));
        return;
      }
      // no-speech / aborted are routine; onend schedules the next session.
      if (code !== "no-speech" && code !== "aborted") {
        scheduleRestart(RESTART_DELAY_MS * 2);
      }
    };

    recognition.onresult = function (event) {
      var heard = "";
      for (var i = event.resultIndex; i < event.results.length; i++) {
        heard += event.results[i][0].transcript;
      }
      // A real result proves the speech service is reachable again, so the
      // network retry budget starts fresh. Resetting on onstart would defeat
      // the cap: a failing session still fires onstart before its error.
      restartAttempts = 0;
      heardAnyResult = true;
      clearSilenceTimer();
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
      trace("final", { chars: finalText.trim().length });
      if (busy) {
        pendingSpeechText.push(finalText.trim());
        return;
      }
      sendTurn(finalText.trim(), false);
    };

    recognitionAvailable = true;
  }

  // If recognition should be running but is neither started nor scheduled,
  // restart it. Covers Chrome dropping a session without firing onend.
  function startWatchdog() {
    if (watchdogTimer !== null) return;
    watchdogTimer = window.setInterval(function () {
      // A start() that never produced onstart or onend (Chrome can do this when
      // the permission prompt is dismissed) would otherwise block every restart
      // forever, because micBusy() keeps reporting "starting".
      if (recognitionStarting && Date.now() - recognitionStartingAt > START_TIMEOUT_MS) {
        trace("start.timeout", { waited_ms: Date.now() - recognitionStartingAt });
        recognitionStarting = false;
        recognitionStartingAt = 0;
        updateMicButton();
      }
      if (!shouldBeListening()) return;
      if (micBusy()) return;
      scheduleRestart(0);
    }, WATCHDOG_MS);
  }

  function startListening() {
    if (!recognitionAvailable || !recognition) return;
    manualStop = false;
    recognitionPaused = false;
    if (recognitionFatal) {
      // an explicit tap is the user's retry: clear the fatal state
      recognitionFatal = false;
      micBtn.disabled = false;
    }
    if (micBusy()) {
      updateMicButton();
      return;
    }
    clearRestartTimer();
    startRecognitionNow();
  }

  function stopListening() {
    manualStop = true;
    clearRestartTimer();
    clearSilenceTimer();
    if (recognition) {
      try {
        // abort() throws away the in-flight utterance. stop() would flush it as
        // a final result, which arrives after the turn already finished and
        // shows up as a second, duplicate answer.
        recognition.abort();
      } catch (err) {
        /* already stopped */
      }
    }
    listening = false;
    recognitionRunning = false;
    recognitionStarting = false;
    updateMicButton();
  }

  // The microphone can otherwise hear the assistant's own audio and cancel
  // playback through the barge-in handler. Pause it while a clip is playing.
  function pauseRecognition() {
    if (!recognition) return;
    recognitionPaused = true;
    clearRestartTimer();
    clearSilenceTimer();
    if (recognitionRunning || recognitionStarting) {
      try {
        // abort(), not stop(): a stop() flushes the current utterance as a
        // final result right as the assistant starts speaking, so the words the
        // user just said came back as a phantom duplicate turn.
        recognition.abort();
      } catch (err) {
        /* already stopped */
      }
    }
    recognitionRunning = false;
    recognitionStarting = false;
    updateMicButton();
  }

  function resumeRecognition() {
    recognitionPaused = false;
    if (!shouldBeListening()) return;
    if (micBusy()) return;
    scheduleRestart(RESTART_DELAY_MS);
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
      // Only the most recent turns: the server rejects a legacy `messages`
      // array past MAX_MESSAGES (50) with a 400, which used to end the call
      // permanently around the 26th spoken turn.
      messages: history.slice(-HISTORY_LIMIT).map(function (m) {
        return { role: m.role, content: m.content };
      }),
      language: lang,
      mode: "call"
    };
    // Reuse the thread the first turn landed in. Without this the server
    // started a fresh conversation on every turn (resolve_conversation creates
    // one when the id is missing), leaving dozens of orphan threads that later
    // surfaced as the chat page's "current" thread.
    if (conversationId) body.conversation_id = conversationId;
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
        if (!conversationId) conversationId = response.headers.get("X-Conversation-ID");
        trace("stream.open", { status: response.status, hasThread: !!conversationId });
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
        // Pause the mic only now, when sound is actually about to come out of
        // the speaker. Pausing at the top of pumpSpeech muted it for the whole
        // ElevenLabs round-trip, so speaking right after "Start call" fell into
        // a dead microphone.
        pauseRecognition();
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

  // A page without a secure context cannot use the microphone at all, and
  // startCall would otherwise re-enable the button the boot check disabled.
  function microphoneUsable() {
    return recognitionAvailable && window.isSecureContext;
  }

  // Ask the browser for the microphone inside the Start-call gesture, then
  // begin recognition once that settles. Starting recognition before the
  // greeting raced its first clip: the pause for that audio aborted a start
  // that was still pending, and Chrome then rejected every later start with
  // InvalidStateError -- the "mic is on but nothing happens" failure.
  function primeMicrophone() {
    if (!microphoneUsable()) {
      updateMicButton();
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      // Older browsers still expose SpeechRecognition without getUserMedia.
      if (shouldBeListening()) startListening();
      return;
    }
    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then(function (stream) {
        // Release our handle; SpeechRecognition opens its own capture.
        stream.getTracks().forEach(function (track) {
          try {
            track.stop();
          } catch (err) {
            /* already stopped */
          }
        });
      })
      .catch(function (err) {
        // Denied or no device: let SpeechRecognition report the specific code
        // (not-allowed / audio-capture) rather than guessing here.
        trace("mic.permission.failed", { name: err && err.name });
      })
      .then(function () {
        // If SAGE is already speaking, stay paused; finishSpeech() resumes us.
        if (active && !recognitionFatal && shouldBeListening()) startListening();
      });
  }

  function startCall(firstText) {
    if (active) return;
    unlockAudio();
    active = true;
    history = [];
    conversationId = null;
    heardAnyResult = false;
    silenceHintShown = false;
    transcript.innerHTML = "";
    startBtn.classList.add("hidden");
    hangupBtn.classList.remove("hidden");
    micBtn.classList.remove("hidden");
    micBtn.disabled = !microphoneUsable();
    setCallStatus(str().connecting, "listening");
    startWatchdog();
    primeMicrophone();
    if (firstText) sendTurn(firstText, false);
    else sendTurn(null, true);
  }

  function stopCall() {
    active = false;
    manualStop = true;
    setBusy(false);
    stopSpeech();
    stopListening();
    clearRestartTimer();
    if (watchdogTimer !== null) {
      window.clearInterval(watchdogTimer);
      watchdogTimer = null;
    }
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
    conversationId = null;
    pendingSpeechText = [];
    heardAnyResult = false;
    silenceHintShown = false;
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
    if (!microphoneUsable()) return;
    if (micBusy()) {
      stopListening();
      setCallStatus(str().muted, null);
    } else {
      stopSpeech(); // tapping the mic while SAGE speaks interrupts the audio
      startListening();
      setCallStatus(str().listening, "listening");
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
  // Report the most specific reason available: a plain-HTTP page blocks the
  // microphone in every browser, and saying "not available in this browser"
  // there sends people hunting for a browser problem that does not exist.
  if (!window.isSecureContext) {
    micBtn.disabled = true;
    showError(str().micInsecure);
    trace("insecure-context");
  } else if (!recognitionAvailable) {
    showError(str().noSpeech);
  }

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
