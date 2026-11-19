/* In-app reminder: show today's next appointment in a banner on /chat.
   Deliberately in-app only — there is no push or email infrastructure, and the
   plan calls for the banner first. Silent on any failure: a reminder must never
   interfere with chatting. */
(function () {
  "use strict";

  var Session = window.Session;
  if (!Session || !Session.requireSession()) return;

  var banner = document.getElementById("schedule-reminder");
  var text = document.getElementById("schedule-reminder-text");
  if (!banner || !text) return;

  var STRINGS = {
    en: { next: "Next up", at: "at", today: "Today" },
    ar: { next: "التالي", at: "الساعة", today: "اليوم" },
  };

  var lang = "en";
  try {
    lang = localStorage.getItem("th_language") === "ar" ? "ar" : "en";
  } catch (err) {
    /* storage blocked */
  }
  var str = STRINGS[lang] || STRINGS.en;

  function formatTime(value) {
    var parsed = new Date(value);
    if (isNaN(parsed.getTime())) return "";
    return parsed.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }

  function render(items) {
    if (!items || !items.length) {
      banner.classList.add("hidden");
      return;
    }
    var next = items[0];
    var time = formatTime(next.starts_at);
    text.textContent =
      str.next + ": " + next.title + (time ? " " + str.at + " " + time : "");
    banner.classList.remove("hidden");
  }

  // the browser's offset in minutes east of UTC
  var offset = -new Date().getTimezoneOffset();

  Session.authFetch("/api/appointments/today?tz_offset=" + offset)
    .then(function (response) {
      if (!response.ok) return null;
      return response.json();
    })
    .then(function (data) {
      if (!data) return;
      var now = Date.now();
      var upcoming = (data.appointments || []).filter(function (item) {
        return new Date(item.starts_at).getTime() >= now - 60 * 60 * 1000;
      });
      render(upcoming);
    })
    .catch(function () {
      /* a missing reminder is not an error worth surfacing */
    });
})();
