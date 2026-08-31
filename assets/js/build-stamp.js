/**
 * Renders a small footer with an optional "last updated" stamp from
 * build.json (written at deploy time) and a link to the media admin.
 */
(function () {
  "use strict";

  function two(n) { return (n < 10 ? "0" : "") + n; }

  function formatUtc(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso || "";
    return d.getUTCFullYear() + "-" + two(d.getUTCMonth() + 1) + "-" + two(d.getUTCDate()) +
      " " + two(d.getUTCHours()) + ":" + two(d.getUTCMinutes()) + " UTC";
  }

  function render(build) {
    build = build || {};
    if (document.getElementById("site-build-stamp")) return;

    var footer = document.createElement("footer");
    footer.id = "site-build-stamp";
    footer.style.cssText = [
      "margin-top:2rem",
      "padding:0.9rem 1rem",
      "border-top:1px solid #e5e7eb",
      "background:#f8f9fb",
      "color:#6b7280",
      "font:500 0.82rem/1.4 system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif",
      "text-align:center"
    ].join(";");

    if (build.built_at) {
      var label = document.createElement("span");
      label.textContent = "Site last updated " + formatUtc(build.built_at);
      footer.appendChild(label);
    }

    if (build.commit) {
      var sep = document.createElement("span");
      sep.textContent = "  \u00b7  ";
      sep.style.opacity = "0.6";
      footer.appendChild(sep);

      var sha = document.createElement("code");
      sha.textContent = build.commit;
      sha.style.cssText = "background:rgba(0,0,0,0.06);padding:0.05rem 0.35rem;border-radius:4px;font-size:0.78rem;";

      if (build.repo_url) {
        var link = document.createElement("a");
        link.href = build.repo_url.replace(/\/$/, "") + "/commit/" + build.commit;
        link.target = "_blank";
        link.rel = "noopener";
        link.style.cssText = "color:inherit;text-decoration:none;";
        link.appendChild(sha);
        footer.appendChild(link);
      } else {
        footer.appendChild(sha);
      }
    }

    var admin = document.createElement("a");
    admin.href = "admin.html";
    admin.textContent = "Media admin";
    admin.style.cssText = "color:inherit;text-decoration:none;";
    if (footer.childNodes.length) {
      var adminSep = document.createElement("span");
      adminSep.textContent = "  \u00b7  ";
      adminSep.style.opacity = "0.6";
      footer.appendChild(adminSep);
    }
    footer.appendChild(admin);

    document.body.appendChild(footer);
  }

  function load() {
    fetch("build.json?_=" + Date.now(), { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (build) { render(build); })
      .catch(function () { render({}); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();
