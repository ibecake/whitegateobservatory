/**
 * Shared gallery renderer. Accepts both a legacy JSON array and
 * { version, tags, items } manifests produced by admin.html.
 */
(function (global) {
  "use strict";

  function isAbsoluteUrl(value) {
    return /^(https?:)?\/\//i.test(value) || /^data:/i.test(value);
  }

  function resolveUrl(path, base) {
    if (!path) return "";
    if (isAbsoluteUrl(path)) return path;
    if (!base) return path;
    var normalizedBase = base.slice(-1) === "/" ? base : base + "/";
    return normalizedBase + String(path).replace(/^\/+/, "");
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function safeText(value) {
    return (value == null || value === "") ? "Unknown" : String(value);
  }

  function yearFromDate(dateValue) {
    var text = safeText(dateValue);
    var match = text.match(/^(\d{4})/);
    return match ? match[1] : "Unknown";
  }

  function itemFile(item) {
    return item.file || item.full || item.url || item.thumb || "";
  }

  function detailValue(item, key) {
    if (item[key] != null && item[key] !== "") return item[key];
    if (item.details && item.details[key] != null && item.details[key] !== "") return item.details[key];
    return "";
  }

  function normalize(data) {
    var items;
    var tags = [];
    var targets = [];
    if (Array.isArray(data)) {
      items = data;
    } else if (data && Array.isArray(data.items)) {
      items = data.items;
      tags = Array.isArray(data.tags) ? data.tags.slice() : [];
      targets = Array.isArray(data.targets) ? data.targets.slice() : [];
    } else {
      return null;
    }
    var seenTags = {};
    var seenTargets = {};
    tags.forEach(function (t) { if (t) seenTags[t] = true; });
    targets.forEach(function (t) { if (t) seenTargets[t] = true; });
    items.forEach(function (item) {
      (item.tags || []).forEach(function (t) { if (t) seenTags[t] = true; });
      if (item.target) seenTargets[item.target] = true;
    });
    return {
      items: items,
      tags: Object.keys(seenTags).sort(),
      targets: Object.keys(seenTargets).sort()
    };
  }

  function fillSelect(select, values, allLabel) {
    if (!select) return;
    var current = select.value;
    select.innerHTML = "";
    var all = document.createElement("option");
    all.value = "";
    all.textContent = allLabel;
    select.appendChild(all);
    values.forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
    if (current && values.indexOf(current) !== -1) select.value = current;
  }

  function mediaKind(item) {
    if (item.type) return item.type;
    var file = itemFile(item).toLowerCase();
    if (/\.(jpe?g|png|webp|gif|heic|heif)$/.test(file) || item.full || item.thumb) return "image";
    if (/\.(mp4|mov|m4v|webm)$/.test(file)) return "video";
    if (/\.(wav|mp3|ogg|flac|m4a)$/.test(file)) return "audio";
    if (/\.pdf$/.test(file)) return "document";
    return "other";
  }

  function mount(opts) {
    var statusEl = opts.statusEl;
    var gridEl = opts.gridEl;
    var filterTarget = opts.filterTarget;
    var filterYear = opts.filterYear;
    var filterTag = opts.filterTag;
    var lightbox = opts.lightbox;
    var lightboxClose = opts.lightboxClose;
    var lightboxMedia = opts.lightboxMedia;
    var lightboxPanel = opts.lightboxPanel;
    var sources = opts.sources || [];
    var mediaBase = opts.mediaBase || "";
    var emptyLabel = opts.emptyLabel || "No media listed in this manifest yet.";
    var items = [];
    var catalog = { tags: [], targets: [] };

    function updateStatus(message, isError) {
      if (!statusEl) return;
      statusEl.textContent = message;
      statusEl.style.display = "block";
      statusEl.style.borderColor = isError ? "#7f1d1d" : "#374151";
      statusEl.style.background = isError ? "#1f1012" : "#111827";
      statusEl.style.color = isError ? "#fecaca" : "#e5e7eb";
    }

    function openLightbox(item) {
      if (!lightbox || !lightboxMedia || !lightboxPanel) return;
      var kind = mediaKind(item);
      var src = resolveUrl(itemFile(item), mediaBase);
      lightboxMedia.innerHTML = "";
      if (kind === "video") {
        var video = document.createElement("video");
        video.className = "lightbox-video";
        video.controls = true;
        video.src = src;
        lightboxMedia.appendChild(video);
      } else if (kind === "audio") {
        var audio = document.createElement("audio");
        audio.className = "lightbox-audio";
        audio.controls = true;
        audio.src = src;
        lightboxMedia.appendChild(audio);
      } else if (kind === "image") {
        var img = document.createElement("img");
        img.className = "lightbox-image";
        img.alt = safeText(item.title);
        img.src = src;
        lightboxMedia.appendChild(img);
      } else {
        var link = document.createElement("p");
        link.innerHTML = '<a href="' + escapeHtml(src) + '" target="_blank" rel="noopener">Open file</a>';
        lightboxMedia.appendChild(link);
      }

      var tags = (item.tags || []).map(function (t) {
        return '<span class="gallery-tag">' + escapeHtml(t) + "</span>";
      }).join(" ");
      var camera = detailValue(item, "camera");
      var telescope = detailValue(item, "telescope");
      var integration = detailValue(item, "integration");
      var extra = "";
      if (item.details) {
        Object.keys(item.details).forEach(function (k) {
          if (["camera", "telescope", "integration"].indexOf(k) !== -1) return;
          extra += "<p><span class=\"label\">" + escapeHtml(k) + ":</span> " + escapeHtml(String(item.details[k])) + "</p>";
        });
      }
      lightboxPanel.innerHTML =
        "<h3>" + escapeHtml(safeText(item.title)) + "</h3>" +
        "<p><span class=\"label\">Type:</span> " + escapeHtml(kind) + "</p>" +
        "<p><span class=\"label\">Target:</span> " + escapeHtml(safeText(item.target)) + "</p>" +
        "<p><span class=\"label\">Date:</span> " + escapeHtml(safeText(item.date)) + "</p>" +
        (camera ? "<p><span class=\"label\">Camera:</span> " + escapeHtml(camera) + "</p>" : "") +
        (telescope ? "<p><span class=\"label\">Telescope:</span> " + escapeHtml(telescope) + "</p>" : "") +
        (integration ? "<p><span class=\"label\">Integration:</span> " + escapeHtml(integration) + "</p>" : "") +
        extra +
        (item.description ? "<p>" + escapeHtml(item.description) + "</p>" : "") +
        (tags ? "<div class=\"gallery-tags\">" + tags + "</div>" : "");
      lightbox.classList.add("open");
      lightbox.setAttribute("aria-hidden", "false");
    }

    function closeLightbox() {
      if (!lightbox) return;
      lightbox.classList.remove("open");
      lightbox.setAttribute("aria-hidden", "true");
      if (lightboxMedia) lightboxMedia.innerHTML = "";
      if (lightboxPanel) lightboxPanel.innerHTML = "";
    }

    function render(list) {
      gridEl.innerHTML = "";
      if (!list.length) {
        updateStatus("No items match the current filters.", false);
        return;
      }
      if (statusEl) statusEl.style.display = "none";
      var fragment = document.createDocumentFragment();
      list.forEach(function (item) {
        var card = document.createElement("article");
        card.className = "gallery-card";
        var button = document.createElement("button");
        button.type = "button";
        button.addEventListener("click", function () { openLightbox(item); });
        var kind = mediaKind(item);
        var thumbPath = item.thumb || (kind === "image" ? itemFile(item) : "");
        if (kind === "image" && thumbPath) {
          var thumb = document.createElement("img");
          thumb.className = "gallery-thumb";
          thumb.loading = "lazy";
          thumb.decoding = "async";
          thumb.src = resolveUrl(thumbPath, mediaBase);
          thumb.alt = safeText(item.title);
          button.appendChild(thumb);
        } else {
          var fallback = document.createElement("div");
          fallback.className = "gallery-thumb-fallback";
          fallback.textContent = kind;
          button.appendChild(fallback);
        }
        var meta = document.createElement("div");
        meta.className = "gallery-meta";
        var tagHtml = (item.tags || []).slice(0, 4).map(function (t) {
          return '<span class="gallery-tag">' + escapeHtml(t) + "</span>";
        }).join("");
        meta.innerHTML =
          "<h3>" + escapeHtml(safeText(item.title)) + "</h3>" +
          "<p>" + escapeHtml(safeText(item.target)) + " | " + escapeHtml(safeText(item.date)) + "</p>" +
          (tagHtml ? '<div class="gallery-tags">' + tagHtml + "</div>" : "");
        card.appendChild(button);
        card.appendChild(meta);
        fragment.appendChild(card);
      });
      gridEl.appendChild(fragment);
    }

    function applyFilters() {
      var targetFilter = filterTarget ? filterTarget.value : "";
      var yearFilter = filterYear ? filterYear.value : "";
      var tagFilter = filterTag ? filterTag.value : "";
      var filtered = items.filter(function (item) {
        if (targetFilter && safeText(item.target) !== targetFilter) return false;
        if (yearFilter && yearFromDate(item.date) !== yearFilter) return false;
        if (tagFilter && (item.tags || []).indexOf(tagFilter) === -1) return false;
        return true;
      });
      render(filtered);
    }

    function populateFilters() {
      var targets = Array.from(new Set(catalog.targets.concat(items.map(function (item) {
        return item.target ? String(item.target) : "";
      }).filter(Boolean)))).sort();
      var years = Array.from(new Set(items.map(function (item) { return yearFromDate(item.date); }))).sort().reverse();
      var tags = Array.from(new Set(catalog.tags.concat(items.reduce(function (acc, item) {
        return acc.concat(item.tags || []);
      }, [])))).sort();
      fillSelect(filterTarget, targets, "All targets");
      fillSelect(filterYear, years, "All years");
      fillSelect(filterTag, tags, "All tags");
    }

    async function load() {
      var payload = null;
      var sourceUsed = "";
      var errors = [];
      for (var i = 0; i < sources.length; i++) {
        var source = sources[i];
        try {
          var response = await fetch(source, { cache: "no-store" });
          if (!response.ok) {
            errors.push(source + " -> HTTP " + response.status);
            continue;
          }
          payload = await response.json();
          sourceUsed = source;
          break;
        } catch (err) {
          errors.push(source + " -> " + ((err && err.name) ? err.name : "FetchError"));
        }
      }
      var normalized = normalize(payload);
      if (!normalized) {
        var isFileProtocol = global.location && global.location.protocol === "file:";
        var redacted = errors.length ? errors.map(function (entry) {
          return entry.replace(/https?:\/\/[^\s]+/g, "[redacted-url]");
        }).join("; ") : "No source returned parseable JSON.";
        var help = isFileProtocol
          ? " Running from file:// blocks fetch in many browsers. Serve the site over http:// (for example: python3 -m http.server)."
          : "";
        updateStatus("Gallery manifest could not be loaded from known paths." + help + " Redacted errors: " + redacted, true);
        return;
      }
      items = normalized.items;
      catalog.tags = normalized.tags || [];
      catalog.targets = normalized.targets || [];
      if (!items.length) {
        updateStatus("Gallery manifest loaded from " + sourceUsed + ", but " + emptyLabel.toLowerCase(), false);
        return;
      }
      populateFilters();
      applyFilters();
    }

    if (filterTarget) filterTarget.addEventListener("change", applyFilters);
    if (filterYear) filterYear.addEventListener("change", applyFilters);
    if (filterTag) filterTag.addEventListener("change", applyFilters);
    if (lightboxClose) lightboxClose.addEventListener("click", closeLightbox);
    if (lightbox) {
      lightbox.addEventListener("click", function (event) {
        if (event.target === lightbox) closeLightbox();
      });
    }
    global.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && lightbox && lightbox.classList.contains("open")) closeLightbox();
    });

    load();
    return { reload: load, closeLightbox: closeLightbox };
  }

  global.WhitegateGallery = {
    normalize: normalize,
    resolveUrl: resolveUrl,
    mount: mount
  };
})(window);
