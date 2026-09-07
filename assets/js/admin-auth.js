/**
 * Browser-only gate for media admin. This is not server authentication:
 * anyone can read the page source. It keeps the tools off-screen until a
 * matching username and password are entered in this tab.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "wg_admin_ok";
  var USERNAME = "whitegate";
  var PASS_SHA256 = "e172f87fba55110e2e151d3e7e0a226d781618bb370fa92ad083f5a730a532ca";
  var waiters = [];

  function isAuthed() {
    try {
      return sessionStorage.getItem(STORAGE_KEY) === "1";
    } catch (err) {
      return false;
    }
  }

  function setAuthed(on) {
    try {
      if (on) sessionStorage.setItem(STORAGE_KEY, "1");
      else sessionStorage.removeItem(STORAGE_KEY);
    } catch (err) {}
    document.documentElement.classList.toggle("admin-authed", !!on);
    document.documentElement.classList.toggle("admin-locked", !on);
  }

  function hexEq(a, b) {
    a = String(a || "");
    b = String(b || "");
    if (a.length !== b.length) return false;
    var d = 0;
    var i;
    for (i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
    return d === 0;
  }

  function sha256hex(text) {
    return crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)).then(function (buf) {
      return Array.from(new Uint8Array(buf)).map(function (b) {
        return ("0" + b.toString(16)).slice(-2);
      }).join("");
    });
  }

  function flushReady() {
    var cbs = waiters.splice(0, waiters.length);
    cbs.forEach(function (cb) { cb(); });
  }

  function signOut() {
    setAuthed(false);
    location.reload();
  }

  function bindForm() {
    var form = document.getElementById("loginForm");
    var userEl = document.getElementById("adminUser");
    var passEl = document.getElementById("adminPassword");
    var errEl = document.getElementById("loginError");
    var logoutBtn = document.getElementById("adminLogout");
    if (logoutBtn) logoutBtn.addEventListener("click", signOut);
    if (!form) return;
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var user = (userEl && userEl.value || "").trim();
      var pass = passEl ? passEl.value : "";
      if (errEl) errEl.textContent = "";
      if (!window.crypto || !crypto.subtle) {
        if (errEl) errEl.textContent = "This browser cannot hash a password here. Use HTTPS or localhost.";
        return;
      }
      sha256hex(user.toLowerCase() + "\0" + pass).then(function (hex) {
        if (hexEq(user.toLowerCase(), USERNAME) && hexEq(hex, PASS_SHA256)) {
          setAuthed(true);
          flushReady();
          if (passEl) passEl.value = "";
        } else if (errEl) {
          errEl.textContent = "Sign-in did not match. Check the username and password.";
        }
      }).catch(function () {
        if (errEl) errEl.textContent = "Could not check the password in this browser.";
      });
    });
    if (userEl) userEl.focus();
  }

  window.WhitegateAdminAuth = {
    isAuthed: isAuthed,
    onReady: function (cb) {
      if (isAuthed()) cb();
      else waiters.push(cb);
    },
    signOut: signOut
  };

  setAuthed(isAuthed());
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindForm);
  } else {
    bindForm();
  }
})();
