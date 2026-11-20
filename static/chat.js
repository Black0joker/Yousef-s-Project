/* Chat page: authenticated streaming chat. The bearer token travels in the
   Authorization header on every request; history lives in localStorage per user. */
(function () {
  "use strict";

  var Session = window.Session;
  if (!Session || !Session.requireSession()) return; // no valid token -> /login

  var user = Session.getUser() || {};
  var fullName = (user.full_name || "").trim();
  var firstName = (user.first_name || "").trim() || fullName.split(" ")[0] || "there";

  // --- language ------------------------------------------------------------

  var LANG_KEY = "th_language";
  var LANG_ALIASES = { en: "en", eng: "en", english: "en", ar: "ar", ara: "ar", arabic: "ar" };

  // UI strings per language; the system prompt on the server follows the same code.
  var STRINGS = {
    en: {
      code: "EN",
      greeting: function (name) { return "Hi, " + name; },
      welcome: function (name) { return "Welcome, " + name + "! Ask me anything to get started."; },
      logout: "log out",
      clear: "Clear",
      placeholder: "Type a message...  (Enter to send, Shift+Enter for a new line)",
      placeholderShort: "Type a message…",
      thinking: "Thinking…",
      noResponse: "(no response)",
      stop: "Stop",
      toggleLang: "Switch language",
      deleteReply: "Delete this message and its reply",
      deleteOne: "Delete this message",
      toolSaving: "Saving memory...",
      toolSearching: "Searching memory...",
      toolSaved: "Memory saved",
      toolFound: "Memory searched",
      toolFailed: "Memory tool failed",
      toolGeneric: "Using tool...",
      toolAccount: "Checking your account...",
      toolAccountDone: "Account checked",
      loadFailed: "Could not load your conversation.",
      deleteFailed: "Could not delete that message.",
      clearFailed: "Could not clear the conversation.",
      jumpLatest: "Latest",
      clearTitle: "Clear this conversation?",
      clearBody: "Every message in this thread will be deleted. What SAGE remembers stays.",
      clearConfirm: "Clear",
      cancel: "Cancel",
      suggest1: "What's on today?",
      suggest2: "Remember that...",
      suggest3: "What do you remember about me?",
    },
    ar: {
      code: "AR",
      greeting: function (name) { return "مرحبًا، " + name; },
      welcome: function (name) { return "أهلًا بك يا " + name + "! اسألني أي شيء للبدء."; },
      logout: "تسجيل الخروج",
      clear: "مسح",
      placeholder: "اكتب رسالة...  (Enter للإرسال، Shift+Enter لسطر جديد)",
      placeholderShort: "اكتب رسالة…",
      thinking: "جارٍ التفكير...",
      noResponse: "(لا يوجد رد)",
      stop: "إيقاف",
      toggleLang: "تغيير اللغة",
      deleteReply: "احذف هذه الرسالة وردها",
      deleteOne: "احذف هذه الرسالة",
      toolSaving: "جارٍ حفظ الذاكرة...",
      toolSearching: "جارٍ البحث في الذاكرة...",
      toolSaved: "تم حفظ الذاكرة",
      toolFound: "تم البحث في الذاكرة",
      toolFailed: "تعذر استخدام أداة الذاكرة",
      toolGeneric: "جارٍ استخدام الأداة...",
      toolAccount: "جارٍ التحقق من حسابك...",
      toolAccountDone: "تم التحقق من الحساب",
      loadFailed: "تعذر تحميل المحادثة.",
      deleteFailed: "تعذر حذف هذه الرسالة.",
      clearFailed: "تعذر مسح المحادثة.",
      jumpLatest: "الأحدث",
      clearTitle: "مسح المحادثة؟",
      clearBody: "ستُحذف كل رسالة في هذه المحادثة. أما ما يتذكره SAGE فيبقى.",
      clearConfirm: "مسح",
      cancel: "إلغاء",
      suggest1: "ما عندي اليوم؟",
      suggest2: "تذكّر أن...",
      suggest3: "ماذا تتذكر عني؟",
    },
  };

  function normalizeLang(value) {
    return LANG_ALIASES[String(value || "").trim().toLowerCase()] || "en";
  }

  // the in-flight turn: AbortController + the server's X-Stream-ID
  var activeTurn = null;

  function loadLanguage() {
    try {
      return normalizeLang(localStorage.getItem(LANG_KEY));
    } catch (err) {
      return "en";
    }
  }

  function saveLanguage() {
    try {
      localStorage.setItem(LANG_KEY, lang);
    } catch (err) {
      /* storage blocked; the choice lasts for this session */
    }
  }

  function str() {
    return STRINGS[lang] || STRINGS.en;
  }

  function toolLabel(name) {
    if (name === "save_memory") return str().toolSaving;
    if (name === "recall_memory") return str().toolSearching;
    if (name === "get_name") return str().toolAccount;
    return str().toolGeneric;
  }

  function toolDoneLabel(name, ok) {
    if (!ok) return str().toolFailed;
    if (name === "save_memory") return str().toolSaved;
    if (name === "recall_memory") return str().toolFound;
    if (name === "get_name") return str().toolAccountDone;
    return str().toolGeneric;
  }

  var lang = loadLanguage();

  var TRASH_ICON =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="14" height="14" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M3 6h18"></path><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"></path>' +
    '<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path>' +
    '<path d="M10 11v6M14 11v6"></path></svg>';

  var messagesEl = document.getElementById("messages");
  var welcomeRow = document.getElementById("welcome-row");
  var welcomeBubble = document.getElementById("welcome-bubble");
  var greetingEl = document.getElementById("greeting");
  var avatarEl = document.getElementById("avatar");
  var form = document.getElementById("composer");
  var input = document.getElementById("input");
  var sendBtn = document.getElementById("send");
  var stopBtn = document.getElementById("stop");
  var clearBtn = document.getElementById("clear");
  var statusEl = document.getElementById("status");
  var statusDot = document.getElementById("status-dot");
  var logoutBtn = document.getElementById("logout");
  var logoutLabel = document.getElementById("logout-label") || logoutBtn;
  var clearLabel = document.getElementById("clear-label");
  var langToggle = document.getElementById("lang-toggle");
  var langLabel = document.getElementById("lang-label");
  var jumpBtn = document.getElementById("jump-latest");
  var jumpLabel = document.getElementById("jump-latest-label");
  var suggestionsEl = document.getElementById("chat-suggestions");
  var suggestionButtons = suggestionsEl
    ? Array.prototype.slice.call(suggestionsEl.querySelectorAll("[data-suggest]"))
    : [];

  applyLanguage();

  var history = [];
  var conversationId = null;

  // The database is the source of truth: the thread is rendered from what the
  // server holds, and the composer only ever sends the new message.
  function renderThread(animate) {
    Array.prototype.slice.call(messagesEl.children).forEach(function (child) {
      if (child !== welcomeRow && child !== suggestionsEl) child.remove();
    });
    history.forEach(function (entry, index) {
      if (entry.role !== "user") {
        addAssistantMessage(entry.content, entry.id, animate);
        return;
      }
      var next = history[index + 1];
      addUserMessage(entry.content, entry.id, !!next && next.role === "assistant", animate);
    });
    if (welcomeRow) welcomeRow.classList.toggle("hidden", history.length > 0);
    showSuggestions(history.length === 0);
    scrollToBottom();
  }

  function loadThread(animate) {
    return Session.authFetch("/api/conversations/current")
      .then(function (response) {
        if (!response.ok) throw new Error(str().loadFailed);
        return response.json();
      })
      .then(function (data) {
        conversationId = data.conversation.id;
        history = (data.turns || [])
          .filter(function (turn) {
            // an errored turn can carry empty assistant text; skip the bubble
            return !(turn.role === "assistant" && !String(turn.content || "").trim());
          })
          .map(function (turn) {
            return { id: turn.id, role: turn.role, content: turn.content };
          });
        renderThread(animate);
      })
      .catch(function () {
        /* stay usable for typing even if the initial load failed */
      });
  }

  // confirm the session server-side and refresh the profile from the token claims
  Session.authFetch("/api/auth/me")
    .then(function (response) {
      return response.ok ? response.json() : null;
    })
    .then(function (data) {
      if (!data || !data.user) return;
      Session.save({ user: data.user });
      var name = (data.user.first_name || "").trim();
      if (!name) return;
      greetingEl.textContent = str().greeting(name);
      avatarEl.textContent = name.charAt(0).toUpperCase();
    })
    .catch(function () {
      /* the session guard already handled expired/invalid tokens */
    });

  // The transcript lives in the database now: render whatever it holds.
  loadThread(true);

  // --- helpers ------------------------------------------------------------

  // applies the selected language to the static UI and the document direction
  function applyLanguage() {
    var strings = str();

    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
    document.body.classList.toggle("lang-ar", lang === "ar");

    greetingEl.textContent = strings.greeting(firstName);
    avatarEl.textContent = firstName.charAt(0).toUpperCase();
    welcomeBubble.textContent = strings.welcome(firstName);
    logoutLabel.textContent = strings.logout;
    if (clearLabel) clearLabel.textContent = strings.clear;
    if (jumpLabel) jumpLabel.textContent = strings.jumpLatest;
    suggestionButtons.forEach(function (button) {
      var key = "suggest" + button.getAttribute("data-suggest");
      if (strings[key]) button.textContent = strings[key];
    });
    input.setAttribute("placeholder", strings.placeholderShort || strings.placeholder);
    input.title = strings.placeholder;
    if (langLabel) langLabel.textContent = strings.code;
    if (langToggle) {
      langToggle.setAttribute("aria-label", strings.toggleLang);
      langToggle.setAttribute("title", strings.toggleLang);
    }
    if (stopBtn) {
      stopBtn.setAttribute("aria-label", strings.stop);
      stopBtn.title = strings.stop;
    }
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function newId() {
    return "m" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  }

  function now() {
    try {
      return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (err) {
      return "";
    }
  }

  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
    syncJump();
  }

  // A streamed token must not yank the view back down while the user is reading
  // earlier messages; only follow the reply when they are already at the end.
  function followIfNearBottom() {
    var distance = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight;
    if (distance < 120) {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }
    syncJump();
  }

  function syncJump() {
    if (!jumpBtn) return;
    var distance = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight;
    var show = distance > 160;
    jumpBtn.classList.toggle("is-visible", show);
    jumpBtn.setAttribute("aria-hidden", show ? "false" : "true");
    jumpBtn.tabIndex = show ? 0 : -1;
  }

  function showSuggestions(show) {
    if (!suggestionsEl) return;
    suggestionsEl.classList.toggle("hidden", !show);
  }

  // --- message rendering --------------------------------------------------

  // Give an optimistic bubble the id the server stored, so its delete button
  // targets the real row without a refetch.
  function adoptId(parts, id) {
    if (!parts || !parts.row || !id) return;
    parts.row.setAttribute("data-msg-id", id);
    var button = parts.row.querySelector("[data-delete-msg]");
    if (button) button.setAttribute("data-delete-msg", id);
  }

  function timeLabel() {
    var label = el("span", "chat-time", now());
    label.setAttribute("dir", "ltr");
    return label;
  }

  function deleteButton(id, withReply) {
    var button = el(
      "button",
      "delete-btn opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity p-1 rounded " +
        "text-brand-400 hover:text-red-600 focus:text-red-600 " +
        "dark:text-sand-500 dark:hover:text-red-400 dark:focus:text-red-400"
    );
    button.type = "button";
    button.setAttribute("data-delete-msg", id);
    button.setAttribute("aria-label", str().deleteOne);
    button.title = withReply ? str().deleteReply : str().deleteOne;
    button.innerHTML = TRASH_ICON;
    return button;
  }

  function addUserMessage(text, id, withReply, animate) {
    var row = el(
      "div",
      "chat-row chat-row--user group" + (animate === false ? "" : " animate-fade-in")
    );
    row.setAttribute("data-msg-id", id);

    var col = el("div", "chat-col chat-col--user");
    var bubble = el("div", "bubble bubble--user", text);
    bubble.setAttribute("dir", "auto");

    var meta = el("div", "chat-meta");
    meta.appendChild(timeLabel());
    meta.appendChild(deleteButton(id, withReply));

    col.appendChild(bubble);
    col.appendChild(meta);
    row.appendChild(col);
    messagesEl.appendChild(row);
    scrollToBottom();
    return { row: row, bubble: bubble };
  }

  function addAssistantMessage(text, id, animate) {
    var parts = createAssistantMessage(id, animate);
    parts.bubble.textContent = text;
    return parts;
  }

  function createAssistantMessage(id, animate) {
    var row = el("div", "chat-row group" + (animate === false ? "" : " animate-fade-in"));
    row.setAttribute("data-msg-id", id);

    var avatar = el("div", "chat-avatar");
    var icon = el("span", "material-symbols-outlined text-[16px]", "spa");
    icon.setAttribute("aria-hidden", "true");
    avatar.appendChild(icon);

    var col = el("div", "chat-col");
    var bubble = el("div", "bubble bubble--assistant");
    bubble.setAttribute("dir", "auto");

    // assistant messages are only ever removed together with the question that
    // produced them, so they carry no delete button of their own
    var meta = el("div", "chat-meta");
    meta.appendChild(timeLabel());

    // transient tool activity; never written to chat history
    var activity = el("div", "chat-activity");
    activity.setAttribute("role", "status");
    activity.setAttribute("aria-live", "polite");
    activity.hidden = true;

    col.appendChild(bubble);
    col.appendChild(activity);
    col.appendChild(meta);
    row.appendChild(avatar);
    row.appendChild(col);
    messagesEl.appendChild(row);
    scrollToBottom();
    return { row: row, bubble: bubble, activity: activity };
  }

  function showTyping(bubble) {
    bubble.textContent = "";
    var dots = el("span", "typing");
    dots.appendChild(el("i"));
    dots.appendChild(el("i"));
    dots.appendChild(el("i"));
    bubble.appendChild(dots);
  }

  function markError(row, bubble, message) {
    row.removeAttribute("data-msg-id");
    var button = row.querySelector("[data-delete-msg]");
    if (button) button.remove();

    bubble.className = "bubble bubble--assistant bubble--error";
    bubble.textContent = "⚠️ " + message;
  }

  // Transient error in the status line, for failures with no bubble to mark.
  function markStatusError(message) {
    statusEl.textContent = message;
    statusDot.style.display = "none";
  }

  // --- composer / status --------------------------------------------------

  function setBusy(busy) {
    sendBtn.disabled = busy;
    // Swap the single action: Send hides while Stop takes its place, so the
    // composer never shows two competing buttons.
    sendBtn.classList.toggle("hidden", busy);
    if (stopBtn) {
      stopBtn.classList.toggle("hidden", !busy);
      stopBtn.disabled = false;
      stopBtn.setAttribute("aria-label", str().stop);
      stopBtn.title = str().stop;
    }
    statusEl.textContent = busy ? str().thinking : "";
    statusDot.style.display = busy ? "inline-block" : "none";
    // Refocus only where a keyboard is already present; on a phone this would
    // pop the keyboard open every time a reply finishes.
    var hasHover =
      !window.matchMedia || window.matchMedia("(hover: hover)").matches;
    if (!busy && hasHover) input.focus();
  }

  function errorText(payload, status) {
    var err = payload && payload.error;
    if (err && err.message) {
      return err.code ? err.code + ": " + err.message : err.message;
    }
    return "Request failed (" + status + ")";
  }

  function readJsonSafe(response) {
    return response.json().catch(function () {
      return null;
    });
  }

  // --- streaming ----------------------------------------------------------

  function consume(body, onEvent) {
    var reader = body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";

    function pump() {
      return reader.read().then(function (result) {
        if (result.done) return;
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

  function send(text) {
    // The server stores the user turn; render it optimistically and reload the
    // thread when the turn ends so ids and content come from the database.
    var userEntry = { id: newId(), role: "user", content: text };
    var userParts = addUserMessage(text, userEntry.id, true);
    showSuggestions(false);

    var assistantEntry = { id: newId(), role: "assistant", content: "" };
    var parts = createAssistantMessage(assistantEntry.id);
    showTyping(parts.bubble);
    setBusy(true);

    var answer = "";
    var streamError = null;
    var stoppedByServer = false;
    var activityTimer = null;
    var toolRunning = false;
    var saved = null;
    var turn = {
      controller: new AbortController(),
      streamId: null,
      stopped: false
    };
    activeTurn = turn;

    function hideActivity() {
      if (activityTimer) {
        clearTimeout(activityTimer);
        activityTimer = null;
      }
      toolRunning = false;
      parts.activity.hidden = true;
    }

    function rollbackStopped() {
      hideActivity();
      // The server already deleted the pair; drop the bubbles to match.
      if (parts.row && parts.row.parentNode) {
        parts.row.parentNode.removeChild(parts.row);
      }
      if (userParts && userParts.row && userParts.row.parentNode) {
        userParts.row.parentNode.removeChild(userParts.row);
      }
      // put the text back in the composer so it can be edited or resent
      input.value = text;
      input.style.height = "auto";
      input.focus();
    }

    function showToolEvent(tool) {
      if (!tool || !tool.name) return;
      if (activityTimer) {
        clearTimeout(activityTimer);
        activityTimer = null;
      }
      var isStart = tool.status === "start";
      toolRunning = isStart;
      parts.activity.textContent = isStart
        ? toolLabel(tool.name)
        : toolDoneLabel(tool.name, tool.ok !== false);
      parts.activity.hidden = false;
      followIfNearBottom();

      if (!isStart) {
        activityTimer = setTimeout(function () {
          activityTimer = null;
          parts.activity.hidden = true;
        }, 1400);
      }
    }

    Session.authFetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // Only the new message is sent; the server rebuilds history from the DB.
      body: JSON.stringify({
        conversation_id: conversationId,
        message: text,
        language: lang,
      }),
      signal: turn.controller.signal,
    })
      .then(function (response) {
        if (!response.ok || !response.body) {
          return readJsonSafe(response).then(function (payload) {
            throw new Error(errorText(payload, response.status));
          });
        }
        turn.streamId = response.headers.get("X-Stream-ID");
        var returnedId = response.headers.get("X-Conversation-ID");
        if (returnedId) conversationId = returnedId;
        return consume(response.body, function (event) {
          if (event.error) {
            hideActivity();
            streamError = event.error;
          } else if (event.tool) {
            showToolEvent(event.tool);
          } else if (event.delta) {
            hideActivity();
            if (!answer) {
              parts.bubble.textContent = "";
              parts.bubble.classList.add("is-streaming");
            }
            answer += event.delta;
            parts.bubble.textContent = answer;
            followIfNearBottom();
          } else if (event.done) {
            if (toolRunning) hideActivity();
          } else if (event.saved) {
            saved = event.saved;
          } else if (event.stopped) {
            stoppedByServer = true;
          }
        });
      })
      .then(function () {
        if (turn.stopped || stoppedByServer) {
          rollbackStopped();
          return;
        }
        if (toolRunning) hideActivity();
        parts.bubble.classList.remove("is-streaming");
        if (streamError) throw new Error(errorText({ error: streamError }, 0));
        if (!answer.trim()) {
          parts.bubble.textContent = str().noResponse;
        }

        // The stream told us the ids it stored, so there is no need to refetch
        // the thread. Only fall back to a reload if persistence failed.
        if (!saved) return loadThread(false);
        if (saved.conversation_id) conversationId = saved.conversation_id;
        adoptId(userParts, saved.user_turn_id);
        adoptId(parts, saved.assistant_turn_id);
        history.push({ id: saved.user_turn_id, role: "user", content: text });
        history.push({
          id: saved.assistant_turn_id,
          role: "assistant",
          content: answer,
        });
        if (welcomeRow) welcomeRow.classList.add("hidden");
      })
      .catch(function (err) {
        if (turn.stopped || stoppedByServer) {
          rollbackStopped();
          return;
        }
        hideActivity();
        parts.bubble.classList.remove("is-streaming");
        markError(parts.row, parts.bubble, err.message);
      })
      .then(function () {
        setBusy(false);
        activeTurn = null;
      });
  }

  // --- events -------------------------------------------------------------

  if (stopBtn) {
    stopBtn.addEventListener("click", function () {
      var turn = activeTurn;
      if (!turn || turn.stopped) return;
      turn.stopped = true;
      stopBtn.disabled = true;
      // ask the server to stop first (it closes the provider link), then drop
      // our own connection
      var request = turn.streamId
        ? Session.authFetch("/api/chat/stop", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: turn.streamId }),
            allowUnauthorized: true,
          })
        : Promise.resolve();
      request
        .catch(function () {
          /* the abort below still ends the turn locally */
        })
        .then(function () {
          turn.controller.abort();
        });
    });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = input.value.trim();
    if (!text || sendBtn.disabled) return;
    input.value = "";
    input.style.height = "auto";
    send(text);
    input.focus();
  });

  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
      } else {
        form.dispatchEvent(new Event("submit", { cancelable: true }));
      }
    }
  });

  input.addEventListener("input", function () {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 144) + "px";
  });

  // deleting a question also removes the assistant reply it produced
  messagesEl.addEventListener("click", function (event) {
    var button =
      event.target && event.target.closest ? event.target.closest("[data-delete-msg]") : null;
    if (!button) return;

    var id = button.getAttribute("data-delete-msg");

    // Deleting is a server operation now: the row goes away in the database and
    // the thread is re-rendered from it. A user turn also removes its reply,
    // which the server handles in one call.
    if (!conversationId || !id) return;
    Session.authFetch(
      "/api/conversations/" +
        encodeURIComponent(conversationId) +
        "/turns/" +
        encodeURIComponent(id),
      { method: "DELETE" }
    )
      .then(function (response) {
        if (response.status !== 204) {
          throw new Error(str().deleteFailed);
        }
        // Remove locally: the server already deleted the row and, for a user
        // turn, the reply that followed it.
        var index = -1;
        for (var i = 0; i < history.length; i++) {
          if (history[i].id === id) {
            index = i;
            break;
          }
        }
        if (index === -1) return;

        var doomed = [history[index].id];
        var next = history[index + 1];
        if (history[index].role === "user" && next && next.role === "assistant") {
          doomed.push(next.id);
        }
        history = history.filter(function (entry) {
          return doomed.indexOf(entry.id) === -1;
        });
        doomed.forEach(function (removedId) {
          var row = messagesEl.querySelector('[data-msg-id="' + removedId + '"]');
          if (row) row.remove();
        });
        if (welcomeRow) welcomeRow.classList.toggle("hidden", history.length > 0);
      })
      .catch(function (err) {
        markStatusError(err.message || str().deleteFailed);
      });
  });

  // Clear wipes the view immediately, so it works even when the request is slow
