/* Schedule page: today's appointments, upcoming items, and routines. The page
   shell is public; every call goes through the guarded API with the bearer
   token from session.js. Times are stored UTC and rendered in local time. */
(function () {
  "use strict";

  var Session = window.Session;
  if (!Session || !Session.requireSession()) return; // no valid token -> /login

  var LANG_KEY = "th_language";

  var STRINGS = {
    en: {
      todayTitle: "Today",
      todayHint: "Appointments SAGE knows about for your local day.",
      todayEmpty: "Nothing scheduled today.",
      upcomingTitle: "Upcoming",
      upcomingEmpty: "Nothing upcoming.",
      titleLabel: "Title",
      whenLabel: "When",
      placeLabel: "Place (optional)",
      add: "Add appointment",
      routinesTitle: "Routines",
      routinesHint: "Repeating parts of your day.",
      routinesEmpty: "No routines yet.",
      placesTitle: "Familiar places",
      placesHint: "Save where you are now, and SAGE can show what you noted about it.",
      placesEmpty: "No saved places yet.",
      placeNameLabel: "Place name",
      radiusLabel: "Radius (metres)",
      saveHere: "Save my current place",
      placeSaved: "Place saved.",
      confirmRemovePlace: "Remove this place?",
      locationOff: "Location is turned off in permissions.",
      locationDenied: "Your browser did not allow location access.",
      done: "Mark done",
      remove: "Remove",
      confirmRemove: "Remove this appointment?",
      confirmRemoveRoutine: "Remove this routine?",
      cancel: "Cancel",
      added: "Appointment added.",
      genericError: "Something went wrong. Please try again.",
      loadFailed: "Could not load your schedule.",
      required: "Please fill in the title and time.",
    },
    ar: {
      todayTitle: "اليوم",
      todayHint: "المواعيد التي يعرفها SAGE ليومك المحلي.",
      todayEmpty: "لا يوجد شيء مجدول اليوم.",
      upcomingTitle: "القادم",
      upcomingEmpty: "لا يوجد شيء قادم.",
      titleLabel: "العنوان",
      whenLabel: "الوقت",
      placeLabel: "المكان (اختياري)",
      add: "أضف موعدًا",
      routinesTitle: "الروتين",
      routinesHint: "أجزاء متكررة من يومك.",
      routinesEmpty: "لا يوجد روتين بعد.",
      placesTitle: "أماكن مألوفة",
      placesHint: "احفظ مكانك الحالي، ويستطيع SAGE أن يعرض ما دوّنته عنه.",
      placesEmpty: "لا توجد أماكن محفوظة بعد.",
      placeNameLabel: "اسم المكان",
      radiusLabel: "النطاق (بالمتر)",
      saveHere: "احفظ مكاني الحالي",
      placeSaved: "تم حفظ المكان.",
      confirmRemovePlace: "حذف هذا المكان؟",
      locationOff: "الموقع متوقف في الأذونات.",
      locationDenied: "لم يسمح متصفحك بالوصول إلى الموقع.",
      done: "تم",
      remove: "حذف",
      confirmRemove: "حذف هذا الموعد؟",
      confirmRemoveRoutine: "حذف هذا الروتين؟",
      cancel: "إلغاء",
      added: "تمت إضافة الموعد.",
      genericError: "حدث خطأ ما. حاول مرة أخرى.",
      loadFailed: "تعذر تحميل جدولك.",
      required: "يرجى إدخال العنوان والوقت.",
    },
  };

  var lang = "en";
  try {
    lang = localStorage.getItem(LANG_KEY) === "ar" ? "ar" : "en";
  } catch (err) {
    /* storage blocked; English is fine */
  }

  function str() {
    return STRINGS[lang] || STRINGS.en;
  }

  var errorEl = document.getElementById("schedule-error");
  var todayList = document.getElementById("today-list");
  var todayEmpty = document.getElementById("today-empty");
  var upcomingList = document.getElementById("upcoming-list");
  var upcomingEmpty = document.getElementById("upcoming-empty");
  var routineList = document.getElementById("routine-list");
  var routinesEmpty = document.getElementById("routines-empty");
  var placeList = document.getElementById("place-list");
  var placesEmpty = document.getElementById("places-empty");
  var placeForm = document.getElementById("place-form");
  var form = document.getElementById("appointment-form");

  function showError(message) {
    errorEl.textContent = message;
    errorEl.classList.remove("hidden");
  }

  function clearError() {
    errorEl.classList.add("hidden");
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function formatWhen(value) {
    if (!value) return "";
    var parsed = new Date(value);
    if (isNaN(parsed.getTime())) return value;
    return parsed.toLocaleString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function actionButton(label, onClick, danger) {
    var button = el(
      "button",
      "shrink-0 rounded-lg px-3 py-2 text-xs font-semibold transition-colors sm:px-2.5 sm:py-1 " +
        (danger
          ? "text-red-700 hover:bg-red-50 dark:text-red-300 dark:hover:bg-red-950/40"
          : "text-brand-700 hover:bg-brand-50 dark:text-brand-200 dark:hover:bg-brand-900/40"),
      label
    );
    button.type = "button";
    button.addEventListener("click", onClick);
    return button;
  }

  function appointmentRow(item, withDone) {
    var row = el(
      "li",
      "flex items-start justify-between gap-3 rounded-xl border border-sand-200 px-3.5 py-2.5 dark:border-white/10"
    );
    var body = el("div", "min-w-0");
    body.appendChild(el("p", "break-words text-sm text-brand-900 dark:text-sand-100", item.title));
    var meta = formatWhen(item.starts_at);
    if (item.place) meta += " · " + item.place;
    body.appendChild(el("p", "mt-0.5 text-xs text-brand-500 dark:text-sand-400", meta));
    row.appendChild(body);

    var actions = el("div", "flex shrink-0 gap-1");
    if (withDone) {
      actions.appendChild(
        actionButton(str().done, function () {
          updateStatus(item.id, "done");
        })
      );
    }
    actions.appendChild(
      actionButton(
        str().remove,
        function () {
          askConfirm(str().confirmRemove).then(function (confirmed) {
            if (!confirmed) return;
            remove("/api/appointments/" + encodeURIComponent(item.id), loadAll);
          });
        },
        true
      )
    );
    row.appendChild(actions);
    return row;
  }

  function updateStatus(id, status) {
    clearError();
    Session.authFetch("/api/appointments/" + encodeURIComponent(id), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: status }),
    })
      .then(function (response) {
        if (!response.ok) throw new Error(str().genericError);
        return loadAll();
      })
      .catch(function (err) {
        showError(err.message || str().genericError);
      });
  }

  function remove(url, onDone) {
    clearError();
    Session.authFetch(url, { method: "DELETE" })
      .then(function (response) {
        if (response.status !== 204) throw new Error(str().genericError);
        return onDone();
      })
      .catch(function (err) {
        showError(err.message || str().genericError);
      });
  }

  function askConfirm(message, confirmLabel) {
    if (window.THUI) {
      return window.THUI.confirm({
        title: message,
        confirmLabel: confirmLabel || str().remove,
        cancelLabel: str().cancel,
      });
    }
    return Promise.resolve(window.confirm(message));
  }

  function renderList(list, items, emptyEl, builder) {
    list.textContent = "";
    items.forEach(function (item) {
      list.appendChild(builder(item));
    });
    emptyEl.classList.toggle("hidden", items.length > 0);
  }

  // the browser's offset in minutes east of UTC, which is what the API expects
  function tzOffset() {
    return -new Date().getTimezoneOffset();
  }

  function loadToday() {
    return Session.authFetch("/api/appointments/today?tz_offset=" + tzOffset())
      .then(function (response) {
        if (!response.ok) throw new Error(str().loadFailed);
        return response.json();
      })
      .then(function (data) {
        renderList(todayList, data.appointments || [], todayEmpty, function (item) {
          return appointmentRow(item, true);
        });
      });
  }

  function loadUpcoming() {
    return Session.authFetch("/api/appointments")
      .then(function (response) {
        if (!response.ok) throw new Error(str().loadFailed);
        return response.json();
      })
      .then(function (data) {
        renderList(upcomingList, data.appointments || [], upcomingEmpty, function (item) {
          return appointmentRow(item, true);
        });
      });
  }

  function loadRoutines() {
    return Session.authFetch("/api/routines")
      .then(function (response) {
        if (!response.ok) throw new Error(str().loadFailed);
        return response.json();
      })
      .then(function (data) {
        var items = data.routines || [];
        routineList.textContent = "";
        items.forEach(function (routine) {
          var row = el(
            "li",
            "flex items-center justify-between gap-3 rounded-xl border border-sand-200 px-3.5 py-2.5 dark:border-white/10"
          );
          var body = el("div", "min-w-0");
          body.appendChild(
            el("p", "break-words text-sm text-brand-900 dark:text-sand-100", routine.label)
          );
          body.appendChild(
            el(
              "p",
              "mt-0.5 text-xs text-brand-500 dark:text-sand-400",
              routine.cadence + (routine.window ? " · " + routine.window : "")
            )
          );
          row.appendChild(body);
          row.appendChild(
            actionButton(
              str().remove,
              function () {
                askConfirm(str().confirmRemoveRoutine).then(function (confirmed) {
                  if (!confirmed) return;
                  remove("/api/routines/" + encodeURIComponent(routine.id), loadAll);
                });
              },
              true
            )
          );
          routineList.appendChild(row);
        });
        routinesEmpty.classList.toggle("hidden", items.length > 0);
      });
  }

  function loadAll() {
    if (window.THUI) {
      window.THUI.skeletons(todayList, 2);
      window.THUI.skeletons(upcomingList, 2);
      window.THUI.skeletons(routineList, 2);
      window.THUI.skeletons(placeList, 2);
    }
    return Promise.all([loadToday(), loadUpcoming(), loadRoutines(), loadPlaces()]).catch(
      function (err) {
        showError(err.message || str().loadFailed);
      }
    );
  }

  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      clearError();
      var title = document.getElementById("title").value.trim();
      var when = document.getElementById("starts_at").value;
      var place = document.getElementById("place").value.trim();
      if (!title || !when) {
        showError(str().required);
        return;
      }

      // datetime-local has no zone; the browser interprets it as local time
      var startsAt = new Date(when).toISOString();

      Session.authFetch("/api/appointments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title, starts_at: startsAt, place: place || null }),
      })
        .then(function (response) {
          if (!response.ok) throw new Error(str().genericError);
          form.reset();
          return loadAll();
        })
        .catch(function (err) {
          showError(err.message || str().genericError);
        });
    });
  }

  function loadPlaces() {
    return Session.authFetch("/api/places")
      .then(function (response) {
        if (!response.ok) throw new Error(str().loadFailed);
        return response.json();
      })
      .then(function (data) {
        var items = data.places || [];
        placeList.textContent = "";
        items.forEach(function (place) {
          var row = el(
            "li",
            "flex items-center justify-between gap-3 rounded-xl border border-sand-200 px-3.5 py-2.5 dark:border-white/10"
          );
          var body = el("div", "min-w-0");
          body.appendChild(
            el("p", "break-words text-sm text-brand-900 dark:text-sand-100", place.name)
          );
          body.appendChild(
            el(
              "p",
              "mt-0.5 text-xs text-brand-500 dark:text-sand-400",
              place.radius_m + " m" + (place.notes ? " · " + place.notes : "")
            )
          );
          row.appendChild(body);
          row.appendChild(
            actionButton(
              str().remove,
              function () {
                askConfirm(str().confirmRemovePlace).then(function (confirmed) {
                  if (!confirmed) return;
                  remove("/api/places/" + encodeURIComponent(place.id), loadAll);
                });
              },
              true
            )
          );
          placeList.appendChild(row);
        });
        placesEmpty.classList.toggle("hidden", items.length > 0);
      });
  }

  if (placeForm) {
    placeForm.addEventListener("submit", function (event) {
      event.preventDefault();
      clearError();

      var name = document.getElementById("place_name").value.trim();
      var radius = Number(document.getElementById("place_radius").value) || 150;
      if (!name) {
        showError(str().required);
        return;
      }
      if (!navigator.geolocation) {
        showError(str().locationDenied);
        return;
      }

      // The browser supplies the position; the model never invents one.
      navigator.geolocation.getCurrentPosition(
        function (position) {
          Session.authFetch("/api/places", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: name,
              latitude: position.coords.latitude,
              longitude: position.coords.longitude,
              radius_m: radius,
            }),
          })
            .then(function (response) {
              if (!response.ok) throw new Error(str().genericError);
              placeForm.reset();
              document.getElementById("place_radius").value = "150";
              return loadAll();
            })
            .catch(function (err) {
              showError(err.message || str().genericError);
            });
        },
        function () {
          showError(str().locationDenied);
        }
      );
    });
  }

  loadAll();
})();
