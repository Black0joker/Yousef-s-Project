/* Login and registration forms: post JSON, store the token, go to the chat. */
(function () {
  "use strict";

  // already signed in (and the token has not expired)? skip the form
  if (window.Session && window.Session.isValid()) {
    window.location.replace("/chat");
    return;
  }

  var form = document.querySelector("[data-auth-form]");
  if (!form) return;

  var endpoint = form.getAttribute("data-endpoint");
  var errorEl = document.getElementById("form-error");
  var submitBtn = form.querySelector('button[type="submit"]');
  var submitLabel = submitBtn ? submitBtn.textContent.trim() : "";

  function showError(message) {
    if (!errorEl) return;
    errorEl.textContent = message;
    errorEl.classList.remove("hidden");
  }

  function clearError() {
    if (!errorEl) return;
    errorEl.textContent = "";
    errorEl.classList.add("hidden");
  }

  function setBusy(busy) {
    if (!submitBtn) return;
    submitBtn.disabled = busy;
    submitBtn.textContent = busy ? "Please wait…" : submitLabel;
  }

  function collect() {
    var payload = {};
    var fields = form.querySelectorAll("[data-field]");
    for (var i = 0; i < fields.length; i++) {
      payload[fields[i].getAttribute("data-field")] = fields[i].value;
    }
    return payload;
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    clearError();

    var payload = collect();

    if (payload.password && payload.password.length < 8) {
      showError("Password must be at least 8 characters.");
      return;
    }
    if (payload.confirm_password != null && payload.password !== payload.confirm_password) {
      showError("The two passwords do not match.");
      return;
    }

    setBusy(true);

    fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            return null;
          })
          .then(function (data) {
            if (!response.ok) {
              var err = data && data.error;
              throw new Error(err && err.message ? err.message : "Request failed (" + response.status + ")");
            }
            return data;
          });
      })
      .then(function (data) {
        window.Session.save(data);
        window.location.replace("/chat");
      })
      .catch(function (err) {
        showError(err.message);
        setBusy(false);
      });
  });
})();
