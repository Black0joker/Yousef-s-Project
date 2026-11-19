/* Location pings: while the chat or call page is open, send the browser's
   current position to /api/location every few seconds. The page shell is
   public; the request carries the bearer token from session.js. */
(function () {
  "use strict";

  var Session = window.Session;
  if (!Session || !Session.requireSession()) return; // no valid token -> /login
  if (!navigator.geolocation) return; // unsupported browser: stay silent

  var INTERVAL_MS = 5000;
  // GeolocationPositionError.PERMISSION_DENIED; the constant is not reliably
  // exposed on error instances across browsers, so compare the numeric code.
  var PERMISSION_DENIED = 1;
  var timer = null;
  var inFlight = false;
  var denied = false;

  var STRINGS = {
    en: { at: "You are at", next: "Next", off: "Location is off in permissions." },
    ar: { at: "أنت في", next: "التالي", off: "الموقع متوقف في الأذونات." },
  };

  var lang = "en";
  try {
    lang = localStorage.getItem("th_language") === "ar" ? "ar" : "en";
  } catch (err) {
    /* storage blocked */
  }
  var str = STRINGS[lang] || STRINGS.en;

  var banner = document.getElementById("place-context");
  var bannerText = document.getElementById("place-context-text");

  function showContext(context) {
    if (!banner || !bannerText) return;
    if (!context || !context.place) {
      banner.classList.add("hidden");
      return;
    }
    var parts = [str.at + " " + context.place.name];
    if (context.appointment) {
      var when = new Date(context.appointment.starts_at);
      if (!isNaN(when.getTime())) {
        parts.push(
          str.next + ": " + context.appointment.title + " " +
            when.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
        );
      }
    }
    bannerText.textContent = parts.join(" · ");
    banner.classList.remove("hidden");
  }

  function send(position) {
    if (inFlight) return; // a slow request must not pile up behind itself
    inFlight = true;
    Session.authFetch("/api/location", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
      }),
    })
      .then(function (response) {
        // 204 means "stored, no familiar place"; a 200 carries context
        if (response.status === 200) return response.json();
        return null;
      })
      .then(function (data) {
        showContext(data && data.context);
      })
      .catch(function () {
        /* transient failures are retried on the next tick */
      })
      .then(function () {
        inFlight = false;
      });
  }

  function tick() {
    if (denied || document.hidden) return;
    navigator.geolocation.getCurrentPosition(send, function (err) {
      // A denied permission is permanent for this page; retrying every 5s
      // would just spam the console and the browser's permission prompt.
      if (err && err.code === PERMISSION_DENIED) stop();
    });
  }

  function start() {
    if (timer !== null || denied) return;
    tick();
    timer = window.setInterval(tick, INTERVAL_MS);
  }

  function stop() {
    denied = true;
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    } else {
      start();
    }
  });

  window.addEventListener("pagehide", function () {
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
  });

  start();
})();
