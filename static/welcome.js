/* Welcome page: save the user's name in localStorage, then go to /chat. */
(function () {
  "use strict";

  var NAME_KEY = "th_chat_name";

  var form = document.getElementById("name-form");
  var input = document.getElementById("name-input");
  var cont = document.getElementById("continue");
  var savedNameEl = document.getElementById("saved-name");
  var continueLink = document.getElementById("continue-link");
  var resetLink = document.getElementById("reset-name");

  var saved = (localStorage.getItem(NAME_KEY) || "").trim();
  if (saved) {
    input.value = saved;
    savedNameEl.textContent = saved;
    cont.classList.remove("hidden");
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var name = input.value.trim();
    if (!name) {
      input.focus();
      return;
    }
    localStorage.setItem(NAME_KEY, name);
    window.location.href = "/chat";
  });

  continueLink.addEventListener("click", function (event) {
    event.preventDefault();
    var name = (input.value || localStorage.getItem(NAME_KEY) || "").trim();
    if (!name) {
      input.focus();
      return;
    }
    localStorage.setItem(NAME_KEY, name);
    window.location.href = "/chat";
  });

  resetLink.addEventListener("click", function (event) {
    event.preventDefault();
    localStorage.removeItem(NAME_KEY);
    cont.classList.add("hidden");
    input.value = "";
    input.focus();
  });
})();
