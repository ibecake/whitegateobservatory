/**
 * Renders experiment / observation lists from JSON.
 * Expected shape: { experiments: [{ id, title, description, level, difficulty }] }
 */
(function (global) {
  "use strict";

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function difficultyClass(exp) {
    var d = String(exp.difficulty || "").toLowerCase();
    if (d === "beginner") return "easy";
    if (d === "intermediate") return "medium";
    if (d === "advanced") return "advanced";
    if (d === "expert") return "hard";
    var level = Number(exp.level) || 0;
    if (level <= 1) return "easy";
    if (level === 2) return "medium";
    if (level === 3) return "advanced";
    return "hard";
  }

  function matchesFilter(exp, filter) {
    if (!filter || filter === "all") return true;
    var d = String(exp.difficulty || "").toLowerCase();
    if (d && d === filter) return true;
    return difficultyClass(exp) === filter;
  }

  function renderList(listEl, experiments) {
    listEl.innerHTML = "";
    experiments.forEach(function (exp) {
      var tier = difficultyClass(exp);
      var article = document.createElement("article");
      article.className = "experiment-card";
      article.setAttribute("data-tier", tier);
      article.setAttribute("data-difficulty", String(exp.difficulty || "").toLowerCase());
      var levelLabel = exp.level != null ? "Level " + exp.level : "";
      var diffLabel = exp.difficulty || "";
      article.innerHTML =
        '<div class="experiment-head">' +
          '<span class="experiment-num">' + escapeHtml(exp.id) + "</span>" +
          "<h3>" + escapeHtml(exp.title) + "</h3>" +
          (levelLabel ? '<span class="level-pill">' + escapeHtml(levelLabel) + "</span>" : "") +
          (diffLabel ? '<span class="diff diff-' + escapeHtml(tier) + '">' + escapeHtml(diffLabel) + "</span>" : "") +
        "</div>" +
        "<p>" + escapeHtml(exp.description) + "</p>";
      listEl.appendChild(article);
    });
  }

  function applyFilter(listEl, buttons, filter) {
    buttons.forEach(function (b) {
      b.classList.toggle("active", (b.getAttribute("data-filter") || "all") === filter);
    });
    listEl.querySelectorAll(".experiment-card").forEach(function (card) {
      var dummy = {
        difficulty: card.getAttribute("data-difficulty"),
        level: 0
      };
      dummy.difficulty = card.getAttribute("data-difficulty");
      var show = filter === "all" ||
        card.getAttribute("data-difficulty") === filter ||
        card.getAttribute("data-tier") === filter;
      card.hidden = !show;
    });
  }

  function mount(opts) {
    var listEl = opts.listEl;
    var buttons = opts.buttons || [];
    var src = opts.src;
    if (!listEl || !src) return;

    fetch(src, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (data) {
      var experiments = (data && data.experiments) || [];
      renderList(listEl, experiments);
      var buttonList = buttons.length ? Array.prototype.slice.call(buttons) : [];
      buttonList.forEach(function (btn) {
        btn.addEventListener("click", function () {
          applyFilter(listEl, buttonList, btn.getAttribute("data-filter") || "all");
        });
      });
    }).catch(function () {
      listEl.innerHTML = "<p class=\"experiments-intro\">Could not load the observation list.</p>";
    });
  }

  global.WhitegateExperiments = { mount: mount, matchesFilter: matchesFilter };
})(window);
