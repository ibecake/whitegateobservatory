/**
 * Renders a small "last updated" footer using build.json, which the Pages
 * workflow writes at deploy time. If build.json is missing (e.g. local preview
 * before a build), the footer is silently skipped.
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
    if (!build || !build.built_at) return;
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

    var label = document.createElement("span");
    label.textContent = "Site last updated " + formatUtc(build.built_at);
    footer.appendChild(label);

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

    document.body.appendChild(footer);
  }

  function load() {
    fetch("build.json?_=" + Date.now(), { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(render)
      .catch(function () { /* no build stamp available; ignore */ });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();
