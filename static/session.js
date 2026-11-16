/* Session helpers: token storage in localStorage + a fetch wrapper that always
   sends the bearer token. */
(function () {
  "use strict";

  var TOKEN_KEY = "th_token";
  var EXPIRES_KEY = "th_expires_at";
  var USER_KEY = "th_user";

  function read(key) {
    try {
      return localStorage.getItem(key);
    } catch (err) {
      return null;
    }
  }

  function write(key, value) {
    try {
      if (value == null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (err) {
      /* storage blocked */
    }
  }

  function getToken() {
    return read(TOKEN_KEY);
  }

  function getExpiresAt() {
    return read(EXPIRES_KEY);
  }

  function getUser() {
    try {
      var raw = read(USER_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (err) {
      return null;
    }
  }

  function isExpired() {
    var expires = getExpiresAt();
    if (!expires) return false;
    var when = Date.parse(expires);
    return !isNaN(when) && when <= Date.now();
  }

  function isValid() {
    return !!getToken() && !isExpired();
  }

  function save(data) {
    if (data && data.token) write(TOKEN_KEY, data.token);
    if (data && data.expires_at) write(EXPIRES_KEY, data.expires_at);
    if (data && data.user) write(USER_KEY, JSON.stringify(data.user));
  }

  function clear() {
    write(TOKEN_KEY, null);
    write(EXPIRES_KEY, null);
    write(USER_KEY, null);
  }

  function requireSession() {
    if (!isValid()) {
      clear();
      window.location.replace("/login");
      return false;
    }
    return true;
  }

  /* fetch() with the Authorization header, logging out on 401. */
  function authFetch(url, options) {
    var opts = options || {};
    var headers = Object.assign({}, opts.headers || {});
    var token = getToken();
    if (token) headers.Authorization = "Bearer " + token;

    return fetch(url, Object.assign({}, opts, { headers: headers })).then(function (response) {
      if (response.status !== 401) return response;

      // let the caller handle its own 401 (e.g. the login form)
      if (opts.allowUnauthorized) return response;

      var hadToken = !!token;
      clear();
      if (hadToken) window.location.replace("/login");
      return response;
    });
  }

  window.Session = {
    getToken: getToken,
    getExpiresAt: getExpiresAt,
    getUser: getUser,
    isExpired: isExpired,
    isValid: isValid,
    save: save,
    clear: clear,
    requireSession: requireSession,
    authFetch: authFetch,
  };
})();
