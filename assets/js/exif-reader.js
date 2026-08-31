/**
 * Browser-side media metadata reader for the observatory admin.
 * Parses JPEG/WebP/PNG/HEIC EXIF (and a few PNG text chunks), then
 * falls back to HTML5 media element metadata for audio/video.
 */
(function (global) {
  "use strict";

  var FILENAME_TAG_HINTS = [
    { re: /aurora|borealis/i, tag: "aurora" },
    { re: /moon|lunar/i, tag: "moon" },
    { re: /sun|solar|prominence/i, tag: "solar" },
    { re: /milky|mw[\s_-]?core/i, tag: "milky-way" },
    { re: /jupiter|jove/i, tag: "jupiter" },
    { re: /saturn/i, tag: "saturn" },
    { re: /mars\b/i, tag: "mars" },
    { re: /venus/i, tag: "venus" },
    { re: /comet/i, tag: "comet" },
    { re: /meteor/i, tag: "meteor" },
    { re: /orion|m42/i, tag: "orion" },
    { re: /andromeda|m31/i, tag: "andromeda" },
    { re: /nebula/i, tag: "nebula" },
    { re: /galaxy/i, tag: "galaxy" },
    { re: /iss\b|station/i, tag: "iss" },
    { re: /1420|hi[\s_-]?line|hydrogen/i, tag: "hi-line" },
    { re: /ssb|cw\b|ft8|js8/i, tag: "ham-radio" },
    { re: /qso|contact/i, tag: "qso" },
    { re: /spectrum|waterfall/i, tag: "spectrum" }
  ];

  function resultTemplate() {
    return {
      date: "",
      dateTime: "",
      make: "",
      model: "",
      camera: "",
      lens: "",
      software: "",
      description: "",
      artist: "",
      width: 0,
      height: 0,
      orientation: 0,
      exposureTime: "",
      fNumber: "",
      iso: "",
      focalLength: "",
      gps: null,
      suggestedTags: [],
      details: {},
      source: ""
    };
  }

  function toDateParts(exifDate) {
    if (!exifDate) return { date: "", dateTime: "" };
    var m = String(exifDate).match(/^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/);
    if (m) {
      return { date: m[1] + "-" + m[2] + "-" + m[3], dateTime: m[1] + "-" + m[2] + "-" + m[3] + "T" + m[4] + ":" + m[5] + ":" + m[6] };
    }
    m = String(exifDate).match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) return { date: m[1] + "-" + m[2] + "-" + m[3], dateTime: String(exifDate) };
    return { date: "", dateTime: "" };
  }

  function addDetail(out, key, value) {
    if (value === undefined || value === null || value === "") return;
    out.details[key] = String(value);
  }

  function cleanCamera(make, model) {
    make = (make || "").trim();
    model = (model || "").trim();
    if (!make && !model) return "";
    if (model && make && model.toLowerCase().indexOf(make.toLowerCase()) === 0) return model;
    if (make && model) return make + " " + model;
    return model || make;
  }

  function suggestTags(out, filename) {
    var tags = [];
    function add(t) { if (t && tags.indexOf(t) === -1) tags.push(t); }
    FILENAME_TAG_HINTS.forEach(function (h) {
      if (h.re.test(filename || "") || h.re.test(out.description || "")) add(h.tag);
    });
    var cam = (out.camera || out.model || "").toLowerCase();
    if (/iphone/.test(cam)) add("iphone");
    if (/canon/.test(cam)) add("canon");
    if (/nikon/.test(cam)) add("nikon");
    if (/sony/.test(cam)) add("sony");
    if (/fuji/.test(cam)) add("fujifilm");
    if (out.date) add(out.date.slice(0, 4));
    if (out.dateTime) {
      var hour = Number(out.dateTime.slice(11, 13));
      if (!isNaN(hour) && (hour >= 21 || hour <= 5)) add("night");
    }
    if (out.gps) add("geotagged");
    out.suggestedTags = tags;
  }

  function finalize(out, filename) {
    out.camera = cleanCamera(out.make, out.model);
    addDetail(out, "camera", out.camera);
    addDetail(out, "make", out.make);
    addDetail(out, "model", out.model);
    addDetail(out, "lens", out.lens);
    addDetail(out, "software", out.software);
    addDetail(out, "iso", out.iso);
    addDetail(out, "exposure", out.exposureTime);
    addDetail(out, "aperture", out.fNumber);
    addDetail(out, "focalLength", out.focalLength);
    if (out.width && out.height) addDetail(out, "resolution", out.width + "×" + out.height);
    if (out.gps) {
      addDetail(out, "latitude", out.gps.lat.toFixed(6));
      addDetail(out, "longitude", out.gps.lon.toFixed(6));
    }
    suggestTags(out, filename);
    return out;
  }

  function decodeBytes(bytes, encoding) {
    try {
      if (encoding === "utf16") {
        var le = bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xfe;
        var be = bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff;
        var start = (le || be) ? 2 : 0;
        var out = [];
        for (var i = start; i + 1 < bytes.length; i += 2) {
          var c = le ? (bytes[i] | (bytes[i + 1] << 8)) : ((bytes[i] << 8) | bytes[i + 1]);
          if (c) out.push(String.fromCharCode(c));
        }
        return out.join("");
      }
      return new TextDecoder(encoding || "utf-8").decode(bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes));
    } catch (e) {
      var s = "";
      for (var j = 0; j < bytes.length; j++) s += String.fromCharCode(bytes[j]);
      return s.replace(/\0+$/, "");
    }
  }

  function asciiFromView(view, offset, length) {
    var chars = [];
    for (var i = 0; i < length; i++) {
      var c = view.getUint8(offset + i);
      if (c === 0) break;
      chars.push(String.fromCharCode(c));
    }
    return chars.join("");
  }

  function findExifHeader(bytes) {
    for (var i = 0; i < bytes.length - 8; i++) {
      if (bytes[i] === 0x45 && bytes[i + 1] === 0x78 && bytes[i + 2] === 0x69 && bytes[i + 3] === 0x66 &&
          bytes[i + 4] === 0 && bytes[i + 5] === 0) {
        return i + 6;
      }
    }
    return -1;
  }

  function rational(view, offset, le) {
    var n = view.getUint32(offset, le);
    var d = view.getUint32(offset + 4, le);
    if (!d) return 0;
    return n / d;
  }

  function signedRational(view, offset, le) {
    var n = view.getInt32(offset, le);
    var d = view.getInt32(offset + 4, le);
    if (!d) return 0;
    return n / d;
  }

  function formatExposure(v) {
    if (!v) return "";
    if (v >= 1) return Number(v).toFixed(v % 1 ? 1 : 0) + "s";
    var den = Math.round(1 / v);
    return "1/" + den + "s";
  }

  function formatAperture(v) {
    if (!v) return "";
    return "f/" + (Math.round(v * 10) / 10);
  }

  function gpsToDecimal(values, ref) {
    if (!values || values.length < 3) return null;
    var dec = values[0] + values[1] / 60 + values[2] / 3600;
    if (ref === "S" || ref === "W") dec = -dec;
    return dec;
  }

  function parseTiffExif(buffer, tiffOffset) {
    var view = new DataView(buffer);
    if (tiffOffset + 8 > view.byteLength) return null;
    var byte0 = view.getUint8(tiffOffset);
    var byte1 = view.getUint8(tiffOffset + 1);
    var le = byte0 === 0x49 && byte1 === 0x49;
    var be = byte0 === 0x4d && byte1 === 0x4d;
    if (!le && !be) return null;
    if (view.getUint16(tiffOffset + 2, le) !== 0x002a) return null;

    var out = resultTemplate();
    out.source = "exif";

    function readIFD(offset, gpsMode) {
      if (offset <= 0 || tiffOffset + offset + 2 > view.byteLength) return {};
      var abs = tiffOffset + offset;
      var count = view.getUint16(abs, le);
      var map = {};
      for (var i = 0; i < count; i++) {
        var e = abs + 2 + i * 12;
        if (e + 12 > view.byteLength) break;
        var tag = view.getUint16(e, le);
        var type = view.getUint16(e + 2, le);
        var num = view.getUint32(e + 4, le);
        var typeSize = { 1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8 }[type] || 1;
        var byteLen = num * typeSize;
        var valueOffset = byteLen > 4 ? tiffOffset + view.getUint32(e + 8, le) : e + 8;
        if (valueOffset < 0 || valueOffset + Math.min(byteLen, 4) > view.byteLength) continue;
        map[tag] = readValue(type, num, valueOffset, byteLen, gpsMode && tag === 0x0002);
      }
      var next = view.getUint32(abs + 2 + count * 12, le);
      map.__next = next;
      return map;
    }

    function readValue(type, num, offset, byteLen, forceRationals) {
      try {
        if (type === 2) {
          return asciiFromView(view, offset, Math.min(num, view.byteLength - offset));
        }
        if (type === 5 || forceRationals) {
          var rats = [];
          var nRat = Math.max(1, Math.floor(byteLen / 8));
          for (var r = 0; r < nRat && offset + r * 8 + 8 <= view.byteLength; r++) {
            rats.push(rational(view, offset + r * 8, le));
          }
          return rats.length === 1 ? rats[0] : rats;
        }
        if (type === 10) {
          return signedRational(view, offset, le);
        }
        if (type === 3) {
          if (num === 1) return view.getUint16(offset, le);
          var shorts = [];
          for (var s = 0; s < num && offset + s * 2 + 2 <= view.byteLength; s++) shorts.push(view.getUint16(offset + s * 2, le));
          return shorts;
        }
        if (type === 4) {
          if (num === 1) return view.getUint32(offset, le);
          var longs = [];
          for (var l = 0; l < num && offset + l * 4 + 4 <= view.byteLength; l++) longs.push(view.getUint32(offset + l * 4, le));
          return longs;
        }
        if (type === 1 || type === 7) {
          if (num === 1) return view.getUint8(offset);
          return asciiFromView(view, offset, Math.min(num, 64));
        }
      } catch (err) {
        return null;
      }
      return null;
    }

    var ifd0 = readIFD(view.getUint32(tiffOffset + 4, le), false);
    applyIfd(out, ifd0);

    var exifOffset = ifd0[0x8769];
    if (typeof exifOffset === "number") applyIfd(out, readIFD(exifOffset, false));

    var gpsOffset = ifd0[0x8825];
    if (typeof gpsOffset === "number") {
      var gpsIfd = readIFD(gpsOffset, true);
      var lat = gpsToDecimal([].concat(gpsIfd[0x0002] || []), gpsIfd[0x0001]);
      var lon = gpsToDecimal([].concat(gpsIfd[0x0004] || []), gpsIfd[0x0003]);
      if (lat != null && lon != null && !isNaN(lat) && !isNaN(lon)) {
        out.gps = { lat: lat, lon: lon };
      }
    }
    return out;
  }

  function applyIfd(out, ifd) {
    if (!ifd) return;
    if (ifd[0x010F]) out.make = String(ifd[0x010F]).trim();
    if (ifd[0x0110]) out.model = String(ifd[0x0110]).trim();
    if (ifd[0x0112]) out.orientation = Number(ifd[0x0112]) || 0;
    if (ifd[0x0131]) out.software = String(ifd[0x0131]).trim();
    if (ifd[0x010E]) out.description = String(ifd[0x010E]).trim();
    if (ifd[0x013B]) out.artist = String(ifd[0x013B]).trim();
    if (ifd[0xA434]) out.lens = String(ifd[0xA434]).trim();
    if (ifd[0xA002]) out.width = Number(ifd[0xA002]) || out.width;
    if (ifd[0xA003]) out.height = Number(ifd[0xA003]) || out.height;
    if (ifd[0x0100]) out.width = Number(ifd[0x0100]) || out.width;
    if (ifd[0x0101]) out.height = Number(ifd[0x0101]) || out.height;
    if (ifd[0x8827]) out.iso = String(Array.isArray(ifd[0x8827]) ? ifd[0x8827][0] : ifd[0x8827]);
    if (ifd[0x829A]) out.exposureTime = formatExposure(Number(ifd[0x829A]));
    if (ifd[0x829D]) out.fNumber = formatAperture(Number(ifd[0x829D]));
    if (ifd[0x920A]) {
      var fl = Number(ifd[0x920A]);
      out.focalLength = fl ? (Math.round(fl) + "mm") : "";
    }
    var dt = ifd[0x9003] || ifd[0x9004] || ifd[0x0132];
    if (dt) {
      var parts = toDateParts(dt);
      if (parts.date) {
        out.date = parts.date;
        out.dateTime = parts.dateTime;
      }
    }
  }

  function parseJpeg(buffer) {
    var view = new DataView(buffer);
    if (view.byteLength < 4 || view.getUint16(0) !== 0xffd8) return null;
    var offset = 2;
    while (offset + 4 < view.byteLength) {
      if (view.getUint8(offset) !== 0xff) break;
      var marker = view.getUint8(offset + 1);
      var size = view.getUint16(offset + 2);
      if (marker === 0xe1 && size >= 8) {
        var start = offset + 4;
        if (asciiFromView(view, start, 4) === "Exif") {
          var parsed = parseTiffExif(buffer, start + 6);
          if (parsed) return parsed;
        }
      }
      if (marker === 0xda) break;
      offset += 2 + size;
    }
    return null;
  }

  function parseWebp(bytes, buffer) {
    if (bytes.length < 16) return null;
    var riff = String.fromCharCode(bytes[0], bytes[1], bytes[2], bytes[3]);
    var webp = String.fromCharCode(bytes[8], bytes[9], bytes[10], bytes[11]);
    if (riff !== "RIFF" || webp !== "WEBP") return null;
    var i = 12;
    while (i + 8 < bytes.length) {
      var fourcc = String.fromCharCode(bytes[i], bytes[i + 1], bytes[i + 2], bytes[i + 3]);
      var size = bytes[i + 4] | (bytes[i + 5] << 8) | (bytes[i + 6] << 16) | (bytes[i + 7] << 24);
      if (size < 0) break;
      if (fourcc === "EXIF") {
        var chunkStart = i + 8;
        var rel = findExifHeader(bytes.subarray(chunkStart, chunkStart + Math.min(size, bytes.length - chunkStart)));
        var tiffAt = rel >= 0 ? chunkStart + rel : chunkStart;
        var parsed = parseTiffExif(buffer, tiffAt);
        if (parsed) return parsed;
      }
      i += 8 + size + (size % 2);
    }
    return null;
  }

  function parsePngText(bytes) {
    if (bytes.length < 16) return null;
    if (!(bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47)) return null;
    var out = resultTemplate();
    out.source = "png-text";
    var i = 8;
    var found = false;
    while (i + 12 < bytes.length) {
      var size = (bytes[i] << 24) | (bytes[i + 1] << 16) | (bytes[i + 2] << 8) | bytes[i + 3];
      if (size < 0 || i + 12 + size > bytes.length) break;
      var type = String.fromCharCode(bytes[i + 4], bytes[i + 5], bytes[i + 6], bytes[i + 7]);
      if (type === "tEXt" || type === "iTXt") {
        var payload = bytes.subarray(i + 8, i + 8 + size);
        var raw = decodeBytes(payload, "latin1");
        var z = raw.indexOf("\0");
        if (z > 0) {
          var key = raw.slice(0, z);
          var val = raw.slice(z + 1).replace(/^\0+/, "").trim();
          if (key === "Creation Time") {
            var parts = toDateParts(val.replace(/-/g, ":").replace("T", " ").slice(0, 19));
            if (!parts.date) {
              var iso = val.match(/^(\d{4}-\d{2}-\d{2})/);
              if (iso) parts = { date: iso[1], dateTime: val };
            }
            out.date = parts.date;
            out.dateTime = parts.dateTime;
            found = true;
          } else if (key === "Comment" || key === "Description") {
            out.description = val;
            found = true;
          } else if (key === "Software") {
            out.software = val;
            found = true;
          } else if (key === "Author") {
            out.artist = val;
            found = true;
          }
        }
      }
      if (type === "IEND") break;
      i += 12 + size;
    }
    return found ? out : null;
  }

  function parseEmbeddedExif(bytes, buffer) {
    var idx = findExifHeader(bytes.subarray(0, Math.min(bytes.length, 512 * 1024)));
    if (idx < 0) return null;
    var parsed = parseTiffExif(buffer, idx);
    if (parsed) parsed.source = parsed.source || "embedded-exif";
    return parsed;
  }

  function fromFileFallback(file) {
    var out = resultTemplate();
    out.source = "file";
    if (file && file.lastModified) {
      var d = new Date(file.lastModified);
      if (!isNaN(d.getTime())) {
        var iso = d.toISOString();
        out.date = iso.slice(0, 10);
        out.dateTime = iso.slice(0, 19);
      }
    }
    return out;
  }

  function readMediaElement(file) {
    return new Promise(function (resolve) {
      var kind = (file.type || "").indexOf("audio/") === 0 ? "audio" : "video";
      var el = document.createElement(kind);
      var url = URL.createObjectURL(file);
      var done = false;
      function finish(extra) {
        if (done) return;
        done = true;
        URL.revokeObjectURL(url);
        resolve(extra || {});
      }
      el.preload = "metadata";
      el.onloadedmetadata = function () {
        finish({
          duration: isFinite(el.duration) ? el.duration : 0,
          width: el.videoWidth || 0,
          height: el.videoHeight || 0
        });
      };
      el.onerror = function () { finish({}); };
      setTimeout(function () { finish({}); }, 2500);
      el.src = url;
    });
  }

  function formatDuration(seconds) {
    if (!seconds || !isFinite(seconds)) return "";
    var s = Math.round(seconds);
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var r = s % 60;
    if (h) return h + ":" + String(m).padStart(2, "0") + ":" + String(r).padStart(2, "0");
    return m + ":" + String(r).padStart(2, "0");
  }

  function readBuffer(buffer, mime, filename) {
    var bytes = new Uint8Array(buffer);
    var parsed = parseJpeg(buffer) || parseWebp(bytes, buffer) || parsePngText(bytes) || parseEmbeddedExif(bytes, buffer);
    if (parsed) return finalize(parsed, filename);
    var empty = resultTemplate();
    empty.source = "";
    return finalize(empty, filename);
  }

  function read(file) {
    if (!file) return Promise.resolve(resultTemplate());
    var mime = file.type || "";
    var name = file.name || "";
    return file.arrayBuffer().then(function (buffer) {
      var parsed = readBuffer(buffer, mime, name);
      var fallback = fromFileFallback(file);
      if (!parsed.date && fallback.date) {
        parsed.date = fallback.date;
        parsed.dateTime = fallback.dateTime;
        if (!parsed.source) parsed.source = "file";
      }
      var isAV = mime.indexOf("video/") === 0 || mime.indexOf("audio/") === 0 ||
        /\.(mp4|mov|m4v|webm|wav|mp3|ogg|flac)$/i.test(name);
      if (!isAV) return parsed;
      return readMediaElement(file).then(function (media) {
        if (media.duration) {
          parsed.details.duration = formatDuration(media.duration);
        }
        if (media.width && media.height) {
          parsed.width = media.width;
          parsed.height = media.height;
          parsed.details.resolution = media.width + "×" + media.height;
        }
        if (!parsed.source) parsed.source = "media-element";
        return parsed;
      });
    }).catch(function () {
      return finalize(fromFileFallback(file), name);
    });
  }

  global.MediaExif = {
    read: read,
    readBuffer: readBuffer,
    suggestFromFilename: function (name) {
      var out = resultTemplate();
      suggestTags(out, name);
      return out.suggestedTags;
    }
  };
})(window);
