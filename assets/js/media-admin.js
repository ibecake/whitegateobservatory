/**
 * Whitegate Observatory media admin.
 * Edits per-section manifest.json files, tags entries, extracts file metadata,
 * and optionally uploads objects to the matching GCS bucket.
 */
(function () {
  "use strict";

  var COLLECTIONS = {
    astrophotography: {
      label: "Astrophotography",
      bucket: "whitegate-astrophotography",
      astroCompat: true,
      saveTo: "astrophoto/manifest.json",
      page: "astro-photography.html"
    },
    astroradio: {
      label: "Radio Astronomy",
      bucket: "whitegate-astroradio",
      saveTo: "astroradio/manifest.json",
      page: "radio-astronomy.html"
    },
    radio: {
      label: "Radio",
      bucket: "whitegate-radio",
      saveTo: "radio/manifest.json",
      page: "radio.html"
    }
  };

  var GITHUB = {
    owner: "ibecake",
    repo: "whitegateobservatory",
    branch: "main"
  };

  var DETAIL_TEMPLATES = {
    image: ["camera", "telescope", "integration", "filter", "exposure"],
    video: ["duration", "resolution", "equipment"],
    audio: ["frequency", "mode", "duration", "equipment"],
    document: ["format", "source"],
    data: ["format", "source", "instrument"],
    other: []
  };
  var TYPES = ["image", "video", "audio", "document", "data", "other"];

  var uid = 1;
  var state = {
    collectionKey: "astrophotography",
    entries: [],
    tagCatalog: [],
    targetCatalog: [],
    knownTags: {},
    knownTargets: {},
    filterTag: "",
    filterText: "",
    gcsToken: sessionStorage.getItem("wg_gcs_token") || "",
    githubToken: sessionStorage.getItem("wg_gh_token") || "",
    bucketItems: [],
    dirty: false,
    hideHeic: true,
    onlyNew: true,
    removedFiles: {},
    loadedItemFiles: {},
    loadSource: "",
    removedTags: {},
    removedTargets: {},
    saveInFlight: false
  };

  var el = {};

  function $(id) { return document.getElementById(id); }

  function currentCollection() { return COLLECTIONS[state.collectionKey]; }
  function currentBucket() { return currentCollection().bucket; }
  function baseUrl() {
    var b = currentBucket();
    return b ? "https://storage.googleapis.com/" + b + "/" : "";
  }
  function isAbsoluteUrl(v) { return /^(https?:)?\/\//i.test(v) || /^data:/i.test(v); }
  function resolveUrl(path, base) {
    if (!path) return "";
    if (isAbsoluteUrl(path)) return path;
    var nb = base && base.slice(-1) === "/" ? base : base + "/";
    return nb + String(path).replace(/^\/+/, "");
  }
  function typeFromContentType(ct, name) {
    ct = (ct || "").toLowerCase();
    var n = (name || "").toLowerCase();
    if (ct.indexOf("image/") === 0 || /\.(jpe?g|png|webp|gif|heic|heif|tif{1,2})$/.test(n)) return "image";
    if (ct.indexOf("video/") === 0 || /\.(mp4|mov|m4v|webm)$/.test(n)) return "video";
    if (ct.indexOf("audio/") === 0 || /\.(wav|mp3|ogg|flac|m4a)$/.test(n)) return "audio";
    if (ct === "application/pdf" || /\.pdf$/.test(n)) return "document";
    if (ct.indexOf("text/") === 0 || /json|csv/.test(ct) || /\.(json|csv|txt|fits)$/.test(n)) return "data";
    return "other";
  }
  function humanSize(bytes) {
    bytes = Number(bytes) || 0;
    var u = ["B", "KB", "MB", "GB"];
    var i = 0;
    while (bytes >= 1024 && i < u.length - 1) { bytes /= 1024; i++; }
    return (i === 0 ? String(bytes) : bytes.toFixed(1)) + " " + u[i];
  }
  function setStatus(node, msg, kind) {
    node.className = "status show " + (kind || "");
    node.innerHTML = msg;
  }
  function clearStatus(node) { node.className = "status"; node.innerHTML = ""; }
  function escapeHtml(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function sanitizeObjectName(name) {
    name = String(name || "").replace(/\\/g, "/").split("/").pop();
    name = name.replace(/[^\w.\-()+ ]+/g, "_").replace(/\s+/g, "_");
    return name || ("upload_" + Date.now());
  }
  function uniqueObjectName(name) {
    var base = sanitizeObjectName(name);
    var used = {};
    state.entries.forEach(function (e) { if (e.file) used[e.file] = true; });
    (state.bucketItems || []).forEach(function (it) { if (it.name) used[it.name] = true; });
    if (!used[base]) return base;
    var dot = base.lastIndexOf(".");
    var stem = dot > 0 ? base.slice(0, dot) : base;
    var ext = dot > 0 ? base.slice(dot) : "";
    var n = 2;
    while (used[stem + "-" + n + ext]) n++;
    return stem + "-" + n + ext;
  }

  function newEntry(seed) {
    seed = seed || {};
    return {
      id: uid++,
      type: seed.type || "image",
      title: seed.title || "",
      date: seed.date || "",
      target: seed.target || "",
      tags: (seed.tags || []).slice(),
      description: seed.description || "",
      file: seed.file || "",
      thumb: seed.thumb || "",
      details: (seed.details || []).slice(),
      localFile: seed.localFile || null,
      objectUrl: seed.objectUrl || "",
      exif: seed.exif || null,
      pendingUpload: !!seed.pendingUpload,
      selected: false,
      fileMeta: seed.fileMeta || null,
      thumbLocalFile: seed.thumbLocalFile || null,
      thumbObjectUrl: seed.thumbObjectUrl || "",
      pendingThumbUpload: !!seed.pendingThumbUpload,
      videoObjectUrl: seed.videoObjectUrl || "",
      autoThumbDone: !!seed.autoThumbDone
    };
  }

  function registerTags(tags) {
    (tags || []).forEach(function (t) {
      if (!t) return;
      if (state.removedTags[t]) return;
      state.knownTags[t] = true;
      if (state.tagCatalog.indexOf(t) === -1) state.tagCatalog.push(t);
    });
  }

  function registerTargets(targets) {
    (targets || []).forEach(function (t) {
      t = String(t || "").trim();
      if (!t) return;
      if (state.removedTargets[t]) return;
      state.knownTargets[t] = true;
      if (state.targetCatalog.indexOf(t) === -1) state.targetCatalog.push(t);
    });
  }

  function entryKey(entry) {
    return (entry && (entry.file || entry.thumb)) || "";
  }

  function recordLoadedFiles() {
    state.loadedItemFiles = {};
    state.removedFiles = {};
    state.entries.forEach(function (e) {
      var k = entryKey(e);
      if (k) state.loadedItemFiles[k] = true;
    });
  }

  function isListedOnGithub(entry) {
    var k = entryKey(entry);
    return !!(k && state.loadedItemFiles[k]);
  }

  function resetCollectionState() {
    state.entries.forEach(revokeMediaUrls);
    state.entries = [];
    state.tagCatalog = [];
    state.targetCatalog = [];
    state.knownTags = {};
    state.knownTargets = {};
    state.filterTag = "";
    state.filterText = "";
    state.bucketItems = [];
    state.dirty = false;
    state.removedFiles = {};
    state.loadedItemFiles = {};
    state.loadSource = "";
    state.removedTags = {};
    state.removedTargets = {};
  }

  function markDirty() {
    state.dirty = true;
    updateOutput();
  }

  function updateSaveBar() {
    if (!el.saveBarStatus) return;
    var listed = state.entries.filter(isListedOnGithub).length;
    var neu = state.entries.length - listed;
    if (state.dirty) {
      el.saveBarStatus.textContent = "Unsaved — " + state.entries.length + " item(s) in this list" +
        (neu ? " (" + neu + " not on the live gallery yet)" : "") + ". Click Save to the website.";
      el.saveBarStatus.className = "save-bar-status dirty";
    } else {
      el.saveBarStatus.textContent = state.entries.length + " item(s) on this gallery. No unsaved changes.";
      el.saveBarStatus.className = "save-bar-status";
    }
  }

  function isHeicName(name) {
    return /\.hei[cf]$/i.test(name || "");
  }
  function isWebImageName(name) {
    return /\.(jpe?g|png|webp|gif)$/i.test(name || "");
  }
  function preferWebObject(it) {
    if (!it || !isHeicName(it.name)) return it;
    var stem = it.name.replace(/\.hei[cf]$/i, "");
    var match = null;
    (state.bucketItems || []).forEach(function (other) {
      var n = other.name || "";
      if (n === stem + ".jpg" || n === stem + ".JPG" || n === stem + ".jpeg" || n === stem + ".webp" || n === stem + ".png") {
        match = other;
      }
    });
    return match || it;
  }

  function tokenHeaders() {
    var h = {};
    if (state.gcsToken) h.Authorization = "Bearer " + state.gcsToken;
    return h;
  }

  // ---- collection UI ----
  function renderSectionTabs() {
    var wrap = el.sectionTabs;
    wrap.innerHTML = "";
    Object.keys(COLLECTIONS).forEach(function (k) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "section-tab" + (k === state.collectionKey ? " active" : "");
      b.textContent = COLLECTIONS[k].label;
      b.addEventListener("click", function () { switchCollection(k); });
      wrap.appendChild(b);
    });
  }

  function switchCollection(key) {
    if (key === state.collectionKey) return;
    if (state.saveInFlight) {
      if (el.saveBarStatus) {
        el.saveBarStatus.textContent = "Wait for the current save to finish before switching galleries.";
        el.saveBarStatus.className = "save-bar-status dirty";
      }
      return;
    }
    if (state.dirty && !confirm("Discard unsaved edits for this section?")) return;
    state.collectionKey = key;
    resetCollectionState();
    el.fileList.style.display = "none";
    el.fileList.innerHTML = "";
    el.fileCount.textContent = "";
    clearStatus(el.listStatus);
    if (el.saveConfirm) {
      el.saveConfirm.className = "status";
      el.saveConfirm.innerHTML = "";
    }
    renderSectionTabs();
    refreshBase();
    renderTagLibrary();
    renderTargetLibrary();
    renderEntries();
    updateOutput();
    loadManifestFromRepo().then(function () { listObjects(); });
  }

  function refreshBase() {
    var c = currentCollection();
    if (el.baseUrl) el.baseUrl.value = baseUrl();
    if (el.saveHint) {
      el.saveHint.textContent = "Gallery page: " + c.page + "  ·  list file: " + c.saveTo + "  ·  bucket: " + c.bucket;
    }
    if (el.importUrl) {
      el.importUrl.value = c.saveTo;
      el.importUrl.placeholder = c.saveTo;
    }
    if (el.savePathLabel) el.savePathLabel.textContent = c.saveTo;
    if (el.previewGalleryLink) {
      el.previewGalleryLink.href = c.page;
      el.previewGalleryLink.textContent = "Open " + c.label + " gallery";
    }
  }

  // ---- tags ----
  function renderTagLibrary() {
    var box = el.tagLibrary;
    if (!box) return;
    box.innerHTML = "";
    if (!state.tagCatalog.length) {
      var empty = document.createElement("span");
      empty.className = "muted-inline";
      empty.textContent = "No tags yet. Create one below or add tags on an entry.";
      box.appendChild(empty);
    }
    state.tagCatalog.forEach(function (t) {
      var count = state.entries.filter(function (e) { return e.tags.indexOf(t) !== -1; }).length;
      var chip = document.createElement("span");
      chip.className = "chip" + (state.filterTag === t ? " active" : "");
      chip.appendChild(document.createTextNode(t + " (" + count + ")"));
      chip.title = "Filter by this tag";
      chip.style.cursor = "pointer";
      chip.addEventListener("click", function (ev) {
        if (ev.target.closest && ev.target.closest("button")) return;
        state.filterTag = state.filterTag === t ? "" : t;
        renderTagLibrary();
        renderEntries();
      });
      var x = document.createElement("button");
      x.type = "button";
      x.textContent = "×";
      x.title = "Remove tag from library and all entries";
      x.addEventListener("click", function (ev) {
        ev.stopPropagation();
        if (!confirm("Remove tag “" + t + "” from the library and every entry?")) return;
        state.tagCatalog = state.tagCatalog.filter(function (x) { return x !== t; });
        delete state.knownTags[t];
        state.entries.forEach(function (e) {
          e.tags = e.tags.filter(function (x) { return x !== t; });
        });
        if (state.filterTag === t) state.filterTag = "";
        state.removedTags[t] = true;
        renderTagLibrary();
        renderEntries();
        markDirty();
      });
      chip.appendChild(x);
      box.appendChild(chip);
    });
    refreshTagSuggestions();
    refreshTargetSuggestions();
  }

  function renderTargetLibrary() {
    var box = el.targetLibrary;
    if (!box) return;
    box.innerHTML = "";
    if (!state.targetCatalog.length) {
      var empty = document.createElement("span");
      empty.className = "muted-inline";
      empty.textContent = "No targets yet. Create one or fill Target on a card.";
      box.appendChild(empty);
    }
    state.targetCatalog.forEach(function (t) {
      var count = state.entries.filter(function (e) { return e.target === t; }).length;
      var chip = document.createElement("span");
      chip.className = "chip";
      chip.appendChild(document.createTextNode(t + " (" + count + ")"));
      chip.title = "Target stays in the library until you remove it";
      var x = document.createElement("button");
      x.type = "button";
      x.textContent = "×";
      x.title = "Remove target from library (does not delete files)";
      x.addEventListener("click", function (ev) {
        ev.stopPropagation();
        if (!confirm("Remove target “" + t + "” from the library? Cards using it will keep the text until you change them.")) return;
        state.targetCatalog = state.targetCatalog.filter(function (x) { return x !== t; });
        delete state.knownTargets[t];
        state.removedTargets[t] = true;
        renderTargetLibrary();
        markDirty();
      });
      chip.appendChild(x);
      box.appendChild(chip);
    });
    refreshTargetSuggestions();
  }

  function addCatalogTag(raw) {
    raw.split(",").forEach(function (part) {
      var t = part.trim();
      if (!t) return;
      delete state.removedTags[t];
      registerTags([t]);
    });
    renderTagLibrary();
    markDirty();
  }

  function addCatalogTarget(raw) {
    raw.split(",").forEach(function (part) {
      var t = part.trim();
      if (!t) return;
      delete state.removedTargets[t];
      registerTargets([t]);
    });
    renderTargetLibrary();
    markDirty();
  }

  // ---- bucket listing ----
  function listObjects() {
    var bucket = currentBucket();
    setStatus(el.listStatus, "Loading objects from <code>" + escapeHtml(bucket) + "</code>…", "");
    el.loadFilesBtn.disabled = true;
    var items = [];
    function page(token) {
      var url = "https://storage.googleapis.com/storage/v1/b/" + encodeURIComponent(bucket) +
        "/o?fields=items(name,contentType,size,timeCreated,updated,metadata),nextPageToken&maxResults=1000" +
        (token ? "&pageToken=" + encodeURIComponent(token) : "");
      return fetch(url, { cache: "no-store", headers: tokenHeaders() }).then(function (r) {
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (j) { throw { status: r.status, body: j }; });
        }
        return r.json();
      }).then(function (data) {
        (data.items || []).forEach(function (it) { items.push(it); });
        if (data.nextPageToken) return page(data.nextPageToken);
      });
    }
    page(null).then(function () {
      el.loadFilesBtn.disabled = false;
      items = items.filter(function (it) { return it.name && it.name.slice(-1) !== "/"; });
      state.bucketItems = items;
      renderFileList();
      var newWeb = items.filter(function (it) { return !isAdded(it.name) && isWebImageName(it.name); });
      if (!items.length) {
        setStatus(el.listStatus, "Bucket is reachable but contains no objects. Type a filename below if you know it.", "warn");
      } else if (newWeb.length) {
        setStatus(el.listStatus, "Found " + items.length + " object(s). <strong>" + newWeb.length +
          " JPEG/PNG/WebP file(s) are not on the page yet</strong> — click Add to page, or Add all new JPEG/PNG/WebP.", "ok");
      } else {
        setStatus(el.listStatus, "Loaded " + items.length + " object(s). Every web image in this bucket is already on the page (HEIC files are hidden by default because browsers cannot display them).", "ok");
      }
    }).catch(function (e) {
      el.loadFilesBtn.disabled = false;
      var code = e && e.status;
      if (code === 401 || code === 403) {
        setStatus(el.listStatus,
          "This bucket is not publicly listable (HTTP " + code + "). Type the <strong>exact filename</strong> below (for example <code>IMG_2756.jpg</code>) and click Add this filename.<br>" +
          "To make listing work, the bucket owner can run:<br>" +
          "<pre style='margin:.5rem 0 0'>gcloud storage buckets add-iam-policy-binding gs://" + escapeHtml(bucket) +
          " \\\n  --member=allUsers --role=roles/storage.objectViewer</pre>", "err");
      } else {
        setStatus(el.listStatus, "Could not list bucket (" + escapeHtml(String(code || (e && e.message) || "network error")) +
          "). Type the exact filename below instead.", "err");
      }
    });
  }

  function isAdded(name) {
    return state.entries.some(function (en) { return en.file === name || en.thumb === name; });
  }

  function visibleBucketItems() {
    var items = (state.bucketItems || []).slice();
    if (state.hideHeic) items = items.filter(function (it) { return !isHeicName(it.name); });
    if (state.onlyNew) items = items.filter(function (it) { return !isAdded(it.name); });
    items.sort(function (a, b) { return a.name < b.name ? -1 : a.name > b.name ? 1 : 0; });
    return items;
  }

  function renderFileList() {
    var items = visibleBucketItems();
    el.fileList.style.display = "";
    el.fileList.innerHTML = "";
    var hidden = (state.bucketItems || []).length - items.length;
    el.fileCount.textContent = items.length + " shown" + (hidden ? " (" + hidden + " hidden)" : "");
    if (!items.length) {
      var empty = document.createElement("div");
      empty.className = "fileitem";
      empty.innerHTML = '<span class="name">Nothing to show with the current filters.</span>';
      el.fileList.appendChild(empty);
      return;
    }
    items.forEach(function (it) {
      var type = typeFromContentType(it.contentType, it.name);
      var heic = isHeicName(it.name);
      var div = document.createElement("div");
      div.className = "fileitem" + (isAdded(it.name) ? " added" : "");
      div.innerHTML =
        '<span class="name">' + escapeHtml(it.name) + "</span>" +
        '<span class="badge">' + escapeHtml(type) + "</span>" +
        '<span class="size">' + humanSize(it.size) + "</span>";
      var btn = document.createElement("button");
      btn.className = "small primary";
      if (isAdded(it.name)) {
        btn.textContent = "On gallery";
        btn.disabled = true;
        div.appendChild(btn);
        var rm = document.createElement("button");
        rm.className = "small danger";
        rm.textContent = "Remove from gallery";
        rm.addEventListener("click", function () {
          removeByFilename(it.name);
        });
        div.appendChild(rm);
      } else {
        btn.textContent = "Add to page";
        btn.addEventListener("click", function () {
          addFromBucketObject(it);
          renderFileList();
        });
        div.appendChild(btn);
      }
      if (heic) {
        var warn = document.createElement("span");
        warn.className = "badge warn";
        warn.title = "Browsers cannot display HEIC. Add the .jpg of the same name instead.";
        warn.textContent = "won't display";
        div.insertBefore(warn, btn);
      }
      el.fileList.appendChild(div);
    });
  }

  function removeByFilename(name) {
    var index = -1;
    state.entries.forEach(function (e, i) {
      if (e.file === name || e.thumb === name) index = i;
    });
    if (index < 0) return;
    var entry = state.entries[index];
    if (!confirm("Remove “" + (entry.file || name) + "” from the gallery list? The file stays in the bucket. Save to publish this removal.")) return;
    revokeMediaUrls(entry);
    var k = entryKey(entry);
    if (k) state.removedFiles[k] = true;
    state.entries.splice(index, 1);
    renderTagLibrary();
    renderTargetLibrary();
    renderEntries();
    renderFileList();
    markDirty();
  }

  function addFromBucketObject(it) {
    var chosen = preferWebObject(it);
    if (chosen !== it && chosen && chosen.name !== it.name) {
      setStatus(el.listStatus, "Added <code>" + escapeHtml(chosen.name) + "</code> (web version of " +
        escapeHtml(it.name) + "). HEIC does not display in browsers.", "ok");
      it = chosen;
    } else if (isHeicName(it.name)) {
      if (!confirm(it.name + " is HEIC. Browsers cannot show it on the gallery. Add it anyway? If a .jpg with the same name exists, use that instead.")) {
        return;
      }
    }
    if (isAdded(it.name)) {
      setStatus(el.listStatus, escapeHtml(it.name) + " is already on the page.", "warn");
      return;
    }
    var type = typeFromContentType(it.contentType, it.name);
    var meta = it.metadata || {};
    var details = [];
    Object.keys(meta).forEach(function (k) {
      if (meta[k]) details.push({ key: k, value: String(meta[k]) });
    });
    var date = meta.capturedAt || meta.date || "";
    if (date.length > 10) date = date.slice(0, 10);
    if (!date && it.timeCreated) date = String(it.timeCreated).slice(0, 10);
    var tags = [];
    if (meta.tags) tags = String(meta.tags).split(",").map(function (t) { return t.trim(); }).filter(Boolean);
    tags = tags.concat(window.MediaExif ? window.MediaExif.suggestFromFilename(it.name) : []);
    registerTags(tags);
    if (meta.target) registerTargets([meta.target]);
    addEntry({
      file: it.name,
      thumb: type === "image" && !isHeicName(it.name) ? it.name : "",
      type: type,
      date: date,
      target: meta.target || "",
      title: meta.title || it.name.replace(/\.[a-z0-9]+$/i, "").replace(/[_\-]+/g, " "),
      camera: meta.camera,
      tags: tags,
      details: details,
      fileMeta: { contentType: it.contentType, size: Number(it.size) || 0, timeCreated: it.timeCreated || "" }
    });
    tryReadRemoteExif(it.name, type);
    var card = el.entries && el.entries.lastElementChild;
    if (card && card.scrollIntoView) card.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function addAllNewWebFiles() {
    var added = 0;
    (state.bucketItems || []).forEach(function (it) {
      if (isAdded(it.name)) return;
      if (!isWebImageName(it.name)) return;
      addFromBucketObject(it);
      added++;
    });
    renderFileList();
    if (!added) setStatus(el.listStatus, "No new JPEG/PNG/WebP files to add.", "warn");
    else setStatus(el.listStatus, "Added " + added + " new web image(s) to this list. They are not on the public gallery until you Save to the website.", "ok");
  }

  function tryReadRemoteExif(objectName, type) {
    if (type !== "image") return;
    if (isHeicName(objectName)) return;
    if (!/\.(jpe?g|png|webp|gif)$/i.test(objectName)) return;
    var url = resolveUrl(objectName, baseUrl());
    fetch(url, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.arrayBuffer();
    }).then(function (buf) {
      if (!window.MediaExif) return;
      var parsed = window.MediaExif.readBuffer(buf, "image/jpeg", objectName);
      var entry = state.entries.filter(function (e) { return e.file === objectName; })[0];
      if (!entry || !parsed) return;
      applyExifToEntry(entry, parsed, { overwriteEmpty: true });
      renderEntries();
      markDirty();
    }).catch(function () { /* CORS likely; ignore */ });
  }

  // ---- local uploads ----
  function handleFiles(fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return;
    var chain = Promise.resolve();
    files.forEach(function (file) {
      chain = chain.then(function () { return ingestLocalFile(file); });
    });
    chain.then(function () {
      setStatus(el.uploadStatus, "Prepared " + files.length + " file(s) on this list. Next: upload to the bucket if needed, then Save to the website to list them on the gallery.", "ok");
    });
  }

  function ingestLocalFile(file) {
    var type = typeFromContentType(file.type, file.name);
    var objectName = uniqueObjectName(file.name);
    var objectUrl = "";
    if (type === "image") {
      try { objectUrl = URL.createObjectURL(file); } catch (e) { objectUrl = ""; }
    }
    var videoObjectUrl = "";
    if (type === "video") {
      try { videoObjectUrl = URL.createObjectURL(file); } catch (e) { videoObjectUrl = ""; }
    }
    var seed = {
      type: type,
      file: objectName,
      thumb: type === "image" ? objectName : "",
      title: file.name.replace(/\.[a-z0-9]+$/i, "").replace(/[_\-]+/g, " "),
      localFile: file,
      objectUrl: objectUrl,
      videoObjectUrl: videoObjectUrl,
      pendingUpload: true,
      fileMeta: { contentType: file.type || "", size: file.size || 0 }
    };
    var reader = window.MediaExif ? window.MediaExif.read(file) : Promise.resolve(null);
    return reader.then(function (exif) {
      if (exif) {
        seed.exif = exif;
        if (exif.date) seed.date = exif.date;
        if (exif.camera) seed.details = seed.details || [];
        seed.tags = (exif.suggestedTags || []).slice();
        if (exif.description) seed.description = exif.description;
        var details = [];
        Object.keys(exif.details || {}).forEach(function (k) {
          details.push({ key: k, value: exif.details[k] });
        });
        seed.details = details;
      }
      if (!seed.date && file.lastModified) {
        seed.date = new Date(file.lastModified).toISOString().slice(0, 10);
      }
      registerTags(seed.tags);
      addEntry(seed);
    });
  }

  function applyExifToEntry(entry, exif, opts) {
    opts = opts || {};
    var onlyEmpty = !!opts.overwriteEmpty;
    entry.exif = exif;
    function setIf(field, value) {
      if (!value) return;
      if (onlyEmpty && entry[field]) return;
      entry[field] = value;
    }
    setIf("date", exif.date);
    if (exif.description) setIf("description", exif.description);
    (exif.suggestedTags || []).forEach(function (t) {
      if (entry.tags.indexOf(t) === -1) entry.tags.push(t);
    });
    registerTags(entry.tags);
    Object.keys(exif.details || {}).forEach(function (k) {
      if (!entry.details.some(function (d) { return d.key === k; })) {
        entry.details.push({ key: k, value: exif.details[k] });
      } else if (!onlyEmpty) {
        entry.details.forEach(function (d) { if (d.key === k) d.value = exif.details[k]; });
      }
    });
  }

  // ---- entries ----
  function addEntry(seed) {
    var e = newEntry(seed);
    if (!e.title && e.file) e.title = e.file.replace(/\.[a-z0-9]+$/i, "").replace(/[_\-]+/g, " ");
    if (seed && seed.camera && !e.details.some(function (d) { return d.key === "camera"; })) {
      e.details.push({ key: "camera", value: String(seed.camera) });
    }
    if (seed && seed.target) registerTargets([seed.target]);
    state.entries.push(e);
    var k = entryKey(e);
    if (k && state.removedFiles[k]) delete state.removedFiles[k];
    renderTagLibrary();
    renderTargetLibrary();
    renderEntries();
    markDirty();
  }

  function visibleEntries() {
    var q = (state.filterText || "").toLowerCase();
    return state.entries.filter(function (e) {
      if (state.filterTag && e.tags.indexOf(state.filterTag) === -1) return false;
      if (!q) return true;
      var blob = [e.title, e.target, e.file, e.description, e.tags.join(" ")].join(" ").toLowerCase();
      return blob.indexOf(q) !== -1;
    });
  }

  function renderEntries() {
    el.entries.innerHTML = "";
    el.entryCount.textContent = String(state.entries.length);
    var list = visibleEntries();
    if (!state.entries.length) {
      var empty = document.createElement("p");
      empty.className = "hint";
      empty.textContent = "No entries yet. Loading the latest GitHub gallery, or add files from the bucket.";
      el.entries.appendChild(empty);
      return;
    }
    if (!list.length) {
      var none = document.createElement("p");
      none.className = "hint";
      none.textContent = "No entries match the current tag/search filter.";
      el.entries.appendChild(none);
      return;
    }
    list.forEach(function (entry) {
      var index = state.entries.indexOf(entry);
      el.entries.appendChild(buildEntryCard(entry, index));
    });
  }

  function revokeMediaUrls(entry) {
    if (!entry) return;
    if (entry.objectUrl) { URL.revokeObjectURL(entry.objectUrl); entry.objectUrl = ""; }
    if (entry.thumbObjectUrl) { URL.revokeObjectURL(entry.thumbObjectUrl); entry.thumbObjectUrl = ""; }
    if (entry.videoObjectUrl) { URL.revokeObjectURL(entry.videoObjectUrl); entry.videoObjectUrl = ""; }
  }

  function thumbNameFor(entry) {
    if (entry.thumb && /\.(jpe?g|png|webp)$/i.test(entry.thumb)) return entry.thumb;
    var stem = String(entry.file || "video").replace(/\.[^.]+$/, "") || "video";
    return uniqueObjectName(stem + "-thumb.jpg");
  }

  function captureVideoFrame(videoEl) {
    return new Promise(function (resolve, reject) {
      try {
        var w = videoEl.videoWidth || 0;
        var h = videoEl.videoHeight || 0;
        if (!w || !h) {
          reject(new Error("The movie has not loaded a visible frame yet."));
          return;
        }
        var canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        var ctx = canvas.getContext("2d");
        ctx.drawImage(videoEl, 0, 0, w, h);
        canvas.toBlob(function (blob) {
          if (!blob) reject(new Error("Could not capture this frame."));
          else resolve(blob);
        }, "image/jpeg", 0.85);
      } catch (err) {
        reject(err || new Error("Could not capture this frame (the movie may be blocked by CORS)."));
      }
    });
  }

  function applyThumbBlob(entry, blob, refreshThumb) {
    if (entry.thumbObjectUrl) URL.revokeObjectURL(entry.thumbObjectUrl);
    var name = thumbNameFor(entry);
    var file = new File([blob], name, { type: "image/jpeg" });
    entry.thumb = name;
    entry.thumbLocalFile = file;
    entry.thumbObjectUrl = URL.createObjectURL(blob);
    entry.pendingThumbUpload = true;
    entry.autoThumbDone = true;
    if (typeof refreshThumb === "function") refreshThumb();
    markDirty();
    return name;
  }

  function buildFrameGrabber(entry, refreshThumb) {
    var wrap = document.createElement("div");
    wrap.className = "frame-grab";
    var label = document.createElement("label");
    label.textContent = "Movie thumbnail";
    wrap.appendChild(label);
    var hint = document.createElement("p");
    hint.className = "hint";
    hint.style.marginTop = "0";
    hint.textContent = "Play or scrub to the frame you want, then grab it. A JPEG is stored as the gallery thumbnail.";
    wrap.appendChild(hint);
    var video = document.createElement("video");
    video.className = "frame-grab-video";
    video.controls = true;
    video.playsInline = true;
    video.preload = "metadata";
    video.setAttribute("playsinline", "");
    if (entry.videoObjectUrl || entry.localFile) {
      if (!entry.videoObjectUrl && entry.localFile) {
        try { entry.videoObjectUrl = URL.createObjectURL(entry.localFile); } catch (e) {}
      }
      video.src = entry.videoObjectUrl || "";
    } else if (entry.file) {
      video.crossOrigin = "anonymous";
      video.src = resolveUrl(entry.file, baseUrl());
    }
    wrap.appendChild(video);
    var status = document.createElement("div");
    status.className = "hint";
    wrap.appendChild(status);
    var bar = document.createElement("div");
    bar.className = "btn-bar";
    var grabBtn = mkBtn("Grab this frame as thumbnail", "small primary", function () {
      grabBtn.disabled = true;
      status.textContent = "Capturing frame…";
      captureVideoFrame(video).then(function (blob) {
        var name = applyThumbBlob(entry, blob, refreshThumb);
        status.textContent = "Thumbnail set to " + name + ". Upload to the bucket (if needed), then Save to the website.";
        grabBtn.disabled = false;
      }).catch(function (err) {
        status.textContent = (err && err.message) || "Could not capture this frame. If the movie is already in the bucket, CORS may block grabbing — upload the movie from this computer, or set a JPEG in Thumbnail.";
        grabBtn.disabled = false;
      });
    });
    bar.appendChild(grabBtn);
    wrap.appendChild(bar);
    video.addEventListener("error", function () {
      status.textContent = "This browser cannot play this movie here (common with some .MOV files). Export MP4, or type a JPEG filename in Thumbnail.";
    });
    function autoGrab() {
      if (entry.autoThumbDone || entry.thumb) return;
      captureVideoFrame(video).then(function (blob) {
        if (entry.thumb && !entry.pendingThumbUpload) return;
        var name = applyThumbBlob(entry, blob, refreshThumb);
        status.textContent = "Grabbed a first frame as " + name + ". Scrub and grab again to pick a better still.";
      }).catch(function () { /* wait for a user grab */ });
    }
    video.addEventListener("loadeddata", function () {
      if (entry.autoThumbDone || entry.thumb) return;
      try {
        var dur = video.duration;
        var t = 1;
        if (isFinite(dur) && dur > 0) t = Math.min(1, Math.max(0.1, dur * 0.08));
        video.currentTime = t;
      } catch (e) {
        autoGrab();
      }
    });
    video.addEventListener("seeked", function onSeeked() {
      if (entry.autoThumbDone || (entry.thumb && !entry.pendingThumbUpload)) {
        video.removeEventListener("seeked", onSeeked);
        return;
      }
      autoGrab();
      video.removeEventListener("seeked", onSeeked);
    });
    return wrap;
  }

  function buildEntryCard(entry, index) {
    var card = document.createElement("div");
    card.className = "entry" + (entry.selected ? " selected" : "");

    var head = document.createElement("div");
    head.className = "entry-head";
    var check = document.createElement("input");
    check.type = "checkbox";
    check.checked = !!entry.selected;
    check.title = "Select for bulk tag";
    check.addEventListener("change", function () { entry.selected = check.checked; card.classList.toggle("selected", entry.selected); });
    head.appendChild(check);
    var idx = document.createElement("span");
    idx.className = "idx";
    idx.textContent = "#" + (index + 1);
    head.appendChild(idx);
    if (entry.pendingUpload) {
      var pend = document.createElement("span");
      pend.className = "badge pending";
      pend.textContent = "in this list — not in the bucket yet";
      head.appendChild(pend);
    } else if (!isListedOnGithub(entry)) {
      var neu = document.createElement("span");
      neu.className = "badge new";
      neu.textContent = "new — save to publish";
      head.appendChild(neu);
    } else {
      var listed = document.createElement("span");
      listed.className = "badge listed";
      listed.textContent = "listed on gallery";
      head.appendChild(listed);
    }
    if (entry.pendingThumbUpload) {
      var pthumb = document.createElement("span");
      pthumb.className = "badge pending";
      pthumb.textContent = "thumbnail not in bucket yet";
      head.appendChild(pthumb);
    }
    if (entry.exif && entry.exif.source) {
      var ex = document.createElement("span");
      ex.className = "badge ok";
      ex.textContent = "metadata: " + entry.exif.source;
      head.appendChild(ex);
    }
    var spacer = document.createElement("span");
    spacer.className = "spacer";
    head.appendChild(spacer);

    var up = mkBtn("↑", "small ghost", function () { move(index, -1); });
    var down = mkBtn("↓", "small ghost", function () { move(index, 1); });
    var dup = mkBtn("Duplicate", "small ghost", function () {
      var copy = newEntry(JSON.parse(JSON.stringify({
        type: entry.type, title: entry.title, date: entry.date, target: entry.target,
        tags: entry.tags, description: entry.description, file: entry.file, thumb: entry.thumb, details: entry.details
      })));
      state.entries.splice(index + 1, 0, copy);
      renderEntries();
      markDirty();
    });
    var del = mkBtn("Remove from gallery", "small danger", function () {
      if (!confirm("Remove “" + (entry.file || entry.title || "this item") + "” from the gallery list? The file stays in the bucket. Save to the website to publish this removal.")) return;
      revokeMediaUrls(entry);
      var k = entryKey(entry);
      if (k) state.removedFiles[k] = true;
      state.entries.splice(index, 1);
      renderTagLibrary();
      renderTargetLibrary();
      renderEntries();
      renderFileList();
      markDirty();
    });
    up.disabled = index === 0;
    down.disabled = index === state.entries.length - 1;
    head.appendChild(up); head.appendChild(down); head.appendChild(dup); head.appendChild(del);
    card.appendChild(head);

    var body = document.createElement("div");
    body.className = "entry-body";
    var thumb = document.createElement("img");
    thumb.className = "entry-thumb";
    var fields = document.createElement("div");
    fields.className = "entry-fields";

    function refreshThumb() {
      var still = entry.thumbObjectUrl ||
        (entry.thumb ? resolveUrl(entry.thumb, baseUrl()) : "") ||
        (entry.type === "image" ? (entry.objectUrl || resolveUrl(entry.file, baseUrl())) : "");
      if (still) {
        thumb.src = still;
        thumb.className = "entry-thumb";
        thumb.alt = entry.title || entry.file;
      } else {
        thumb.removeAttribute("src");
        thumb.className = "entry-thumb placeholder";
        thumb.alt = entry.type || "media";
      }
    }
    thumb.addEventListener("error", function () {
      thumb.className = "entry-thumb placeholder";
      thumb.removeAttribute("src");
    });

    var r1 = document.createElement("div"); r1.className = "row";
    r1.appendChild(fieldSelect("Type", TYPES, entry.type, function (v) {
      entry.type = v; refreshThumb(); renderDetails(); markDirty();
    }));
    r1.appendChild(fieldText("Title", entry.title, "e.g. Aurora over Cork Harbour", function (v) { entry.title = v; markDirty(); }, 2));
    fields.appendChild(r1);

    var r2 = document.createElement("div"); r2.className = "row";
    r2.appendChild(fieldDate("Date", entry.date, function (v) { entry.date = v; markDirty(); }));
    r2.appendChild(fieldText("Target / subject", entry.target, "e.g. Aurora Borealis", function (v) {
      entry.target = v;
      markDirty();
    }, 2, "targetsuggest", function (v) {
      var t = String(v || "").trim();
      entry.target = t;
      if (t) {
        delete state.removedTargets[t];
        registerTargets([t]);
        renderTargetLibrary();
      }
      markDirty();
    }));
    fields.appendChild(r2);

    var r3 = document.createElement("div"); r3.className = "row";
    r3.appendChild(fieldText("File (object path or URL)", entry.file, "IMG_1959.jpg", function (v) {
      entry.file = v.trim(); refreshThumb(); markDirty();
    }, 2));
    r3.appendChild(fieldText("Thumbnail (optional)", entry.thumb, "same as file if blank, or a grabbed movie frame", function (v) {
      entry.thumb = v.trim(); refreshThumb(); markDirty();
    }, 2));
    fields.appendChild(r3);

    if (entry.type === "video") {
      fields.appendChild(buildFrameGrabber(entry, refreshThumb));
    }

    if (entry.exif && (entry.exif.camera || entry.exif.dateTime || entry.exif.gps)) {
      var box = document.createElement("div");
      box.className = "exif-box";
      var bits = [];
      if (entry.exif.camera) bits.push("Camera: " + entry.exif.camera);
      if (entry.exif.dateTime) bits.push("Captured: " + entry.exif.dateTime.replace("T", " "));
      if (entry.exif.lens) bits.push("Lens: " + entry.exif.lens);
      if (entry.exif.iso) bits.push("ISO " + entry.exif.iso);
      if (entry.exif.exposureTime) bits.push(entry.exif.exposureTime);
      if (entry.exif.fNumber) bits.push(entry.exif.fNumber);
      if (entry.exif.focalLength) bits.push(entry.exif.focalLength);
      if (entry.exif.width && entry.exif.height) bits.push(entry.exif.width + "×" + entry.exif.height);
      if (entry.exif.gps) bits.push(entry.exif.gps.lat.toFixed(5) + ", " + entry.exif.gps.lon.toFixed(5));
      box.textContent = bits.join(" · ");
      fields.appendChild(box);
    }

    var tagsField = document.createElement("div"); tagsField.className = "field";
    var tagsLabel = document.createElement("label"); tagsLabel.textContent = "Tags"; tagsField.appendChild(tagsLabel);
    var tagsBox = document.createElement("div"); tagsBox.className = "tags";
    tagsField.appendChild(tagsBox);
    fields.appendChild(tagsField);

    function renderTags(focusInput) {
      tagsBox.innerHTML = "";
      entry.tags.forEach(function (t, ti) {
        var chip = document.createElement("span"); chip.className = "chip";
        chip.appendChild(document.createTextNode(t));
        var x = document.createElement("button"); x.type = "button"; x.textContent = "×";
        x.addEventListener("click", function () {
          entry.tags.splice(ti, 1); renderTags(true); renderTagLibrary(); markDirty();
        });
        chip.appendChild(x);
        tagsBox.appendChild(chip);
      });
      var inp = document.createElement("input");
      inp.type = "text";
      inp.placeholder = entry.tags.length ? "add tag…" : "type a tag, press Enter";
      inp.setAttribute("list", "tagsuggest");
      inp.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === ",") {
          ev.preventDefault();
          addTagFromInput(inp.value);
          inp.value = "";
        } else if (ev.key === "Backspace" && !inp.value && entry.tags.length) {
          entry.tags.pop(); renderTags(true); renderTagLibrary(); markDirty();
        }
      });
      inp.addEventListener("change", function () { if (inp.value.trim()) { addTagFromInput(inp.value); inp.value = ""; } });
      tagsBox.appendChild(inp);
      if (focusInput) inp.focus();
    }
    function addTagFromInput(raw) {
      raw.split(",").forEach(function (part) {
        var t = part.trim();
        if (t && entry.tags.indexOf(t) === -1) {
          entry.tags.push(t);
          delete state.removedTags[t];
          registerTags([t]);
        }
      });
      renderTags(true);
      renderTagLibrary();
      markDirty();
    }
    renderTags(false);

    var descField = document.createElement("div"); descField.className = "field";
    var descLabel = document.createElement("label"); descLabel.textContent = "Description"; descField.appendChild(descLabel);
    var ta = document.createElement("textarea"); ta.rows = 2; ta.value = entry.description;
    ta.addEventListener("input", function () { entry.description = ta.value; markDirty(); });
    descField.appendChild(ta);
    fields.appendChild(descField);

    var detailsField = document.createElement("div"); detailsField.className = "field";
    var dHead = document.createElement("label"); dHead.textContent = "Details"; detailsField.appendChild(dHead);
    var detailsWrap = document.createElement("div");
    detailsField.appendChild(detailsWrap);
    var dBar = document.createElement("div"); dBar.className = "btn-bar";
    dBar.appendChild(mkBtn("+ Add detail", "small", function () {
      entry.details.push({ key: "", value: "" }); renderDetails(); markDirty();
    }));
    dBar.appendChild(mkBtn("Suggested fields", "small ghost", function () {
      (DETAIL_TEMPLATES[entry.type] || []).forEach(function (k) {
        if (!entry.details.some(function (d) { return d.key === k; })) entry.details.push({ key: k, value: "" });
      });
      renderDetails(); markDirty();
    }));
    detailsField.appendChild(dBar);
    fields.appendChild(detailsField);

    function renderDetails() {
      detailsWrap.innerHTML = "";
      entry.details.forEach(function (d, di) {
        var row = document.createElement("div"); row.className = "detail-row";
        var k = document.createElement("input"); k.className = "k"; k.type = "text"; k.placeholder = "key (e.g. camera)"; k.value = d.key;
        k.setAttribute("list", "detailsuggest");
        k.addEventListener("input", function () { d.key = k.value.trim(); markDirty(); });
        var v = document.createElement("input"); v.type = "text"; v.placeholder = "value"; v.value = d.value;
        v.addEventListener("input", function () { d.value = v.value; markDirty(); });
        var rm = mkBtn("×", "small danger", function () { entry.details.splice(di, 1); renderDetails(); markDirty(); });
        row.appendChild(k); row.appendChild(v); row.appendChild(rm);
        detailsWrap.appendChild(row);
      });
    }
    renderDetails();

    body.appendChild(thumb);
    body.appendChild(fields);
    card.appendChild(body);
    refreshThumb();
    return card;
  }

  function move(index, dir) {
    var ni = index + dir;
    if (ni < 0 || ni >= state.entries.length) return;
    var tmp = state.entries[index];
    state.entries[index] = state.entries[ni];
    state.entries[ni] = tmp;
    renderEntries();
    markDirty();
  }

  function mkBtn(label, cls, fn) {
    var b = document.createElement("button"); b.type = "button"; b.className = cls; b.textContent = label;
    b.addEventListener("click", fn); return b;
  }
  function fieldText(label, value, placeholder, onInput, grow, listId, onChange) {
    var f = document.createElement("div"); f.className = "field"; if (grow) f.style.flex = grow + " 1 200px";
    var l = document.createElement("label"); l.textContent = label; f.appendChild(l);
    var i = document.createElement("input"); i.type = "text"; i.value = value || ""; i.placeholder = placeholder || "";
    if (listId) i.setAttribute("list", listId);
    i.addEventListener("input", function () { onInput(i.value); });
    if (onChange) i.addEventListener("change", function () { onChange(i.value); });
    f.appendChild(i);
    return f;
  }
  function fieldDate(label, value, onInput) {
    var f = document.createElement("div"); f.className = "field";
    var l = document.createElement("label"); l.textContent = label; f.appendChild(l);
    var i = document.createElement("input"); i.type = "date"; i.value = value || "";
    i.addEventListener("input", function () { onInput(i.value); }); f.appendChild(i);
    return f;
  }
  function fieldSelect(label, opts, value, onChange) {
    var f = document.createElement("div"); f.className = "field";
    var l = document.createElement("label"); l.textContent = label; f.appendChild(l);
    var s = document.createElement("select");
    opts.forEach(function (o) { var op = document.createElement("option"); op.value = o; op.textContent = o; s.appendChild(op); });
    s.value = value;
    s.addEventListener("change", function () { onChange(s.value); }); f.appendChild(s);
    return f;
  }

  var tagDatalist = document.createElement("datalist"); tagDatalist.id = "tagsuggest"; document.body.appendChild(tagDatalist);
  var targetDatalist = document.createElement("datalist"); targetDatalist.id = "targetsuggest"; document.body.appendChild(targetDatalist);
  var detailDatalist = document.createElement("datalist"); detailDatalist.id = "detailsuggest"; document.body.appendChild(detailDatalist);
  function refreshTagSuggestions() {
    tagDatalist.innerHTML = "";
    state.tagCatalog.slice().sort().forEach(function (t) {
      var o = document.createElement("option"); o.value = t; tagDatalist.appendChild(o);
    });
  }
  function refreshTargetSuggestions() {
    targetDatalist.innerHTML = "";
    state.targetCatalog.slice().sort().forEach(function (t) {
      var o = document.createElement("option"); o.value = t; targetDatalist.appendChild(o);
    });
  }
  (function seedDetailKeys() {
    var seen = {};
    Object.keys(DETAIL_TEMPLATES).forEach(function (t) {
      DETAIL_TEMPLATES[t].forEach(function (k) { seen[k] = true; });
    });
    ["make", "model", "lens", "iso", "aperture", "focalLength", "latitude", "longitude", "resolution"].forEach(function (k) { seen[k] = true; });
    Object.keys(seen).forEach(function (k) { var o = document.createElement("option"); o.value = k; detailDatalist.appendChild(o); });
  })();

  // ---- manifest ----
  function detailsObject(entry) {
    var details = {};
    (entry.details || []).forEach(function (d) { if (d.key) details[d.key] = d.value; });
    return details;
  }

  function buildManifest() {
    var base = baseUrl();
    var compat = !!currentCollection().astroCompat;
    var items = state.entries.map(function (e) {
      var out = { type: e.type || "other", title: e.title || "" };
      if (e.date) out.date = e.date;
      if (e.target) out.target = e.target;
      if (e.tags && e.tags.length) out.tags = e.tags.slice();
      out.file = e.file || "";
      if (base && e.file) out.url = resolveUrl(e.file, base);
      if (e.thumb) out.thumb = e.thumb;
      if (e.description) out.description = e.description;
      var details = detailsObject(e);
      if (Object.keys(details).length) out.details = details;
      if (e.fileMeta && (e.fileMeta.contentType || e.fileMeta.size)) {
        out.fileMeta = {};
        if (e.fileMeta.contentType) out.fileMeta.contentType = e.fileMeta.contentType;
        if (e.fileMeta.size) out.fileMeta.size = e.fileMeta.size;
      }
      if (compat && e.type === "image") {
        out.full = e.file || "";
        if (!out.thumb) out.thumb = e.file || "";
        ["camera", "telescope", "integration"].forEach(function (k) {
          if (details[k]) out[k] = details[k];
        });
      }
      return out;
    });
    return {
      version: 1,
      collection: state.collectionKey,
      updated: new Date().toISOString(),
      tags: state.tagCatalog.slice(),
      targets: state.targetCatalog.slice(),
      items: items
    };
  }

  function updateOutput() {
    if (el.output) el.output.value = JSON.stringify(buildManifest(), null, 2);
    updateSaveBar();
  }

  function entryFromObject(o) {
    o = o || {};
    var detailPairs = [];
    if (o.details && typeof o.details === "object" && !Array.isArray(o.details)) {
      Object.keys(o.details).forEach(function (k) { detailPairs.push({ key: k, value: String(o.details[k]) }); });
    } else if (Array.isArray(o.details)) {
      o.details.forEach(function (d) {
        if (d && d.key) detailPairs.push({ key: d.key, value: String(d.value == null ? "" : d.value) });
      });
    }
    ["camera", "telescope", "integration", "filter", "exposure", "frequency", "mode", "duration"].forEach(function (k) {
      if (o[k] != null && !detailPairs.some(function (d) { return d.key === k; })) {
        detailPairs.push({ key: k, value: String(o[k]) });
      }
    });
    var file = o.file || o.full || o.url || o.thumb || "";
    var base = baseUrl();
    if (base && file.indexOf(base) === 0) file = file.slice(base.length);
    var thumb = o.thumb || "";
    if (base && thumb.indexOf(base) === 0) thumb = thumb.slice(base.length);
    registerTags(o.tags);
    if (o.target) registerTargets([o.target]);
    return newEntry({
      type: o.type || typeFromContentType("", file),
      title: o.title || "",
      date: o.date || "",
      target: o.target || "",
      tags: o.tags || [],
      description: o.description || "",
      file: file,
      thumb: thumb === file ? "" : thumb,
      details: detailPairs,
      fileMeta: o.fileMeta || null
    });
  }

  function importEntries(data, opts) {
    opts = opts || {};
    var tags = [];
    var targets = [];
    var arr;
    if (Array.isArray(data)) {
      arr = data;
    } else if (data && Array.isArray(data.items)) {
      arr = data.items;
      tags = data.tags || [];
      targets = data.targets || [];
    } else {
      throw new Error("Manifest must be a JSON array or { items: [] }.");
    }
    state.tagCatalog = [];
    state.targetCatalog = [];
    state.knownTags = {};
    state.knownTargets = {};
    registerTags(tags);
    registerTargets(targets);
    state.entries = arr.map(entryFromObject);
    arr.forEach(function (o) { if (o && o.target) registerTargets([o.target]); });
    if (!opts.keepDirty) state.dirty = false;
    recordLoadedFiles();
    refreshTagSuggestions();
    refreshTargetSuggestions();
    renderTagLibrary();
    renderTargetLibrary();
    renderEntries();
    updateOutput();
  }

  function githubRawUrl(path) {
    path = path || currentCollection().saveTo;
    return "https://raw.githubusercontent.com/" + GITHUB.owner + "/" + GITHUB.repo + "/" +
      GITHUB.branch + "/" + path + "?t=" + Date.now();
  }

  function githubApiContentsUrl(path) {
    path = path || currentCollection().saveTo;
    return "https://api.github.com/repos/" + GITHUB.owner + "/" + GITHUB.repo +
      "/contents/" + path + "?ref=" + GITHUB.branch;
  }

  function githubHeaders() {
    var h = { Accept: "application/vnd.github+json" };
    if (state.githubToken) h.Authorization = "Bearer " + state.githubToken;
    return h;
  }

  function decodeGithubContent(json) {
    if (!json || !json.content) return null;
    var b64 = String(json.content).replace(/\n/g, "");
    var bin = atob(b64);
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return JSON.parse(new TextDecoder().decode(bytes));
  }

  function fetchLatestGithubManifest(path, opts) {
    opts = opts || {};
    path = path || currentCollection().saveTo;
    function fromApi() {
      return fetch(githubApiContentsUrl(path), { cache: "no-store", headers: githubHeaders() }).then(function (r) {
        if (r.status === 404) return { data: null, source: "GitHub API " + GITHUB.branch, sha: null, path: path };
        if (!r.ok) throw new Error("GitHub HTTP " + r.status);
        return r.json().then(function (meta) {
          return {
            data: decodeGithubContent(meta),
            source: "GitHub API " + GITHUB.branch,
            sha: meta.sha || null,
            path: path
          };
        });
      });
    }
    function fromRaw() {
      return fetch(githubRawUrl(path), { cache: "no-store" }).then(function (r) {
        if (!r.ok) throw new Error("raw HTTP " + r.status);
        return r.json().then(function (data) {
          return { data: data, source: "GitHub " + GITHUB.branch, sha: null, path: path };
        });
      });
    }
    if (opts.requireSha) return fromApi();
    return fromApi().catch(function () { return fromRaw(); });
  }

  function mergeRemoteItems(remote, opts) {
    opts = opts || {};
    if (!remote || !Array.isArray(remote.items)) return 0;
    if (opts.unionCatalogs) {
      registerTags(remote.tags);
      registerTargets(remote.targets);
    }
    var localKeys = {};
    state.entries.forEach(function (e) {
      var k = entryKey(e);
      if (k) localKeys[k] = true;
    });
    var kept = 0;
    remote.items.forEach(function (item) {
      var k = (item && (item.file || item.full)) || "";
      if (!k || localKeys[k] || state.removedFiles[k]) return;
      state.entries.push(entryFromObject(item));
      localKeys[k] = true;
      if (opts.markListed) state.loadedItemFiles[k] = true;
      kept++;
    });
    if (kept || opts.unionCatalogs) {
      renderTagLibrary();
      renderTargetLibrary();
      renderEntries();
    }
    return kept;
  }

  function loadManifestFromRepo() {
    var collectionKey = state.collectionKey;
    var c = COLLECTIONS[collectionKey];
    var localUrls = [c.saveTo, "./" + c.saveTo, "dist/" + c.saveTo];
    function stillHere() { return state.collectionKey === collectionKey; }
    if (el.loadBanner) setStatus(el.loadBanner, "Loading the latest gallery list from GitHub…", "");
    setStatus(el.importStatus, "Loading latest " + escapeHtml(c.saveTo) + " from GitHub…", "");

    function fromLocal(i) {
      if (!stillHere()) return Promise.resolve();
      if (i >= localUrls.length) {
        state.loadSource = "";
        setStatus(el.importStatus, "No existing manifest at " + escapeHtml(c.saveTo) + ". Starting empty — add files from the bucket.", "warn");
        if (el.loadBanner) {
          setStatus(el.loadBanner, "No gallery list found yet for this section. Add files from the bucket, then Save to the website.", "warn");
        }
        return Promise.resolve();
      }
      return fetch(localUrls[i], { cache: "no-store" }).then(function (r) {
        if (!stillHere()) return;
        if (!r.ok) return fromLocal(i + 1);
        return r.json().then(function (data) {
          if (!stillHere()) return;
          importEntries(data);
          state.loadSource = localUrls[i] + " (may be a cached Pages copy)";
          var msg = "Loaded " + state.entries.length + " item(s) from " + escapeHtml(localUrls[i]) +
            ". This may lag behind GitHub — use Reload latest gallery from GitHub if it looks stale.";
          setStatus(el.importStatus, msg, "warn");
          if (el.loadBanner) setStatus(el.loadBanner, msg, "warn");
        });
      }).catch(function () { return fromLocal(i + 1); });
    }

    function mergeSameOriginDraft() {
      return fetch(c.saveTo, { cache: "no-store" }).then(function (r) {
        if (!stillHere() || !r.ok) return 0;
        return r.json().then(function (data) {
          if (!stillHere()) return 0;
          return mergeRemoteItems(data, { markListed: false, unionCatalogs: true });
        });
      }).catch(function () { return 0; });
    }

    return fetchLatestGithubManifest(c.saveTo).then(function (result) {
      if (!stillHere()) return;
      if (!result || !result.data) return fromLocal(0);
      importEntries(result.data);
      state.loadSource = result.source;
      var githubCount = state.entries.length;
      return mergeSameOriginDraft().then(function (extra) {
        if (!stillHere()) return;
        var msg = "Loaded <strong>" + githubCount + " item(s)</strong> from " +
          escapeHtml(result.source) + " (" + escapeHtml(c.saveTo) + "). This is the live gallery list. Add or remove files below, then Save.";
        if (extra) {
          msg += " Also found " + extra + " extra item(s) in the local copy of the file that are not on GitHub yet — they are marked <em>new — save to publish</em>.";
        }
        setStatus(el.importStatus, msg, "ok");
        if (el.loadBanner) setStatus(el.loadBanner, msg, "ok");
      });
    }).catch(function () {
      return fromLocal(0);
    });
  }

  // ---- GCS upload ----
  function gcsMetadataFor(entry) {
    var details = detailsObject(entry);
    var meta = {};
    if (entry.title) meta.title = entry.title;
    if (entry.date) meta.date = entry.date;
    if (entry.target) meta.target = entry.target;
    if (entry.tags.length) meta.tags = entry.tags.join(",");
    if (details.camera) meta.camera = details.camera;
    if (details.latitude) meta.latitude = details.latitude;
    if (details.longitude) meta.longitude = details.longitude;
    return meta;
  }

  function uploadNamedFileToGcs(objectName, file, metadata) {
    if (!file) return Promise.reject(new Error("No local file for " + objectName));
    var boundary = "whitegate_" + Date.now() + "_" + Math.random().toString(16).slice(2);
    var resource = {
      name: objectName,
      contentType: file.type || "application/octet-stream",
      metadata: metadata || {}
    };
    return file.arrayBuffer().then(function (buf) {
      var encoder = new TextEncoder();
      var head = encoder.encode(
        "--" + boundary + "\r\n" +
        "Content-Type: application/json; charset=UTF-8\r\n\r\n" +
        JSON.stringify(resource) + "\r\n" +
        "--" + boundary + "\r\n" +
        "Content-Type: " + (file.type || "application/octet-stream") + "\r\n\r\n"
      );
      var tail = encoder.encode("\r\n--" + boundary + "--\r\n");
      var body = new Uint8Array(head.length + buf.byteLength + tail.length);
      body.set(head, 0);
      body.set(new Uint8Array(buf), head.length);
      body.set(tail, head.length + buf.byteLength);
      var url = "https://storage.googleapis.com/upload/storage/v1/b/" +
        encodeURIComponent(currentBucket()) + "/o?uploadType=multipart";
      return fetch(url, {
        method: "POST",
        headers: {
          Authorization: "Bearer " + state.gcsToken,
          "Content-Type": "multipart/related; boundary=" + boundary
        },
        body: body
      }).then(function (r) {
        if (!r.ok) {
          return r.text().then(function (t) { throw new Error("GCS HTTP " + r.status + " " + t.slice(0, 200)); });
        }
        return r.json();
      });
    });
  }

  function uploadFileToGcs(entry) {
    return uploadNamedFileToGcs(entry.file, entry.localFile, gcsMetadataFor(entry)).then(function (json) {
      entry.pendingUpload = false;
      return json;
    });
  }

  function uploadThumbToGcs(entry) {
    var meta = gcsMetadataFor(entry);
    meta.role = "thumbnail";
    return uploadNamedFileToGcs(entry.thumb, entry.thumbLocalFile, meta).then(function (json) {
      entry.pendingThumbUpload = false;
      return json;
    });
  }

  function uploadPending() {
    if (!state.gcsToken) {
      setStatus(el.publishStatus, "Paste a GCS access token in Settings first (<code>gcloud auth print-access-token</code>).", "warn");
      return;
    }
    var jobs = [];
    state.entries.forEach(function (e) {
      if (e.pendingUpload && e.localFile) jobs.push({ entry: e, kind: "file", name: e.file, run: function () { return uploadFileToGcs(e); } });
      if (e.pendingThumbUpload && e.thumbLocalFile) jobs.push({ entry: e, kind: "thumb", name: e.thumb, run: function () { return uploadThumbToGcs(e); } });
    });
    if (!jobs.length) {
      setStatus(el.publishStatus, "Nothing to upload — add files or grab a movie frame first.", "warn");
      return;
    }
    el.uploadGcsBtn.disabled = true;
    setStatus(el.publishStatus, "Uploading " + jobs.length + " file(s) to <code>" + escapeHtml(currentBucket()) + "</code>…", "");
    var i = 0;
    function next() {
      if (i >= jobs.length) {
        el.uploadGcsBtn.disabled = false;
        renderEntries();
        updateOutput();
        var names = jobs.map(function (j) { return "<code>" + escapeHtml(j.name) + "</code>"; }).join(", ");
        var msg = "Uploaded " + jobs.length + " file(s) to the bucket: " + names +
          ". They are stored, but <strong>not on the public gallery yet</strong> until you Save to the website.";
        setStatus(el.publishStatus, msg, "ok");
        if (el.uploadStatus) setStatus(el.uploadStatus, msg, "ok");
        return Promise.resolve();
      }
      var job = jobs[i++];
      setStatus(el.publishStatus, "Uploading " + escapeHtml(job.name) + " (" + i + "/" + jobs.length + ")…", "");
      return job.run().then(next, function (err) {
        el.uploadGcsBtn.disabled = false;
        setStatus(el.publishStatus, "Upload failed on " + escapeHtml(job.name) + ": " + escapeHtml(err.message || String(err)) +
          ". If this is a CORS error, apply the example CORS config to the bucket (see help below).", "err");
      });
    }
    next();
  }

  function gsutilSnippet() {
    var bucket = currentBucket();
    var lines = [];
    state.entries.forEach(function (e) {
      if (e.pendingUpload) lines.push("gcloud storage cp " + JSON.stringify(e.file) + " gs://" + bucket + "/" + e.file);
      if (e.pendingThumbUpload) lines.push("gcloud storage cp " + JSON.stringify(e.thumb) + " gs://" + bucket + "/" + e.thumb);
    });
    lines.push("# then commit " + currentCollection().saveTo);
    return lines.join("\n") || "# no pending local files";
  }

  // ---- GitHub commit ----
  function utf8ToBase64(str) {
    var bytes = new TextEncoder().encode(str);
    var bin = "";
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  }

  function githubEditUrl(path) {
    path = path || currentCollection().saveTo;
    return "https://github.com/" + GITHUB.owner + "/" + GITHUB.repo + "/edit/" + GITHUB.branch + "/" + path;
  }

  function setSaveDisabled(disabled) {
    state.saveInFlight = !!disabled;
    [el.saveBarBtn, el.saveBarBtn2].forEach(function (b) {
      if (b) b.disabled = disabled;
    });
    if (el.sectionTabs) {
      el.sectionTabs.querySelectorAll("button").forEach(function (b) { b.disabled = disabled; });
    }
    if (el.reloadGithubBtn) el.reloadGithubBtn.disabled = disabled;
  }

  function confirmationHtml(col) {
    col = col || currentCollection();
    var n = state.entries.length;
    var newOnes = state.entries.filter(function (e) { return !isListedOnGithub(e); });
    var removed = Object.keys(state.removedFiles);
    var bits = [];
    bits.push("<strong>Saved " + n + " item(s)</strong> to <code>" + escapeHtml(col.saveTo) + "</code> on <code>" + GITHUB.branch + "</code>.");
    if (newOnes.length) {
      bits.push("Newly listed: " + newOnes.map(function (e) {
        return "<code>" + escapeHtml(e.file || e.title) + "</code>";
      }).join(", ") + ".");
    }
    if (removed.length) {
      bits.push("Removed from gallery: " + removed.map(function (f) {
        return "<code>" + escapeHtml(f) + "</code>";
      }).join(", ") + ".");
    }
    if (!newOnes.length && !removed.length) {
      bits.push("Existing gallery items were kept. Tags and targets were saved with the list.");
    }
    bits.push("The public gallery updates in about 1–2 minutes. Then hard-refresh <a href=\"" +
      escapeHtml(col.page) + "\" target=\"_blank\" rel=\"noopener\">" +
      escapeHtml(col.label) + "</a>.");
    return bits.join(" ");
  }

  function markPublished() {
    recordLoadedFiles();
    state.dirty = false;
    state.removedTags = {};
    state.removedTargets = {};
    renderEntries();
    renderFileList();
    updateSaveBar();
  }

  function mergeFetchedManifest(data) {
    return mergeRemoteItems(data, { markListed: true, unionCatalogs: true });
  }

  function saveToWebsite() {
    if (state.saveInFlight) return;
    var collectionKey = state.collectionKey;
    var col = COLLECTIONS[collectionKey];
    var path = col.saveTo;
    function stillHere() { return state.collectionKey === collectionKey; }

    setSaveDisabled(true);
    if (el.publishStatus) setStatus(el.publishStatus, "Fetching the latest GitHub list so existing gallery items are kept…", "");
    fetchLatestGithubManifest(path, { requireSha: !!state.githubToken }).then(function (result) {
      if (!stillHere()) {
        setSaveDisabled(false);
        if (el.publishStatus) setStatus(el.publishStatus, "Save cancelled because the gallery section changed.", "warn");
        return;
      }
      var kept = 0;
      if (result && result.data) kept = mergeFetchedManifest(result.data);
      updateOutput();
      var json = (el.output && el.output.value) || JSON.stringify(buildManifest(), null, 2);
      if (kept && el.publishStatus) {
        setStatus(el.publishStatus, "Merged " + kept + " item(s) that were on GitHub but missing from this page, then saving…", "ok");
      }
      var sha = result && result.sha ? result.sha : null;
      function afterCopy() {
        if (state.githubToken) {
          commitManifest(json, { sha: sha, path: path, collectionKey: collectionKey, attempt: 0 });
          return;
        }
        setSaveDisabled(false);
        window.open(githubEditUrl(path), "_blank", "noopener");
        var msg = "Merged list copied (" + state.entries.length + " item(s)). In the GitHub tab: select all, paste over the whole file, click <strong>Commit changes</strong> on <code>main</code>. Wait about a minute, then hard-refresh the gallery.";
        if (el.publishStatus) setStatus(el.publishStatus, msg, "ok");
        if (el.saveConfirm) setStatus(el.saveConfirm, msg, "ok");
        if (el.saveBarStatus) {
          el.saveBarStatus.textContent = "Copied " + state.entries.length + " items. Paste into GitHub and commit.";
          el.saveBarStatus.className = "save-bar-status dirty";
        }
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(json).then(afterCopy, function () {
          fallbackCopy(json);
          afterCopy();
        });
      } else {
        fallbackCopy(json);
        afterCopy();
      }
    }).catch(function (err) {
      setSaveDisabled(false);
      if (el.publishStatus) {
        setStatus(el.publishStatus, "Could not fetch GitHub before save: " + escapeHtml(err.message || String(err)) +
          ". Saving this page’s list only — check you are not dropping existing items.", "warn");
      }
      if (!stillHere()) return;
      updateOutput();
      var json = (el.output && el.output.value) || JSON.stringify(buildManifest(), null, 2);
      if (state.githubToken) {
        setSaveDisabled(true);
        commitManifest(json, { sha: null, path: path, collectionKey: collectionKey, attempt: 0, refetchSha: true });
      } else {
        window.open(githubEditUrl(path), "_blank", "noopener");
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(json);
      }
    });
  }

  function commitManifest(json, opts) {
    opts = opts || {};
    if (!state.githubToken) {
      saveToWebsite();
      return;
    }
    var collectionKey = opts.collectionKey || state.collectionKey;
    var path = opts.path || COLLECTIONS[collectionKey].saveTo;
    var sha = opts.sha;
    var attempt = opts.attempt || 0;
    json = json || ((el.output && el.output.value) || JSON.stringify(buildManifest(), null, 2));
    var api = "https://api.github.com/repos/" + GITHUB.owner + "/" + GITHUB.repo + "/contents/" + path;
    var headers = {
      Authorization: "Bearer " + state.githubToken,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json"
    };

    function putWithSha(useSha) {
      var body = {
        message: "Update " + path + " via media admin",
        content: utf8ToBase64(json),
        branch: GITHUB.branch
      };
      if (useSha) body.sha = useSha;
      return fetch(api, { method: "PUT", headers: headers, body: JSON.stringify(body) });
    }

    function retryAgainstLatest() {
      if (attempt >= 1) return Promise.reject(new Error("GitHub file changed again during save. Try Save once more."));
      if (state.collectionKey !== collectionKey) return Promise.reject(new Error("Gallery section changed during save"));
      if (el.publishStatus) setStatus(el.publishStatus, "GitHub list changed; merging the latest copy and retrying…", "warn");
      return fetchLatestGithubManifest(path, { requireSha: true }).then(function (result) {
        if (state.collectionKey !== collectionKey) throw new Error("Gallery section changed during save");
        if (result && result.data) mergeFetchedManifest(result.data);
        updateOutput();
        var nextJson = (el.output && el.output.value) || JSON.stringify(buildManifest(), null, 2);
        return commitManifest(nextJson, {
          sha: result && result.sha ? result.sha : null,
          path: path,
          collectionKey: collectionKey,
          attempt: attempt + 1
        });
      });
    }

    if (el.publishStatus) setStatus(el.publishStatus, "Saving " + escapeHtml(path) + " to " + GITHUB.branch + "…", "");
    setSaveDisabled(true);

    var start = Promise.resolve(sha);
    if (opts.refetchSha && !sha) {
      // Last-resort only when the merge fetch could not return a SHA.
      start = fetchLatestGithubManifest(path, { requireSha: true }).then(function (result) {
        if (result && result.data) mergeFetchedManifest(result.data);
        updateOutput();
        json = (el.output && el.output.value) || JSON.stringify(buildManifest(), null, 2);
        return result && result.sha ? result.sha : null;
      });
    }

    return start.then(function (useSha) {
      return putWithSha(useSha);
    }).then(function (r) {
      if (r && r.handled) return r;
      if (r && r.status === 409) {
        return retryAgainstLatest().then(function () { return { handled: true }; });
      }
      setSaveDisabled(false);
      if (!r.ok) return r.text().then(function (t) { throw new Error("GitHub HTTP " + r.status + " " + t.slice(0, 180)); });
      if (state.collectionKey !== collectionKey) return { handled: true };
      var html = confirmationHtml(COLLECTIONS[collectionKey]);
      markPublished();
      if (el.publishStatus) setStatus(el.publishStatus, html, "ok");
      if (el.saveConfirm) setStatus(el.saveConfirm, html, "ok");
      if (el.loadBanner) setStatus(el.loadBanner, html, "ok");
      if (el.saveBarStatus) {
        el.saveBarStatus.textContent = "Saved " + state.entries.length + " item(s). Gallery updates in 1–2 minutes.";
        el.saveBarStatus.className = "save-bar-status";
      }
      return { handled: true };
    }).catch(function (err) {
      setSaveDisabled(false);
      if (el.publishStatus) setStatus(el.publishStatus, "Could not save: " + escapeHtml(err.message || String(err)), "err");
    });
  }

  function downloadManifest() {
    var blob = new Blob([el.output.value || "[]"], { type: "application/json" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "manifest.json";
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
  }

  function copyText(text, btn, label) {
    function done() { btn.textContent = "Copied!"; setTimeout(function () { btn.textContent = label; }, 1200); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
    } else { fallbackCopy(text); done(); }
  }
  function fallbackCopy(text) {
    el.output.focus(); el.output.select();
    try { document.execCommand("copy"); } catch (e) {}
  }

  function applyBulkTag() {
    var tag = (el.bulkTag.value || "").trim();
    if (!tag) { setStatus(el.bulkStatus, "Enter a tag first.", "warn"); return; }
    var selected = state.entries.filter(function (e) { return e.selected; });
    if (!selected.length) { setStatus(el.bulkStatus, "Select one or more entries with the checkboxes.", "warn"); return; }
    delete state.removedTags[tag];
    registerTags([tag]);
    selected.forEach(function (e) {
      if (e.tags.indexOf(tag) === -1) e.tags.push(tag);
    });
    el.bulkTag.value = "";
    renderTagLibrary();
    renderEntries();
    markDirty();
    setStatus(el.bulkStatus, "Applied “" + escapeHtml(tag) + "” to " + selected.length + " entr(y/ies).", "ok");
  }

  function bind() {
    el.sectionTabs = $("sectionTabs");
    el.baseUrl = $("baseUrl");
    el.loadFilesBtn = $("loadFilesBtn");
    el.addManualBtn = $("addManualBtn");
    el.fileCount = $("fileCount");
    el.listStatus = $("listStatus");
    el.fileList = $("fileList");
    el.entries = $("entries");
    el.entryCount = $("entryCount");
    el.addBlankBtn = $("addBlankBtn");
    el.clearBtn = $("clearBtn");
    el.importUrl = $("importUrl");
    el.importUrlBtn = $("importUrlBtn");
    el.importFile = $("importFile");
    el.importStatus = $("importStatus");
    el.output = $("output");
    el.downloadBtn = $("downloadBtn");
    el.copyBtn = $("copyBtn");
    el.saveHint = $("saveHint");
    el.tagLibrary = $("tagLibrary");
    el.newTag = $("newTag");
    el.addTagBtn = $("addTagBtn");
    el.dropzone = $("dropzone");
    el.fileInput = $("fileInput");
    el.uploadStatus = $("uploadStatus");
    el.gcsToken = $("gcsToken");
    el.githubToken = $("githubToken");
    el.filterText = $("filterText");
    el.bulkTag = $("bulkTag");
    el.bulkBtn = $("bulkBtn");
    el.bulkStatus = $("bulkStatus");
    el.uploadGcsBtn = $("uploadGcsBtn");
    el.commitBtn = $("commitBtn");
    el.copyGsutilBtn = $("copyGsutilBtn");
    el.publishStatus = $("publishStatus");
    el.gsutilOut = $("gsutilOut");
    el.saveBarBtn = $("saveBarBtn");
    el.saveBarBtn2 = $("saveBarBtn2");
    el.saveBarStatus = $("saveBarStatus");
    el.previewGalleryLink = $("previewGalleryLink");
    el.savePathLabel = $("savePathLabel");
    el.addAllBtn = $("addAllBtn");
    el.onlyNewCheck = $("onlyNewCheck");
    el.hideHeicCheck = $("hideHeicCheck");
    el.manualName = $("manualName");
    el.reloadGithubBtn = $("reloadGithubBtn");
    el.targetLibrary = $("targetLibrary");
    el.newTarget = $("newTarget");
    el.addTargetBtn = $("addTargetBtn");
    el.loadBanner = $("loadBanner");
    el.saveConfirm = $("saveConfirm");

    el.gcsToken.value = state.gcsToken;
    el.githubToken.value = state.githubToken;

    el.gcsToken.addEventListener("change", function () {
      state.gcsToken = el.gcsToken.value.trim();
      if (state.gcsToken) sessionStorage.setItem("wg_gcs_token", state.gcsToken);
      else sessionStorage.removeItem("wg_gcs_token");
    });
    el.githubToken.addEventListener("change", function () {
      state.githubToken = el.githubToken.value.trim();
      if (state.githubToken) sessionStorage.setItem("wg_gh_token", state.githubToken);
      else sessionStorage.removeItem("wg_gh_token");
    });

    el.loadFilesBtn.addEventListener("click", listObjects);
    el.addManualBtn.addEventListener("click", function () {
      var name = (el.manualName && el.manualName.value || "").trim();
      if (!name) {
        setStatus(el.listStatus, "Type the exact object name first, e.g. IMG_2756.jpg", "warn");
        return;
      }
      addEntry({
        file: name,
        thumb: /\.(jpe?g|png|webp|gif)$/i.test(name) ? name : "",
        type: typeFromContentType("", name),
        title: name.replace(/\.[a-z0-9]+$/i, "").replace(/[_\-]+/g, " ")
      });
      el.manualName.value = "";
      renderFileList();
      setStatus(el.listStatus, "Added <code>" + escapeHtml(name) + "</code> to this list. Save to the website to list it on the gallery.", "ok");
    });
    if (el.addAllBtn) el.addAllBtn.addEventListener("click", addAllNewWebFiles);
    if (el.onlyNewCheck) {
      el.onlyNewCheck.checked = state.onlyNew;
      el.onlyNewCheck.addEventListener("change", function () {
        state.onlyNew = el.onlyNewCheck.checked;
        renderFileList();
      });
    }
    if (el.hideHeicCheck) {
      el.hideHeicCheck.checked = state.hideHeic;
      el.hideHeicCheck.addEventListener("change", function () {
        state.hideHeic = el.hideHeicCheck.checked;
        renderFileList();
      });
    }
    el.addBlankBtn.addEventListener("click", function () { addEntry({}); });
    el.clearBtn.addEventListener("click", function () {
      if (state.entries.length && !confirm("Remove all items from this gallery list? Files stay in the bucket. Save to publish the empty list.")) return;
      state.entries.forEach(function (e) {
        revokeMediaUrls(e);
        var k = entryKey(e);
        if (k) state.removedFiles[k] = true;
      });
      state.entries = [];
      renderTagLibrary();
      renderTargetLibrary();
      renderEntries();
      renderFileList();
      markDirty();
    });
    el.addTagBtn.addEventListener("click", function () {
      addCatalogTag(el.newTag.value);
      el.newTag.value = "";
    });
    el.newTag.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); addCatalogTag(el.newTag.value); el.newTag.value = ""; }
    });
    if (el.addTargetBtn) {
      el.addTargetBtn.addEventListener("click", function () {
        addCatalogTarget(el.newTarget.value);
        el.newTarget.value = "";
      });
    }
    if (el.newTarget) {
      el.newTarget.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") { ev.preventDefault(); addCatalogTarget(el.newTarget.value); el.newTarget.value = ""; }
      });
    }
    if (el.reloadGithubBtn) {
      el.reloadGithubBtn.addEventListener("click", function () {
        if (state.saveInFlight) return;
        if (state.dirty && !confirm("Reload from GitHub and discard unsaved edits?")) return;
        resetCollectionState();
        renderTagLibrary();
        renderTargetLibrary();
        renderEntries();
        updateOutput();
        loadManifestFromRepo().then(function () { listObjects(); });
      });
    }
    el.filterText.addEventListener("input", function () {
      state.filterText = el.filterText.value;
      renderEntries();
    });
    el.bulkBtn.addEventListener("click", applyBulkTag);

    el.dropzone.addEventListener("click", function () { el.fileInput.click(); });
    el.fileInput.addEventListener("change", function () {
      handleFiles(el.fileInput.files);
      el.fileInput.value = "";
    });
    ["dragenter", "dragover"].forEach(function (evt) {
      el.dropzone.addEventListener(evt, function (e) { e.preventDefault(); el.dropzone.classList.add("drag"); });
    });
    ["dragleave", "drop"].forEach(function (evt) {
      el.dropzone.addEventListener(evt, function (e) { e.preventDefault(); el.dropzone.classList.remove("drag"); });
    });
    el.dropzone.addEventListener("drop", function (e) {
      handleFiles(e.dataTransfer && e.dataTransfer.files);
    });

    el.importUrlBtn.addEventListener("click", function () {
      var url = el.importUrl.value.trim();
      if (!url) { setStatus(el.importStatus, "Enter a manifest URL first.", "warn"); return; }
      setStatus(el.importStatus, "Loading " + escapeHtml(url) + "…", "");
      fetch(url, { cache: "no-store" }).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      }).then(function (data) {
          importEntries(data);
          setStatus(el.importStatus, "Imported " + state.entries.length + " item(s).", "ok");
      }).catch(function (e) {
        setStatus(el.importStatus, "Could not load manifest: " + escapeHtml(e.message || String(e)), "err");
      });
    });
    el.importFile.addEventListener("change", function () {
      var f = el.importFile.files && el.importFile.files[0];
      if (!f) return;
      var reader = new FileReader();
      reader.onload = function () {
        try {
          importEntries(JSON.parse(reader.result));
          setStatus(el.importStatus, "Imported " + state.entries.length + " item(s) from " + escapeHtml(f.name) + ".", "ok");
        } catch (err) {
          setStatus(el.importStatus, "Invalid JSON: " + escapeHtml(err.message || String(err)), "err");
        }
      };
      reader.readAsText(f);
    });

    el.downloadBtn.addEventListener("click", downloadManifest);
    el.copyBtn.addEventListener("click", function () { copyText(el.output.value || "[]", el.copyBtn, "Copy JSON"); });
    if (el.uploadGcsBtn) el.uploadGcsBtn.addEventListener("click", uploadPending);
    if (el.copyGsutilBtn) {
      el.copyGsutilBtn.addEventListener("click", function () {
        var snippet = gsutilSnippet();
        if (el.gsutilOut) el.gsutilOut.value = snippet;
        copyText(snippet, el.copyGsutilBtn, "Copy gcloud copy commands");
      });
    }
    function bindSave(btn) {
      if (btn) btn.addEventListener("click", saveToWebsite);
    }
    bindSave(el.saveBarBtn);
    bindSave(el.saveBarBtn2);
  }

  bind();
  renderSectionTabs();
  refreshBase();
  renderTagLibrary();
  renderTargetLibrary();
  renderEntries();
  updateOutput();
  loadManifestFromRepo().then(function () { listObjects(); });
})();
