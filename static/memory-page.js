/* Memory page: list what SAGE stored, delete items, export a copy, and toggle
   consent. The page shell is public; every call goes through the guarded API
   with the bearer token from session.js. */
(function () {
  "use strict";

  var Session = window.Session;
  if (!Session || !Session.requireSession()) return; // no valid token -> /login

  var LANG_KEY = "th_language";

  var STRINGS = {
    en: {
      memoryTitle: "What SAGE remembers",
      memoryHint: "Everything saved about you. You can remove any item, or export a copy.",
      export: "Export",
      deleteAll: "Forget everything",
      empty: "Nothing saved yet.",
      entities: "People & things",
      facts: "Facts",
      relationships: "Relationships",
      consentTitle: "Permissions",
      consentHint: "Turn these off at any time. Nothing is shared without your permission.",
      delete: "Remove",
      confirmDelete: "Remove this item? SAGE will forget it.",
      confirmDeleteAll: "This deletes everything SAGE remembers about you. Continue?",
      cancel: "Cancel",
      deletedOne: "Removed.",
      deletedAll: "All memory deleted.",
      exported: "Export downloaded.",
      emptyValue: "(empty)",
      consentLocation: "Location",
      consentVoice: "Voice replies",
      consentMemorySharing: "Share memory with trusted people",
      consentFamiliarVoice: "Familiar voice",
      consentTrustedSharing: "Share my activity with trusted people",
      on: "On",
      off: "Off",
      genericError: "Something went wrong. Please try again.",
      loadFailed: "Could not load your memory.",
    },
    ar: {
      memoryTitle: "ما يتذكره SAGE",
      memoryHint: "كل ما تم حفظه عنك. يمكنك حذف أي عنصر أو تنزيل نسخة.",
      export: "تنزيل نسخة",
      deleteAll: "انسَ كل شيء",
      empty: "لا يوجد شيء محفوظ بعد.",
      entities: "الأشخاص والأشياء",
      facts: "الحقائق",
      relationships: "العلاقات",
      consentTitle: "الأذونات",
      consentHint: "يمكنك إيقافها في أي وقت. لا يُشارك أي شيء بدون إذنك.",
      delete: "حذف",
      confirmDelete: "حذف هذا العنصر؟ سينساه SAGE.",
      confirmDeleteAll: "سيتم حذف كل ما يتذكره SAGE عنك. هل تريد المتابعة؟",
      cancel: "إلغاء",
      deletedOne: "تم الحذف.",
      deletedAll: "تم حذف كل الذاكرة.",
      exported: "تم تنزيل النسخة.",
      emptyValue: "(فارغ)",
      consentLocation: "الموقع",
      consentVoice: "الردود الصوتية",
      consentMemorySharing: "مشاركة الذاكرة مع أشخاص موثوقين",
      consentFamiliarVoice: "صوت مألوف",
      consentTrustedSharing: "مشاركة نشاطي مع أشخاص موثوقين",
      on: "مفعّل",
      off: "متوقف",
      genericError: "حدث خطأ ما. حاول مرة أخرى.",
      loadFailed: "تعذر تحميل الذاكرة.",
    },
  };

  var CONSENT_LABELS = {
    location: "consentLocation",
    voice: "consentVoice",
    memory_sharing: "consentMemorySharing",
    familiar_voice: "consentFamiliarVoice",
    trusted_sharing: "consentTrustedSharing",
  };

  var lang = "en";

  function normalizeLang(value) {
    return value === "ar" ? "ar" : "en";
  }

  function str() {
    return STRINGS[lang] || STRINGS.en;
  }

  try {
    lang = normalizeLang(localStorage.getItem(LANG_KEY));
  } catch (err) {
    /* storage blocked; English is fine */
  }

  var errorEl = document.getElementById("memory-error");
  var successEl = document.getElementById("memory-success");
  var emptyEl = document.getElementById("memory-empty");
  var contentEl = document.getElementById("memory-content");
  var entityList = document.getElementById("entity-list");
  var factList = document.getElementById("fact-list");
  var relationshipList = document.getElementById("relationship-list");
  var consentList = document.getElementById("consent-list");
  var exportButton = document.getElementById("export-button");
  var deleteAllButton = document.getElementById("delete-all-button");

  function showError(message) {
    successEl.classList.add("hidden");
    errorEl.textContent = message;
    errorEl.classList.remove("hidden");
  }

  function showSuccess(message) {
    errorEl.classList.add("hidden");
    if (window.THUI) {
      window.THUI.showSuccess(successEl, message);
      return;
    }
    successEl.textContent = message;
    successEl.classList.remove("hidden");
  }

  function clearMessages() {
    errorEl.classList.add("hidden");
    successEl.classList.add("hidden");
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function renderValue(value) {
    if (value === null || value === undefined || value === "") return str().emptyValue;
    if (typeof value === "string") return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    try {
      return JSON.stringify(value);
    } catch (err) {
      return str().emptyValue;
    }
  }

  function row(mainText, subText, onDelete) {
    var item = el(
      "li",
      "hover-lift flex items-start justify-between gap-3 rounded-xl border border-sand-200 px-3.5 py-2.5 dark:border-white/10"
    );
    var body = el("div", "min-w-0");
    body.appendChild(el("p", "break-words text-sm text-brand-900 dark:text-sand-100", mainText));
    if (subText) {
      body.appendChild(el("p", "mt-0.5 text-xs text-brand-500 dark:text-sand-400", subText));
    }
    item.appendChild(body);

    var button = el(
      "button",
      "shrink-0 rounded-lg px-3 py-2 text-xs font-semibold text-red-700 transition-colors hover:bg-red-50 sm:px-2.5 sm:py-1 dark:text-red-300 dark:hover:bg-red-950/40",
      str().delete
    );
    button.type = "button";
    button.addEventListener("click", onDelete);
    item.appendChild(button);
    return item;
  }

  function deleteItem(url, onDone) {
    var ask = window.THUI
      ? window.THUI.confirm({
          title: str().confirmDelete,
          confirmLabel: str().delete,
          cancelLabel: str().cancel,
        })
      : Promise.resolve(window.confirm(str().confirmDelete));

    ask.then(function (confirmed) {
      if (!confirmed) return;
      clearMessages();
      Session.authFetch(url, { method: "DELETE" })
        .then(function (response) {
          if (response.status !== 204) {
            return response
              .json()
              .catch(function () {
                return null;
              })
              .then(function (payload) {
                var err = payload && payload.error;
                throw new Error(err && err.message ? err.message : str().genericError);
              });
          }
          onDone();
          showSuccess(str().deletedOne);
        })
        .catch(function (err) {
          showError(err.message || str().genericError);
        });
    });
  }

  function renderMemory(data) {
    entityList.textContent = "";
    factList.textContent = "";
    relationshipList.textContent = "";

    (data.entities || []).forEach(function (entity) {
      entityList.appendChild(
        row(
          entity.name,
          entity.entity_type,
          function () {
            deleteItem("/api/memory/entity/" + encodeURIComponent(entity.id), loadMemory);
          }
        )
      );
    });

    (data.facts || []).forEach(function (fact) {
      factList.appendChild(
        row(
          fact.predicate + ": " + renderValue(fact.value),
          fact.entity + (fact.valid_until ? " · expired" : ""),
          function () {
            deleteItem("/api/memory/fact/" + encodeURIComponent(fact.id), loadMemory);
          }
        )
      );
    });

    (data.relationships || []).forEach(function (relationship) {
      relationshipList.appendChild(
        row(
          relationship.source + " → " + relationship.target,
          relationship.relationship_type,
          function () {
            deleteItem(
              "/api/memory/relationship/" + encodeURIComponent(relationship.id),
              loadMemory
            );
          }
        )
      );
    });

    var empty =
      !(data.entities || []).length &&
      !(data.facts || []).length &&
      !(data.relationships || []).length;
    emptyEl.classList.toggle("hidden", !empty);
    contentEl.classList.toggle("hidden", empty);
    // Re-trigger the drift each time the empty state appears, so it reads as a
    // state change rather than a static paragraph.
    if (empty) {
      emptyEl.classList.remove("empty-float");
      void emptyEl.offsetWidth;
      emptyEl.classList.add("empty-float");
    }
  }

  function renderConsents(consents) {
    consentList.textContent = "";
    Object.keys(consents || {}).forEach(function (kind) {
      var entry = consents[kind];
      var item = el(
        "li",
        "hover-lift flex items-center justify-between gap-3 rounded-xl border border-sand-200 px-3.5 py-2.5 dark:border-white/10"
      );
      var label = el("span", "text-sm text-brand-900 dark:text-sand-100", str()[CONSENT_LABELS[kind]] || kind);
      item.appendChild(label);

      // A sliding knob plus the On/Off word. The motion carries the state
      // change at a glance, while the text keeps the contract that a toggle
      // reads its state in words, not colour alone.
      var button = el("button", "flex shrink-0 items-center gap-2");
      button.type = "button";
      button.setAttribute("role", "switch");
      button.setAttribute("aria-checked", entry.granted ? "true" : "false");
      button.setAttribute("aria-label", str()[CONSENT_LABELS[kind]] || kind);
      var knob = el("span", "switch");
      knob.appendChild(el("span", "switch-knob"));
      button.appendChild(knob);
      button.appendChild(
        el(
          "span",
          "text-xs font-semibold " +
            (entry.granted
              ? "text-brand-800 dark:text-brand-100"
              : "text-brand-500 dark:text-sand-400"),
          entry.granted ? str().on : str().off
        )
      );

      button.addEventListener("click", function () {
        clearMessages();
        Session.authFetch("/api/consents/" + encodeURIComponent(kind), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ granted: !entry.granted }),
        })
          .then(function (response) {
            if (!response.ok) throw new Error(str().genericError);
            return loadConsents();
          })
          .catch(function (err) {
            showError(err.message || str().genericError);
          });
      });
      item.appendChild(button);
      consentList.appendChild(item);
    });
  }

  function loadMemory() {
    if (window.THUI) {
      window.THUI.skeletons(entityList, 2);
      window.THUI.skeletons(factList, 2);
      window.THUI.skeletons(relationshipList, 2);
    }
    return Session.authFetch("/api/memory")
      .then(function (response) {
        if (!response.ok) throw new Error(str().loadFailed);
        return response.json();
      })
      .then(renderMemory)
      .catch(function (err) {
        [entityList, factList, relationshipList].forEach(function (list) {
          if (list) list.textContent = "";
        });
        showError(err.message || str().loadFailed);
      });
  }

  function loadConsents() {
    if (window.THUI) window.THUI.skeletons(consentList, 2);
    return Session.authFetch("/api/consents")
      .then(function (response) {
        if (!response.ok) throw new Error(str().genericError);
        return response.json();
      })
      .then(function (data) {
        renderConsents(data.consents);
      })
      .catch(function (err) {
        if (consentList) consentList.textContent = "";
        showError(err.message || str().genericError);
      });
  }

  if (exportButton) {
    exportButton.addEventListener("click", function () {
      clearMessages();
      Session.authFetch("/api/memory/export")
        .then(function (response) {
          if (!response.ok) throw new Error(str().genericError);
          return response.blob();
        })
        .then(function (blob) {
          var url = URL.createObjectURL(blob);
          var link = document.createElement("a");
          link.href = url;
          link.download = "sage-memory-export.json";
          document.body.appendChild(link);
          link.click();
          link.remove();
          URL.revokeObjectURL(url);
          showSuccess(str().exported);
        })
        .catch(function (err) {
          showError(err.message || str().genericError);
        });
    });
  }

  if (deleteAllButton) {
    deleteAllButton.addEventListener("click", function () {
      var ask = window.THUI
        ? window.THUI.confirm({
            title: str().confirmDeleteAll,
            confirmLabel: str().deleteAll,
            cancelLabel: str().cancel,
          })
        : Promise.resolve(window.confirm(str().confirmDeleteAll));

      ask.then(function (confirmed) {
        if (!confirmed) return;
        clearMessages();
        Session.authFetch("/api/memory", {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: true }),
        })
          .then(function (response) {
            if (!response.ok) throw new Error(str().genericError);
            return response.json();
          })
          .then(function () {
            showSuccess(str().deletedAll);
            return loadMemory();
          })
          .catch(function (err) {
            showError(err.message || str().genericError);
          });
      });
    });
  }

  loadMemory();
  loadConsents();
})();
