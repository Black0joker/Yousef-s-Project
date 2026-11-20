/* Account settings: edit the profile (names + email) and change the password.
   The page shell is public; everything here goes through the guarded API with
   the bearer token from session.js. */
(function () {
  "use strict";

  var Session = window.Session;
  if (!Session || !Session.requireSession()) return;

  var LANG_KEY = "th_language";

  var STRINGS = {
    en: {
      pageTitle: "Settings",
      profileTitle: "Profile",
      profileHint: "The name SAGE uses with you, and the email you sign in with.",
      firstName: "First name",
      lastName: "Last name",
      email: "Email",
      currentPassword: "Current password",
      currentPasswordHint: "Required to change your email address.",
      saveProfile: "Save profile",
      passwordTitle: "Password",
      passwordHint: "Use at least 8 characters. Other signed-in devices stay signed in.",
      newPassword: "New password",
      confirmPassword: "Confirm new password",
      savePassword: "Update password",
      profileSaved: "Profile updated.",
      passwordSaved: "Password updated.",
      passwordMismatch: "The two passwords do not match.",
      genericError: "Something went wrong. Please try again.",
      busy: "Please wait…",
      toggleLang: "Switch language"
    },
    ar: {
      pageTitle: "الإعدادات",
      profileTitle: "الملف الشخصي",
      profileHint: "الاسم الذي يستخدمه SAGE معك، والبريد الذي تسجّل الدخول به.",
      firstName: "الاسم الأول",
      lastName: "اسم العائلة",
      email: "البريد الإلكتروني",
      currentPassword: "كلمة المرور الحالية",
      currentPasswordHint: "مطلوبة لتغيير بريدك الإلكتروني.",
      saveProfile: "حفظ الملف الشخصي",
      passwordTitle: "كلمة المرور",
      passwordHint: "استخدم 8 أحرف على الأقل. تبقى الأجهزة الأخرى مسجّلة الدخول.",
      newPassword: "كلمة المرور الجديدة",
      confirmPassword: "تأكيد كلمة المرور الجديدة",
      savePassword: "تحديث كلمة المرور",
      profileSaved: "تم تحديث الملف الشخصي.",
      passwordSaved: "تم تحديث كلمة المرور.",
      passwordMismatch: "كلمتا المرور غير متطابقتين.",
      genericError: "حدث خطأ ما. حاول مرة أخرى.",
      busy: "يرجى الانتظار…",
      toggleLang: "تغيير اللغة"
    }
  };

  function normalizeLang(value) {
    return value === "ar" ? "ar" : "en";
  }

  var lang = normalizeLang(localStorage.getItem(LANG_KEY) || "en");

  function str() {
    return STRINGS[lang];
  }

  var profileForm = document.getElementById("profile-form");
  var passwordForm = document.getElementById("password-form");
  var firstNameEl = document.getElementById("first_name");
  var lastNameEl = document.getElementById("last_name");
  var emailEl = document.getElementById("email");
  var profilePasswordRow = document.getElementById("profile-password-row");
  var profilePasswordEl = document.getElementById("profile_password");
  var profileError = document.getElementById("profile-error");
  var profileSuccess = document.getElementById("profile-success");
  var passwordError = document.getElementById("password-error");
  var passwordSuccess = document.getElementById("password-success");
  var langToggle = document.getElementById("lang-toggle");
  var langLabel = document.getElementById("lang-label");

  var originalEmail = "";

  // --- helpers ------------------------------------------------------------

  function show(el, message) {
    if (!el) return;
    if (el.getAttribute("role") === "status" && window.THUI) {
      window.THUI.showSuccess(el, message);
      return;
    }
    el.textContent = message;
    el.classList.remove("hidden");
  }

  function hide(el) {
    if (!el) return;
    el.textContent = "";
    el.classList.add("hidden");
  }

  function errorMessage(payload, status) {
    var err = payload && payload.error;
    if (err && err.message) return err.message;
    return str().genericError + " (" + status + ")";
  }

  function setBusy(form, busy) {
    var button = form ? form.querySelector('button[type="submit"]') : null;
    if (!button) return;
    if (busy) {
      button.dataset.label = button.textContent;
      button.textContent = str().busy;
    } else if (button.dataset.label) {
      button.textContent = button.dataset.label;
      delete button.dataset.label;
    }
    button.disabled = busy;
  }

  function readJson(response) {
    return response.json().catch(function () {
      return null;
    });
  }

  /* A 401 from these routes means the token is gone (redirect) unless the code
     is invalid_credentials, which is just a wrong current password. */
  function authFailure(response, data) {
    if (response.status !== 401) return false;
    var code = data && data.error && data.error.code;
    if (code === "invalid_credentials") return false;
    Session.clear();
    window.location.replace("/login");
    return true;
  }

  // --- language -----------------------------------------------------------

  function applyLanguage() {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
    document.body.classList.toggle("lang-ar", lang === "ar");

    var nodes = document.querySelectorAll("[data-i18n]");
    for (var i = 0; i < nodes.length; i++) {
      var key = nodes[i].getAttribute("data-i18n");
      if (str()[key]) nodes[i].textContent = str()[key];
    }
    if (langLabel) langLabel.textContent = lang === "ar" ? "AR" : "EN";
    if (langToggle) {
      langToggle.setAttribute("aria-label", str().toggleLang);
      langToggle.setAttribute("title", str().toggleLang);
    }
  }

  if (langToggle) {
    langToggle.addEventListener("click", function () {
      lang = lang === "ar" ? "en" : "ar";
      try {
        localStorage.setItem(LANG_KEY, lang);
      } catch (err) {
        /* storage blocked */
      }
      applyLanguage();
    });
  }

  // --- profile ------------------------------------------------------------

  function emailChanged() {
    return emailEl.value.trim().toLowerCase() !== originalEmail.toLowerCase();
  }

  function syncPasswordRow() {
    var changed = emailChanged();
    profilePasswordRow.classList.toggle("hidden", !changed);
    profilePasswordEl.required = changed;
    if (!changed) profilePasswordEl.value = "";
  }

  function fillProfile(user) {
    firstNameEl.value = user.first_name || "";
    lastNameEl.value = user.last_name || "";
    emailEl.value = user.email || "";
    originalEmail = user.email || "";
    syncPasswordRow();
  }

  if (emailEl) emailEl.addEventListener("input", syncPasswordRow);

  if (profileForm) {
    profileForm.addEventListener("submit", function (event) {
      event.preventDefault();
      hide(profileError);
      hide(profileSuccess);

      var payload = {
        first_name: firstNameEl.value,
        last_name: lastNameEl.value,
        email: emailEl.value
      };
      if (emailChanged()) payload.current_password = profilePasswordEl.value;

      setBusy(profileForm, true);
      Session.authFetch("/api/auth/profile", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        allowUnauthorized: true
      })
        .then(function (response) {
          return readJson(response).then(function (data) {
            if (authFailure(response, data)) return null;
            if (!response.ok) {
              show(profileError, errorMessage(data, response.status));
              return null;
            }
            return data;
          });
        })
        .then(function (data) {
          if (!data) return;
          Session.save({
            token: data.token,
            expires_at: data.expires_at,
            user: data.user
          });
          originalEmail = data.user.email || originalEmail;
          emailEl.value = originalEmail;
          syncPasswordRow();
          show(profileSuccess, str().profileSaved);
        })
        .catch(function () {
          show(profileError, str().genericError);
        })
        .then(function () {
          setBusy(profileForm, false);
        });
    });
  }

  // --- password -----------------------------------------------------------

  if (passwordForm) {
    passwordForm.addEventListener("submit", function (event) {
      event.preventDefault();
      hide(passwordError);
      hide(passwordSuccess);

      var payload = {
        current_password: document.getElementById("current_password").value,
        new_password: document.getElementById("new_password").value,
        confirm_password: document.getElementById("confirm_password").value
      };

      if (payload.new_password !== payload.confirm_password) {
        show(passwordError, str().passwordMismatch);
        return;
      }

      setBusy(passwordForm, true);
      Session.authFetch("/api/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        allowUnauthorized: true
      })
        .then(function (response) {
          if (response.ok) {
            passwordForm.reset();
            show(passwordSuccess, str().passwordSaved);
            return;
          }
          return readJson(response).then(function (data) {
            if (authFailure(response, data)) return;
            show(passwordError, errorMessage(data, response.status));
          });
        })
        .catch(function () {
          show(passwordError, str().genericError);
        })
        .then(function () {
          setBusy(passwordForm, false);
        });
    });
  }

  // --- boot ---------------------------------------------------------------

  applyLanguage();

  Session.authFetch("/api/auth/me")
    .then(function (response) {
      return response.ok ? response.json() : null;
    })
    .then(function (data) {
      if (data && data.user) fillProfile(data.user);
    })
    .catch(function () {
      /* the session guard already handled expired/invalid tokens */
    });
})();