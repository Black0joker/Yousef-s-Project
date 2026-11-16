/* Chat page: streams replies from /api/chat/stream, keeps history in localStorage,
   and lets you delete a single message. */
(function () {
  "use strict";

  var NAME_KEY = "th_chat_name";
  var HISTORY_KEY = "th_chat_history";

  var NAME = (localStorage.getItem(NAME_KEY) || "").trim();
  if (!NAME) {
    window.location.replace("/");
    return;
  }

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
  var changeName = document.getElementById("change-name");

  greetingEl.textContent = "Hi, " + NAME;
  avatarEl.textContent = NAME.charAt(0).toUpperCase();
  welcomeBubble.textContent = "Welcome, " + NAME + "! Ask me anything to get started.";

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

  // --- helpers ------------------------------------------------------------

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
    return el("span", "text-xs text-slate-400 font-label dark:text-slate-500", now());
  }

  function deleteButton(id, withReply) {
    var button = el(
      "button",
      "delete-btn opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity p-1 rounded " +
        "text-slate-400 hover:text-red-600 focus:text-red-600 " +
        "dark:text-slate-500 dark:hover:text-red-400 dark:focus:text-red-400"
    );
    button.type = "button";
    button.setAttribute("data-delete-msg", id);
    button.setAttribute("aria-label", "Delete this message");
    button.title = withReply ? "Delete this message and its reply" : "Delete this message";
    button.innerHTML = TRASH_ICON;
    return button;
  }

  function addUserMessage(text, id, withReply) {
    var row = el("div", "flex items-start justify-end gap-3 max-w-[85%] ml-auto group animate-fade-in");
    row.setAttribute("data-msg-id", id);

    var col = el("div", "flex flex-col items-end");
    var bubble = el(
      "div",
      "bg-[#2563eb] text-white px-4 py-3 rounded-[16px] rounded-br-[4px] text-[15px] leading-relaxed " +
        "shadow-sm whitespace-pre-wrap break-words selection:bg-blue-800 dark:bg-blue-600",
      text
    );

    var meta = el("div", "flex items-center gap-1.5 mt-1.5 mr-1");
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
    var row = el("div", "flex items-start gap-3 max-w-[85%] group animate-fade-in");
    row.setAttribute("data-msg-id", id);

    var avatar = el(
      "div",
      "w-8 h-8 rounded-full bg-gradient-to-tr from-blue-600 to-indigo-500 text-white flex-shrink-0 " +
        "flex items-center justify-center shadow-sm mt-0.5"
    );
    var icon = el("span", "material-symbols-outlined text-[16px]", "smart_toy");
    icon.setAttribute("aria-hidden", "true");
    avatar.appendChild(icon);

    var col = el("div", "flex flex-col");
    var bubble = el(
      "div",
      "bg-[#eef1f5] text-[#1e293b] px-4 py-3 rounded-[16px] rounded-bl-[4px] text-[15px] leading-relaxed " +
        "shadow-sm whitespace-pre-wrap break-words dark:bg-slate-800 dark:text-slate-100"
    );

    // assistant messages are only ever removed together with the question that
    // produced them, so they carry no delete button of their own
    var meta = el("div", "flex items-center gap-1.5 mt-1.5 ml-1");
    meta.appendChild(timeLabel());

    col.appendChild(bubble);
    col.appendChild(meta);
    row.appendChild(avatar);
    row.appendChild(col);
    messagesEl.appendChild(row);
    scrollToBottom();
    return { row: row, bubble: bubble };
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

    bubble.className =
      "bg-red-50 text-red-700 border border-red-200 px-4 py-3 rounded-[16px] rounded-bl-[4px] " +
      "text-[15px] leading-relaxed shadow-sm whitespace-pre-wrap break-words " +
      "dark:bg-red-950/40 dark:text-red-300 dark:border-red-900/70";
    bubble.textContent = "⚠️ " + message;
  }

  // --- composer / status --------------------------------------------------

  function setBusy(busy) {
    sendBtn.disabled = busy;
    input.disabled = busy;
    statusEl.textContent = busy ? "Thinking…" : "";
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

    fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // ids stay client-side; the API only ever sees role + content
      body: JSON.stringify({
        messages: history.map(function (m) {
          return { role: m.role, content: m.content };
        }),
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
            streamError = event.error;
          } else if (event.delta) {
            if (!answer) {
              parts.bubble.textContent = "";
              parts.bubble.classList.add("is-streaming");
            }
            answer += event.delta;
            parts.bubble.textContent = answer;
            scrollToBottom();
          }
        });
      })
      .then(function () {
        parts.bubble.classList.remove("is-streaming");
        if (streamError) throw new Error(errorText({ error: streamError }, 0));
        if (!answer.trim()) {
          parts.bubble.textContent = "(no response)";
        }
        assistantEntry.content = answer;
        history.push(assistantEntry);
        saveHistory();
      })
      .catch(function (err) {
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

  changeName.addEventListener("click", function () {
    localStorage.removeItem(NAME_KEY); // keep the thread, return to the name form
  });
})();