// or the thread was already deleted. The server copy is removed alongside.
  function clearView() {
    history = [];
    conversationId = null;
    Array.prototype.slice.call(messagesEl.children).forEach(function (child) {
      if (child !== welcomeRow && child !== suggestionsEl) child.remove();
    });
    if (welcomeRow) welcomeRow.classList.remove("hidden");
    showSuggestions(true);
    statusEl.textContent = "";
    statusDot.style.display = "none";
    input.focus();
  }

  clearBtn.addEventListener("click", function () {
    var ui = window.THUI;
    var ask = ui
      ? ui.confirm({
          title: str().clearTitle,
          message: str().clearBody,
          confirmLabel: str().clearConfirm,
          cancelLabel: str().cancel,
        })
      : Promise.resolve(window.confirm(str().clearTitle));

    ask.then(function (confirmed) {
      if (!confirmed) return;
      clearView();
      // Clear every thread, not just the current one: older databases hold one
      // conversation per message, and leaving those behind would resurrect one.
      Session.authFetch("/api/conversations", { method: "DELETE" })
        .then(function (response) {
          if (response.status !== 204) {
            throw new Error(str().clearFailed);
          }
        })
        .catch(function (err) {
          markStatusError(err.message || str().clearFailed);
        });
    });
  });

  if (jumpBtn) {
    jumpBtn.addEventListener("click", function () {
      // Smooth only for an explicit jump; streaming deltas scroll instantly.
      var reduce =
        window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (messagesEl.scrollTo) {
        messagesEl.scrollTo({
          top: messagesEl.scrollHeight,
          behavior: reduce ? "auto" : "smooth",
        });
      } else {
        scrollToBottom();
      }
      input.focus();
    });
  }

  messagesEl.addEventListener("scroll", syncJump, { passive: true });

  suggestionButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      var label = button.textContent.trim();
      if (!label) return;
      input.value = label;
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 144) + "px";
      input.focus();
    });
  });

  syncJump();

  if (langToggle) {
    langToggle.addEventListener("click", function () {
      lang = lang === "ar" ? "en" : "ar";
      saveLanguage();
      applyLanguage();
    });
  }

  logoutBtn.addEventListener("click", function () {
    Session.authFetch("/api/auth/logout", { method: "POST" }).catch(function () {});
    Session.clear();
    window.location.replace("/login");
  });
})();
