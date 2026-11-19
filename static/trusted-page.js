/* Trusted people page: invite by email, accept or decline invitations, and see
   the activity of the people who trust you. The page shell is public; every
   call goes through the guarded API with the bearer token from session.js. */
(function () {
  "use strict";

  var Session = window.Session;
  if (!Session || !Session.requireSession()) return; // no valid token -> /login

  var LANG_KEY = "th_language";

  var STRINGS = {
    en: {
      trustedTitle: "People I trust",
      trustedHint: "They can see when you were last active, and your last location if you allow it. Nothing is shared until they accept.",
      trustedEmpty: "You have not added anyone yet.",
      emailLabel: "Their email",
      allowLastSeen: "Last seen",
      allowLocation: "Last location",
      sendInvite: "Send invitation",
      invitesTitle: "Invitations for me",
      invitesEmpty: "No pending invitations.",
      watchingTitle: "People who trust me",
      watchingEmpty: "Nobody is watching over you yet.",
      sharingTitle: "Share my activity",
      sharingHint: "While this is off, the people you added can see that you invited them but not your last seen or location.",
      sharingOn: "Sharing on",
      sharingOff: "Sharing off",
      notSharing: "They have not turned sharing on yet",
      paused: "Sharing paused",
      pending: "Waiting for them to accept",
      accepted: "Accepted",
      revoked: "Revoked",
      revoke: "Remove",
      accept: "Accept",
      decline: "Decline",
      see: "View activity",
      hide: "Hide",
      confirmRevoke: "Remove this trusted person? They will lose access immediately.",
      inviteSent: "Invitation sent.",
      acceptedMsg: "Invitation accepted.",
      declinedMsg: "Invitation declined.",
      revokedMsg: "Access removed.",
      lastSeen: "Last seen",
      neverSeen: "No activity recorded yet",
      warning: "No activity for over 24 hours.",
      location: "Last location",
      locationHidden: "Not shared",
      staleBadge: "Check in",
      activeBadge: "Active",
      unknownBadge: "No activity yet",
      genericError: "Something went wrong. Please try again.",
      loadFailed: "Could not load this page.",
    },
    ar: {
      trustedTitle: "أشخاص أثق بهم",
      trustedHint: "يمكنهم رؤية آخر نشاط لك، وموقعك الأخير إذا سمحت. لا يُشارك شيء قبل موافقتهم.",
      trustedEmpty: "لم تضف أحدًا بعد.",
      emailLabel: "بريدهم الإلكتروني",
      allowLastSeen: "آخر ظهور",
      allowLocation: "آخر موقع",
      sendInvite: "أرسل الدعوة",
      invitesTitle: "دعوات لك",
      invitesEmpty: "لا توجد دعوات معلقة.",
      watchingTitle: "أشخاص يثقون بي",
      watchingEmpty: "لا أحد يطمئن عليك بعد.",
      sharingTitle: "مشاركة نشاطي",
      sharingHint: "عند إيقافها، يرى من أضفتهم أنك دعوتهم، لكن ليس آخر ظهورك ولا موقعك.",
      sharingOn: "المشاركة مفعّلة",
      sharingOff: "المشاركة متوقفة",
      notSharing: "لم يفعّلوا المشاركة بعد",
      paused: "المشاركة متوقفة",
      pending: "بانتظار موافقتهم",
      accepted: "تمت الموافقة",
      revoked: "ملغى",
      revoke: "إزالة",
      accept: "موافقة",
      decline: "رفض",
      see: "عرض النشاط",
      hide: "إخفاء",
      confirmRevoke: "إزالة هذا الشخص؟ سيفقد الوصول فورًا.",
      inviteSent: "تم إرسال الدعوة.",
      acceptedMsg: "تمت قبول الدعوة.",
      declinedMsg: "تم رفض الدعوة.",
      revokedMsg: "تم إلغاء الوصول.",
      lastSeen: "آخر ظهور",
      neverSeen: "لا يوجد نشاط مسجل بعد",
      warning: "لا يوجد نشاط منذ أكثر من 24 ساعة.",
      location: "آخر موقع",
      locationHidden: "غير مشارك",
      staleBadge: "اطمئن عليه",
      activeBadge: "نشط",
      unknownBadge: "لا نشاط بعد",
      genericError: "حدث خطأ ما. حاول مرة أخرى.",
      loadFailed: "تعذر تحميل الصفحة.",
    },
  };

  var lang = "en";
  try {
    lang = localStorage.getItem(LANG_KEY) === "ar" ? "ar" : "en";
  } catch (err) {
    /* storage blocked */
  }

  function str() {
    return STRINGS[lang] || STRINGS.en;
  }

  var errorEl = document.getElementById("trusted-error");
  var successEl = document.getElementById("trusted-success");
  var trustedList = document.getElementById("trusted-list");
  var trustedEmpty = document.getElementById("trusted-empty");
  var invitesList = document.getElementById("invites-list");
  var invitesEmpty = document.getElementById("invites-empty");
  var watchingList = document.getElementById("watching-list");
  var watchingEmpty = document.getElementById("watching-empty");
  var sharingToggle = document.getElementById("sharing-toggle");
  var form = document.getElementById("trusted-form");

  function showError(message) {
    successEl.classList.add("hidden");
    errorEl.textContent = message;
    errorEl.classList.remove("hidden");
  }

  function showSuccess(message) {
    errorEl.classList.add("hidden");
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

  function actionButton(label, onClick, danger) {
    var button = el(
      "button",
      "shrink-0 rounded-lg px-2.5 py-1 text-xs font-semibold transition-colors " +
        (danger
          ? "text-red-700 hover:bg-red-50 dark:text-red-300 dark:hover:bg-red-950/40"
          : "text-brand-700 hover:bg-brand-50 dark:text-brand-200 dark:hover:bg-brand-900/40"),
      label
    );
    button.type = "button";
    button.addEventListener("click", onClick);
    return button;
  }

  function card(mainText, subText) {
    var item = el(
      "li",
      "flex items-start justify-between gap-3 rounded-xl border border-sand-200 px-3.5 py-2.5 dark:border-white/10"
    );
    var body = el("div", "min-w-0");
    body.appendChild(el("p", "truncate text-sm text-brand-900 dark:text-sand-100", mainText));
    if (subText) {
      body.appendChild(el("p", "mt-0.5 text-xs text-brand-500 dark:text-sand-400", subText));
    }
    item.appendChild(body);
    return { item: item, body: body };
  }

  function formatWhen(value) {
    if (!value) return "";
    var parsed = new Date(value);
    if (isNaN(parsed.getTime())) return value;
    return parsed.toLocaleString(undefined, {
      weekday: "short", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  }

  function statusLabel(status) {
    if (status === "accepted") return str().accepted;
    if (status === "revoked") return str().revoked;
    return str().pending;
  }

  function send(url, options) {
    clearMessages();
    return Session.authFetch(url, options || {})
      .then(function (response) {
        if (!response.ok && response.status !== 204) {
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
        return null;
      });
  }

  // --- my trusted people -------------------------------------------------

  function renderTrusted(items) {
    trustedList.textContent = "";
    items.forEach(function (contact) {
      var row = card(contact.email, statusLabel(contact.status));
      if (contact.status !== "revoked") {
        row.item.appendChild(
          actionButton(
            str().revoke,
            function () {
              if (!window.confirm(str().confirmRevoke)) return;
              send("/api/trusted/" + encodeURIComponent(contact.id), { method: "DELETE" })
                .then(function () {
                  showSuccess(str().revokedMsg);
                  return loadAll();
                })
                .catch(function (err) {
                  showError(err.message || str().genericError);
                });
            },
            true
          )
        );
      }
      trustedList.appendChild(row.item);
    });
    trustedEmpty.classList.toggle("hidden", items.length > 0);
  }

  // --- invitations -------------------------------------------------------

  function renderInvites(items) {
    invitesList.textContent = "";
    items.forEach(function (invite) {
      var row = card(invite.email, str().pending);
      var actions = el("div", "flex shrink-0 gap-1");
      actions.appendChild(
        actionButton(str().accept, function () {
          send("/api/trusted/invitations/" + encodeURIComponent(invite.id) + "/accept", {
            method: "POST",
          })
            .then(function () {
              showSuccess(str().acceptedMsg);
              return loadAll();
            })
            .catch(function (err) {
              showError(err.message || str().genericError);
            });
        })
      );
      actions.appendChild(
        actionButton(
          str().decline,
          function () {
            send("/api/trusted/invitations/" + encodeURIComponent(invite.id) + "/decline", {
              method: "POST",
            })
              .then(function () {
                showSuccess(str().declinedMsg);
                return loadAll();
              })
              .catch(function (err) {
                showError(err.message || str().genericError);
              });
          },
          true
        )
      );
      row.item.appendChild(actions);
      invitesList.appendChild(row.item);
    });
    invitesEmpty.classList.toggle("hidden", items.length > 0);
  }

  // --- people I can see --------------------------------------------------

  function renderPeek(container, data) {
    container.textContent = "";

    var badgeText = str().unknownBadge;
    if (data.status === "active") badgeText = str().activeBadge;
    if (data.status === "stale") badgeText = str().staleBadge;

    var badge = el(
      "span",
      "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold " +
        (data.warning
          ? "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
          : "bg-brand-100 text-brand-800 dark:bg-brand-900/60 dark:text-brand-100"),
      badgeText
    );

    var line = el("p", "mt-1 text-xs text-brand-600 dark:text-sand-400");
    line.textContent = data.last_seen
      ? str().lastSeen + ": " + formatWhen(data.last_seen)
      : str().neverSeen;
    container.appendChild(line);

    if (data.location) {
      var where = el("p", "mt-1 text-xs text-brand-600 dark:text-sand-400");
      where.textContent =
        str().location + ": " +
        data.location.latitude.toFixed(4) + ", " + data.location.longitude.toFixed(4);
      container.appendChild(where);
    } else {
      container.appendChild(
        el("p", "mt-1 text-xs text-brand-500 dark:text-sand-400", str().locationHidden)
      );
    }

    // The warning is a prompt to reach out, never a claim that something is
    // wrong: a user may simply have stopped using the app.
    if (data.warning) {
      container.appendChild(
        el(
          "p",
          "mt-2 rounded-lg bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-200",
          str().warning
        )
      );
    }

    container.appendChild(el("div", "mt-2")).appendChild(badge);
  }

  function renderWatching(items) {
    watchingList.textContent = "";
    items.forEach(function (person) {
      // "sharing: false" means the owner accepted but has not turned sharing on.
      // Show the person with that state rather than hiding them, so an accepted
      // invitation never looks like it vanished.
      var subText = person.sharing ? statusLabel("accepted") : str().notSharing;
      var row = card(person.name || person.email || "", subText);
      var detail = el("div", "mt-2 hidden");
      var expanded = false;

      if (!person.sharing) {
        row.body.appendChild(
          el(
            "p",
            "mt-2 rounded-lg bg-sand-100 px-2.5 py-1.5 text-xs text-brand-700 dark:bg-white/5 dark:text-sand-300",
            str().paused
          )
        );
        watchingList.appendChild(row.item);
        return;
      }

      var toggle = actionButton(str().see, function () {
        expanded = !expanded;
        toggle.textContent = expanded ? str().hide : str().see;
        detail.classList.toggle("hidden", !expanded);
        if (!expanded) return;

        Session.authFetch("/api/trusted/people/" + encodeURIComponent(person.id))
          .then(function (response) {
            if (!response.ok) throw new Error(str().genericError);
            return response.json();
          })
          .then(function (data) {
            renderPeek(detail, data);
          })
          .catch(function (err) {
            showError(err.message || str().genericError);
          });
      });

      row.body.appendChild(toggle);
      row.body.appendChild(detail);
      watchingList.appendChild(row.item);
    });
    watchingEmpty.classList.toggle("hidden", items.length > 0);
  }

  // --- sharing switch ----------------------------------------------------

  var sharingGranted = false;

  function renderSharing(granted) {
    sharingGranted = granted;
    if (!sharingToggle) return;
    sharingToggle.textContent = granted ? str().sharingOn : str().sharingOff;
    sharingToggle.className =
      "shrink-0 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors " +
      (granted
        ? "bg-brand-100 text-brand-800 hover:bg-brand-200 dark:bg-brand-900/60 dark:text-brand-100 dark:hover:bg-brand-800"
        : "bg-amber-100 text-amber-800 hover:bg-amber-200 dark:bg-amber-900/40 dark:text-amber-200 dark:hover:bg-amber-900/60");
  }

  function loadSharing() {
    return Session.authFetch("/api/consents")
      .then(function (response) {
        if (!response.ok) return null;
        return response.json();
      })
      .then(function (data) {
        var entry = data && data.consents && data.consents.trusted_sharing;
        renderSharing(!!(entry && entry.granted));
      })
      .catch(function () {
        /* the switch keeps its last state */
      });
  }

  if (sharingToggle) {
    sharingToggle.addEventListener("click", function () {
      clearMessages();
      Session.authFetch("/api/consents/trusted_sharing", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ granted: !sharingGranted }),
      })
        .then(function (response) {
          if (!response.ok) throw new Error(str().genericError);
          return response.json();
        })
        .then(function (data) {
          renderSharing(!!data.granted);
          return loadAll();
        })
        .catch(function (err) {
          showError(err.message || str().genericError);
        });
    });
  }

  // --- loading -----------------------------------------------------------

  function loadAll() {
    return Promise.all([
      Session.authFetch("/api/trusted").then(function (r) {
        return r.ok ? r.json() : { trusted: [] };
      }),
      Session.authFetch("/api/trusted/invitations").then(function (r) {
        return r.ok ? r.json() : { invitations: [] };
      }),
      Session.authFetch("/api/trusted/watching").then(function (r) {
        return r.ok ? r.json() : { watching: [] };
      }),
    ])
      .then(function (results) {
        renderTrusted(results[0].trusted || []);
        renderInvites(results[1].invitations || []);
        renderWatching(results[2].watching || []);
      })
      .catch(function (err) {
        showError(err.message || str().loadFailed);
      });
  }

  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      clearMessages();
      var email = document.getElementById("email").value.trim();
      if (!email) {
        showError(str().genericError);
        return;
      }

      send("/api/trusted", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: email,
          can_see_last_seen: document.getElementById("can_see_last_seen").checked,
          can_see_location: document.getElementById("can_see_location").checked,
        }),
      })
        .then(function () {
          form.reset();
          document.getElementById("can_see_last_seen").checked = true;
          document.getElementById("can_see_location").checked = true;
          showSuccess(str().inviteSent);
          return loadAll();
        })
        .catch(function (err) {
          showError(err.message || str().genericError);
        });
    });
  }

  loadSharing();
  loadAll();
})();
