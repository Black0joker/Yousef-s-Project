/* Theme bootstrap + toggle.
   Loaded in <head> (before first paint) so the saved theme is applied with no
   flash of the wrong colours. Falls back to the OS preference until the user
   picks a theme explicitly, then that choice is remembered. */
(function () {
  "use strict";

  var KEY = "th_theme";
  var root = document.documentElement;

  var BACKGROUND = { light: "#f8fafc", dark: "#020617" };

  function stored() {
    try {
      var value = localStorage.getItem(KEY);
      return value === "dark" || value === "light" ? value : null;
    } catch (err) {
      return null;
    }
  }

  function systemTheme() {
    try {
      return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
    } catch (err) {
      return "light";
    }
  }

  function current() {
    return root.classList.contains("dark") ? "dark" : "light";
  }

  function paint(theme) {
    root.classList.toggle("dark", theme === "dark");
    root.style.backgroundColor = BACKGROUND[theme] || BACKGROUND.light;
    root.style.colorScheme = theme; // native controls, scrollbars, form fields
  }

  function syncButtons() {
    var theme = current();
    var buttons = document.querySelectorAll("[data-theme-toggle]");

    for (var i = 0; i < buttons.length; i++) {
      var button = buttons[i];
      button.setAttribute(
        "aria-label",
        theme === "dark" ? "Switch to light mode" : "Switch to dark mode"
      );
      button.setAttribute("title", theme === "dark" ? "Light mode" : "Dark mode");

      toggle(button.querySelectorAll('[data-theme-icon="to-light"]'), theme === "dark");
      toggle(button.querySelectorAll('[data-theme-icon="to-dark"]'), theme !== "dark");
    }
  }

  function toggle(nodes, show) {
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].classList.toggle("hidden", !show);
    }
  }

  function set(theme) {
    paint(theme);
    try {
      localStorage.setItem(KEY, theme);
    } catch (err) {
      /* private mode / storage blocked: theme still applies for this page */
    }
    syncButtons();
  }

  window.THTheme = {
    current: current,
    set: set,
    toggle: function () {
      set(current() === "dark" ? "light" : "dark");
    },
    sync: syncButtons,
  };

  // Apply immediately, before the browser paints.
  paint(stored() || systemTheme());

  // Keep following the OS while the user has not chosen explicitly.
  try {
    var query = window.matchMedia("(prefers-color-scheme: dark)");
    var onSystemChange = function () {
      if (!stored()) paint(systemTheme());
    };
    if (query.addEventListener) query.addEventListener("change", onSystemChange);
    else if (query.addListener) query.addListener(onSystemChange);
  } catch (err) {
    /* matchMedia unavailable */
  }

  function wire() {
    syncButtons();
    document.addEventListener("click", function (event) {
      var button =
        event.target && event.target.closest
          ? event.target.closest("[data-theme-toggle]")
          : null;
      if (!button) return;
      event.preventDefault();
      window.THTheme.toggle();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
