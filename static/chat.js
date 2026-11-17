/* Chat page: authenticated streaming chat. The bearer token travels in the
   Authorization header on every request; history lives in localStorage per user. */
(function () {
  "use strict";

  var Session = window.Session;
  if (!Session || !Session.requireSession()) return; // no valid token -> /login

  var user = Session.getUser() || {};
  var fullName = (user.full_name || "").trim();
  var firstName = (user.first_name || "").trim() || fullName.split(" ")[0] || "there";
  var HISTORY_KEY = "th_chat_history_" + (user.id != null ? user.id : "anon");

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
      toggleLang: "Switch language",
      deleteReply: "Delete this message and its reply",
      deleteOne: "Delete this message",
      toolSaving: "Saving memory...",
      toolSearching: "Searching memory...",
      toolSaved: "Memory saved",
      toolFound: "Memory searched",
      toolFailed: "Memory tool failed",
      toolGeneric: "Using tool...",
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
      toggleLang: "تغيير اللغة",
      deleteReply: "احذف هذه الرسالة وردها",
      deleteOne: "احذف هذه الرسالة",
      toolSaving: "جارٍ حفظ الذاكرة...",
      toolSearching: "جارٍ البحث في الذاكرة...",
      toolSaved: "تم حفظ الذاكرة",
      toolFound: "تم البحث في الذاكرة",
      toolFailed: "تعذر استخدام أداة الذاكرة",
      toolGeneric: "جارٍ استخدام الأداة...",
    },
  };

  function normalizeLang(value) {
    return LANG_ALIASES[String(value || "").trim().toLowerCase()] || "en";
  }

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
    return str().toolGeneric;
  }

  function toolDoneLabel(name, ok) {
    if (!ok) return str().toolFailed;
    if (name === "save_memory") return str().toolSaved;
    if (name === "recall_memory") return str().toolFound;
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
  var clearBtn = document.getElementById("clear");
  var statusEl = document.getElementById("status");
  var statusDot = document.getElementById("status-dot");
  var logoutBtn = document.getElementById("logout");
  var logoutLabel = document.getElementById("logout-label") || logoutBtn;
  var clearLabel = document.getElementById("clear-label");
  var langToggle = document.getElementById("lang-toggle");
  var langLabel = document.getElementById("lang-label");

  applyLanguage();

  var history = loadHistory();
  history.forEach(function (entry, index) {
    if (entry.role !== "user") {
      addAssistantMessage(entry.content, entry.id);
      return;
    }
    var next = history[index + 1];
    addUserMessage(entry.content, entry.id, !!next && next.role === "assistant");
  });
  saveHistory(); // persist any ids assigned to older entries

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
    input.setAttribute("placeholder", strings.placeholderShort || strings.placeholder);
    input.title = strings.placeholder;
    if (langLabel) langLabel.textContent = strings.code;
    if (langToggle) {
      langToggle.setAttribute("aria-label", strings.toggleLang);
      langToggle.setAttribute("title", strings.toggleLang);
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
  }

  function loadHistory() {
    try {
      var parsed = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
      if (!Array.isArray(parsed)) return [];
      return parsed
        .filter(function (m) {
          return (
            m &&
            (m.role === "user" || m.role === "assistant") &&
            typeof m.content === "string" &&
            m.content.length > 0
          );
        })
        .map(function (m) {
          return { id: typeof m.id === "string" && m.id ? m.id : newId(), role: m.role, content: m.content };
        });
    } catch (err) {
      return [];
    }
  }

  function saveHistory() {
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
    } catch (err) {
      /* storage full or blocked; the chat still works for this session */
    }
  }

  // --- message rendering --------------------------------------------------

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

  function addUserMessage(text, id, withReply) {
    var row = el("div", "chat-row chat-row--user group animate-fade-in");
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

  function addAssistantMessage(text, id) {
    var parts = createAssistantMessage(id);
    parts.bubble.textContent = text;
    return parts;
  }

  function createAssistantMessage(id) {
    var row = el("div", "chat-row group animate-fade-in");
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

  // --- composer / status --------------------------------------------------

  function setBusy(busy) {
    sendBtn.disabled = busy;
    input.disabled = busy;
    statusEl.textContent = busy ? str().thinking : "";
    statusDot.style.display = busy ? "inline-block" : "none";
    if (!busy) input.focus();
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
    var userEntry = { id: newId(), role: "user", content: text };
    history.push(userEntry);
    saveHistory();
    addUserMessage(text, userEntry.id, true);

    var assistantEntry = { id: newId(), role: "assistant", content: "" };
    var parts = createAssistantMessage(assistantEntry.id);
    showTyping(parts.bubble);
    setBusy(true);

    var answer = "";
    var streamError = null;
    var activityTimer = null;
    var toolRunning = false;

    function hideActivity() {
      if (activityTimer) {
        clearTimeout(activityTimer);
        activityTimer = null;
      }
      toolRunning = false;
      parts.activity.hidden = true;
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
      scrollToBottom();

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
      // ids stay client-side; the API only ever sees role + content
      body: JSON.stringify({
        messages: history.map(function (m) {
          return { role: m.role, content: m.content };
        }),
        language: lang,
      }),
    })
      .then(function (response) {
        if (!response.ok || !response.body) {
          return readJsonSafe(response).then(function (payload) {
            throw new Error(errorText(payload, response.status));
          });
        }
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
            scrollToBottom();
          } else if (event.done) {
            if (toolRunning) hideActivity();
          }
        });
      })
      .then(function () {
        if (toolRunning) hideActivity();
        parts.bubble.classList.remove("is-streaming");
        if (streamError) throw new Error(errorText({ error: streamError }, 0));
        if (!answer.trim()) {
          parts.bubble.textContent = str().noResponse;
        }
        assistantEntry.content = answer;
        history.push(assistantEntry);
        saveHistory();
      })
      .catch(function (err) {
        hideActivity();
        parts.bubble.classList.remove("is-streaming");
        markError(parts.row, parts.bubble, err.message);
      })
      .then(function () {
        setBusy(false);
      });
  }

  // --- events -------------------------------------------------------------

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = input.value.trim();
    if (!text || sendBtn.disabled) return;
    input.value = "";
    input.style.height = "auto";
    send(text);
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
    saveHistory();

    doomed.forEach(function (removedId) {
      var row = messagesEl.querySelector('[data-msg-id="' + removedId + '"]');
      if (row) row.remove();
    });
  });

  clearBtn.addEventListener("click", function () {
    history = [];
    saveHistory();
    Array.prototype.slice.call(messagesEl.children).forEach(function (child) {
      if (child !== welcomeRow) child.remove();
    });
    statusEl.textContent = "";
    statusDot.style.display = "none";
    input.focus();
  });

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
