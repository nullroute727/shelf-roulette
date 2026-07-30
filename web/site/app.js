/*
 * Shelf Roulette
 * Fetches the shelf from the local proxy, draws a wheel of book spines on a
 * canvas, and spins it to pick the next read. No build step, no dependencies.
 */
(function () {
  "use strict";

  var API_SHELF = "/api/shelf";
  var API_COVER = "/api/cover?url=";

  var MOBILE_MAX = 500;      // below this width: fewer spines, no cover art
  var CAP_SMALL = 10;
  var CAP_LARGE = 18;
  var COVER_MAX_SEGMENTS = 24;   // beyond this a wedge is too thin for a jacket
  var MIN_SPINE_FONT = 11;       // below this a spine label is not worth drawing
  var STORE_KEY = "shelf-roulette.dismissed.v1";
  var MODE_KEY = "shelf-roulette.mode.v1";

  var TAU = Math.PI * 2;
  var POINTER_ANGLE = -Math.PI / 2;  // fixed pointer sits at the top

  // Muted book-cloth colors used for spines and as the cover fallback.
  var SPINE_COLORS = [
    "#3f5d74", "#6b4b3e", "#4f6b52", "#7a4b52", "#5a5470",
    "#7d6a3c", "#3d5b5f", "#6a5237", "#4a4f6b", "#705a45"
  ];

  // Packed shelf uses a narrower, darker set. Ten cycling hues at 242 segments
  // reads as a carnival wheel, whereas a few muted cloth tones read as books.
  // Deliberately low chroma and a narrow value range, so neighbouring spines
  // differ without the wheel banding into stripes.
  var PACKED_COLORS = [
    "#2b3b4d", "#33404f", "#4a3a34", "#3d4a3e", "#524233", "#343a4a",
    "#414a55", "#463c42"
  ];

  /* ---------------- storage with in-memory fallback ---------------- */

  var store = (function () {
    var memory = {};
    var usable = true;
    try {
      var probe = "__sr_probe__";
      window.localStorage.setItem(probe, "1");
      window.localStorage.removeItem(probe);
    } catch (err) {
      usable = false;
    }
    return {
      usable: function () { return usable; },
      get: function (key) {
        if (usable) {
          try { return window.localStorage.getItem(key); }
          catch (err) { usable = false; }
        }
        return Object.prototype.hasOwnProperty.call(memory, key) ? memory[key] : null;
      },
      set: function (key, value) {
        if (usable) {
          try { window.localStorage.setItem(key, value); return; }
          catch (err) { usable = false; }
        }
        memory[key] = value;
      }
    };
  }());

  /* ---------------- state ---------------- */

  var state = {
    pool: [],          // every book on the shelf
    candidates: [],    // the books currently on the wheel
    dismissed: {},     // key -> true
    rotation: 0,
    spinning: false,
    winner: null,
    winnerIndex: -1,
    cap: CAP_LARGE,
    covers: true,
    mode: "all"
  };

  var el = {};
  var canvas, ctx;
  var cssSize = 0;
  var coverCache = {};   // url -> { img: Image|null, state: "loading"|"ok"|"fail" }
  var reduceMotion = false;
  var packedSheen = null;     // cached gradient, rebuilt only when the radius changes
  var packedSheenR = 0;

  /* ---------------- helpers ---------------- */

  function $(id) { return document.getElementById(id); }

  function norm(angle) { return ((angle % TAU) + TAU) % TAU; }

  // amount above zero lightens, below zero darkens.
  function shadeColor(hex, amount) {
    var num = parseInt(hex.slice(1), 16);
    var parts = [(num >> 16) & 255, (num >> 8) & 255, num & 255];
    var out = parts.map(function (v) {
      var shifted = amount >= 0 ? v + (255 - v) * amount : v * (1 + amount);
      return Math.max(0, Math.min(255, Math.round(shifted)));
    });
    return "rgb(" + out[0] + ", " + out[1] + ", " + out[2] + ")";
  }

  function bookKey(book) {
    return book.goodreadsUrl || (book.title + "|" + book.author);
  }

  function randomInt(max) {
    if (max <= 0) return 0;
    if (window.crypto && window.crypto.getRandomValues) {
      var buf = new Uint32Array(1);
      // Rejection sampling keeps the distribution even.
      var limit = Math.floor(4294967296 / max) * max;
      do { window.crypto.getRandomValues(buf); } while (buf[0] >= limit);
      return buf[0] % max;
    }
    return Math.floor(Math.random() * max);
  }

  function shuffled(list) {
    var out = list.slice();
    for (var i = out.length - 1; i > 0; i--) {
      var j = randomInt(i + 1);
      var tmp = out[i]; out[i] = out[j]; out[j] = tmp;
    }
    return out;
  }

  // Goodreads image URLs carry size tokens like ._SY475_ before the extension.
  // Swap in a narrower variant so phones do not pull full size jackets.
  function sizedCover(url, token) {
    if (!url) return null;
    var tokenRe = /\._S[XY]\d+_(?=\.[a-z0-9]+$)/i;
    if (tokenRe.test(url)) return url.replace(tokenRe, "._" + token + "_");
    return url.replace(/(\.[a-z0-9]+)$/i, "._" + token + "_$1");
  }

  function proxiedCover(url) {
    return API_COVER + encodeURIComponent(url);
  }

  function announce(message) {
    el.announce.textContent = message;
  }

  function show(node, visible) {
    if (visible) node.removeAttribute("hidden");
    else node.setAttribute("hidden", "");
  }

  /* ---------------- cover loading ---------------- */

  function loadCover(rawUrl, token) {
    if (!rawUrl) return Promise.resolve(null);
    var target = proxiedCover(sizedCover(rawUrl, token));
    var entry = coverCache[target];
    if (entry && entry.state === "ok") return Promise.resolve(entry.img);
    if (entry && entry.state === "fail") return Promise.resolve(null);
    if (entry && entry.promise) return entry.promise;

    var img = new Image();
    img.decoding = "async";
    entry = { img: img, state: "loading" };
    coverCache[target] = entry;

    entry.promise = new Promise(function (resolve) {
      img.onload = function () { entry.state = "ok"; resolve(img); };
      img.onerror = function () { entry.state = "fail"; entry.img = null; resolve(null); };
      img.src = target;
    });
    return entry.promise;
  }

  // Only the books on the current wheel are preloaded. Everything else waits.
  function preloadCandidateCovers() {
    if (!state.covers) return;
    state.candidates.forEach(function (book) {
      if (!book.coverUrl) return;
      loadCover(book.coverUrl, "SX318").then(function (img) {
        if (img && !state.spinning) drawWheel();
      });
    });
  }

  /* ---------------- canvas sizing ---------------- */

  function layoutCanvas() {
    var wrapWidth = canvas.parentNode.clientWidth || 320;
    var maxByHeight = Math.max(240, Math.round(window.innerHeight * 0.56));
    var size = Math.min(wrapWidth, 520, maxByHeight);

    cssSize = size;
    var dpr = window.devicePixelRatio || 1;
    canvas.style.width = size + "px";
    canvas.style.height = size + "px";
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  // "all" puts the entire shelf on the wheel. "set" falls back to a readable
  // slice, which is the only mode where spine labels and jacket art can fit.
  function currentCap() {
    if (state.mode === "all") return Infinity;
    return window.innerWidth < MOBILE_MAX ? CAP_SMALL : CAP_LARGE;
  }

  // Jacket art needs a wedge wide enough to read, so it is gated on the segment
  // count as well as the viewport.
  function coversAllowed() {
    return window.innerWidth >= MOBILE_MAX && state.candidates.length <= COVER_MAX_SEGMENTS;
  }

  /* ---------------- drawing ---------------- */

  function truncateToWidth(text, maxWidth) {
    if (ctx.measureText(text).width <= maxWidth) return text;
    var out = text;
    while (out.length > 1 && ctx.measureText(out + "...").width > maxWidth) {
      out = out.slice(0, -1);
    }
    return out.replace(/[\s,:;.-]+$/, "") + "...";
  }

  function drawWheel() {
    if (!ctx || !cssSize) return;

    var n = state.candidates.length;
    var c = cssSize / 2;
    var outerR = c - 8;
    var hubR = Math.max(26, outerR * 0.2);

    ctx.clearRect(0, 0, cssSize, cssSize);

    if (n === 0) return;

    var seg = TAU / n;

    // A radial label is limited by the wedge's tangential thickness at mid
    // radius. With the whole shelf on the wheel that is a couple of pixels, so
    // labels and separator hairlines are dropped rather than drawn as mush.
    var midR = (hubR + outerR) / 2;
    var thickness = seg * midR;
    var spineFont = Math.min(17, Math.round(outerR * 0.085), Math.floor(thickness - 2));
    var labelsFit = spineFont >= MIN_SPINE_FONT;
    if (!labelsFit) spineFont = MIN_SPINE_FONT;
    var gapsFit = thickness >= 6;
    var palette = gapsFit ? SPINE_COLORS : PACKED_COLORS;

    ctx.save();
    ctx.translate(c, c);
    ctx.rotate(state.rotation);

    for (var i = 0; i < n; i++) {
      var book = state.candidates[i];
      var a0 = i * seg;
      var a1 = a0 + seg;
      // Deterministic per-index shading breaks up the palette repeat, so a long
      // shelf reads as many individual spines rather than a striped pinwheel.
      // Biased darker so a packed shelf reads as book cloth in low light rather
      // than a bright pinwheel.
      var base = shadeColor(
        palette[i % palette.length],
        ((i * 37) % 11) / 11 * 0.20 - 0.14
      );

      // Wedge shape, reused for fill and as a clip for the jacket art.
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.arc(0, 0, outerR, a0, a1);
      ctx.closePath();

      ctx.fillStyle = base;
      ctx.fill();

      var cover = null;
      if (state.covers && book.coverUrl) {
        var key = proxiedCover(sizedCover(book.coverUrl, "SX318"));
        var entry = coverCache[key];
        if (entry && entry.state === "ok") cover = entry.img;
      }

      if (cover) {
        ctx.save();
        ctx.clip();
        ctx.rotate(a0 + seg / 2);
        // Cover the wedge's bounding box with the jacket, preserving aspect.
        var boxW = outerR;
        var boxH = 2 * outerR * Math.sin(seg / 2) + 4;
        var scale = Math.max(boxW / cover.width, boxH / cover.height);
        var drawW = cover.width * scale;
        var drawH = cover.height * scale;
        ctx.drawImage(cover, 0, -drawH / 2, drawW, drawH);

        // Darken the art so the spine label stays legible over busy jackets.
        var shade = ctx.createLinearGradient(0, 0, outerR, 0);
        shade.addColorStop(0, "rgba(10, 20, 33, 0.9)");
        shade.addColorStop(0.55, "rgba(10, 20, 33, 0.55)");
        shade.addColorStop(1, "rgba(10, 20, 33, 0.3)");
        ctx.fillStyle = shade;
        ctx.fillRect(0, -boxH / 2, outerR, boxH);
        ctx.restore();
      } else if (gapsFit) {
        // Solid spine gets a subtle sheen so it reads as book cloth. Skipped on a
        // packed shelf, where building a gradient per wedge per frame would cost
        // far more than the effect is worth. A single overlay covers that case.
        ctx.save();
        ctx.clip();
        var sheen = ctx.createLinearGradient(0, 0, outerR * Math.cos(a0 + seg / 2), outerR * Math.sin(a0 + seg / 2));
        sheen.addColorStop(0, "rgba(0, 0, 0, 0.34)");
        sheen.addColorStop(0.6, "rgba(255, 255, 255, 0.05)");
        sheen.addColorStop(1, "rgba(0, 0, 0, 0.22)");
        ctx.fillStyle = sheen;
        ctx.fillRect(-outerR, -outerR, outerR * 2, outerR * 2);
        ctx.restore();
      }

      if (!labelsFit) continue;

      // Spine label, running outward from the hub.
      ctx.save();
      ctx.rotate(a0 + seg / 2);
      ctx.textBaseline = "middle";
      ctx.font = "600 " + spineFont + 'px "Barlow Condensed", "Arial Narrow", sans-serif';
      ctx.fillStyle = "#f2e8d2";
      ctx.shadowColor = "rgba(6, 12, 20, 0.85)";
      ctx.shadowBlur = 3;
      var label = truncateToWidth(book.title, outerR - hubR - 18);

      // Labels on the left half would otherwise render upside down, so flip
      // them end over end and draw inward from the rim instead.
      var onScreen = norm(state.rotation + a0 + seg / 2);
      if (onScreen > Math.PI / 2 && onScreen < Math.PI * 1.5) {
        ctx.rotate(Math.PI);
        ctx.textAlign = "left";
        ctx.fillText(label, -(outerR - 12), 0);
      } else {
        ctx.textAlign = "right";
        ctx.fillText(label, outerR - 12, 0);
      }
      ctx.restore();
    }

    // One shared depth wash for the packed shelf, replacing the per-wedge sheen.
    if (!gapsFit) {
      if (!packedSheen || packedSheenR !== outerR) {
        packedSheen = ctx.createRadialGradient(0, 0, hubR, 0, 0, outerR);
        packedSheen.addColorStop(0, "rgba(0, 0, 0, 0.34)");
        packedSheen.addColorStop(0.55, "rgba(255, 255, 255, 0.04)");
        packedSheen.addColorStop(1, "rgba(0, 0, 0, 0.30)");
        packedSheenR = outerR;
      }
      ctx.beginPath();
      ctx.arc(0, 0, outerR, 0, TAU);
      ctx.fillStyle = packedSheen;
      ctx.fill();

      // Faint gilt banding every twelfth spine gives the packed shelf some
      // rhythm, the way tooled bands break up a run of bindings.
      ctx.strokeStyle = "rgba(201, 150, 43, 0.20)";
      ctx.lineWidth = 1;
      for (var g = 0; g < n; g += 12) {
        var ga = g * seg;
        ctx.beginPath();
        ctx.moveTo(hubR * Math.cos(ga), hubR * Math.sin(ga));
        ctx.lineTo(outerR * Math.cos(ga), outerR * Math.sin(ga));
        ctx.stroke();
      }
    }

    // Hairline gaps between spines, only while a gap still reads as a gap.
    if (gapsFit) {
      ctx.strokeStyle = "rgba(8, 16, 26, 0.85)";
      ctx.lineWidth = 1.5;
      for (var k = 0; k < n; k++) {
        var a = k * seg;
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(outerR * Math.cos(a), outerR * Math.sin(a));
        ctx.stroke();
      }
    }

    ctx.restore();

    // Brass rim, drawn unrotated so it stays put.
    ctx.save();
    ctx.translate(c, c);
    ctx.beginPath();
    ctx.arc(0, 0, outerR, 0, TAU);
    ctx.strokeStyle = "#c9962b";
    ctx.lineWidth = 5;
    ctx.stroke();

    // The winning spine sits proud of the rim, like a book pulled off the shelf.
    // With the whole shelf loaded a single wedge is about 1.5 degrees, so the
    // marker is widened to stay findable while still centred on the real winner.
    if (state.winnerIndex >= 0 && state.winnerIndex < n && !state.spinning) {
      var markWidth = Math.max(seg, 0.05);
      var mid = state.rotation + (state.winnerIndex + 0.5) * seg;
      var a0m = mid - markWidth / 2;
      var a1m = mid + markWidth / 2;
      var innerMark = outerR * 0.72;

      // Annular tab rather than a full wedge, so the winning spine stays visible
      // underneath and the marker reads as a bookmark rather than a pie slice.
      ctx.beginPath();
      ctx.arc(0, 0, innerMark, a0m, a1m);
      ctx.arc(0, 0, outerR + 7, a1m, a0m, true);
      ctx.closePath();
      ctx.fillStyle = "#e6bb51";
      ctx.fill();
      ctx.strokeStyle = "#0e1b2b";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.arc(0, 0, outerR - 4, 0, TAU);
    ctx.strokeStyle = "rgba(230, 187, 81, 0.5)";
    ctx.lineWidth = 1;
    ctx.stroke();

    // Hub.
    ctx.beginPath();
    ctx.arc(0, 0, hubR, 0, TAU);
    ctx.fillStyle = "#0e1b2b";
    ctx.fill();
    ctx.strokeStyle = "#c9962b";
    ctx.lineWidth = 2.5;
    ctx.stroke();

    ctx.fillStyle = "#e6bb51";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = '600 ' + Math.max(10, Math.round(hubR * 0.38)) + 'px "Barlow Condensed", "Arial Narrow", sans-serif';
    ctx.fillText("SPIN", 0, 1);
    ctx.restore();
  }

  /* ---------------- spin math ---------------- */

  function segIndexAt(rotation) {
    var n = state.candidates.length;
    if (!n) return -1;
    var seg = TAU / n;
    return Math.floor(norm(POINTER_ANGLE - rotation) / seg) % n;
  }

  // Rotation that puts segment i centered under the pointer, at least
  // minAdvance radians forward of where we are now.
  function rotationForIndex(i, minAdvance) {
    var n = state.candidates.length;
    var seg = TAU / n;
    var desired = POINTER_ANGLE - (i + 0.5) * seg;
    var target = state.rotation + minAdvance;
    var turns = Math.ceil((target - desired) / TAU);
    return desired + TAU * turns;
  }

  // Snap an arbitrary resting rotation to the nearest exact segment center,
  // so the pointer never straddles a gap after a flick.
  function snapRotation(rotation) {
    var n = state.candidates.length;
    var seg = TAU / n;
    var i = Math.floor(norm(POINTER_ANGLE - rotation) / seg) % n;
    var desired = POINTER_ANGLE - (i + 0.5) * seg;
    var turns = Math.round((rotation - desired) / TAU);
    return desired + TAU * turns;
  }

  function easeOutQuart(t) { return 1 - Math.pow(1 - t, 4); }

  function finishSpin(finalRotation) {
    state.rotation = norm(finalRotation);
    state.spinning = false;
    setControlsDisabled(false);
    drawWheel();

    var index = segIndexAt(state.rotation);
    var book = state.candidates[index];
    if (!book) return;
    state.winner = book;
    state.winnerIndex = index;
    drawWheel();
    renderSlip(book);
    announce("Selected: " + book.title + " by " + book.author + ".");
  }

  function runSpin(finalRotation, duration) {
    if (reduceMotion) { finishSpin(finalRotation); return; }

    state.spinning = true;
    setControlsDisabled(true);
    show(el.viewSlip, false);

    var from = state.rotation;
    var delta = finalRotation - from;
    var start = null;

    function frame(now) {
      if (start === null) start = now;
      var t = Math.min(1, (now - start) / duration);
      state.rotation = from + delta * easeOutQuart(t);
      drawWheel();
      if (t < 1) window.requestAnimationFrame(frame);
      else finishSpin(finalRotation);
    }
    window.requestAnimationFrame(frame);
  }

  function spin() {
    if (state.spinning || state.candidates.length === 0) return;
    if (state.candidates.length === 1) {
      finishSpin(rotationForIndex(0, TAU * 2));
      return;
    }
    var target = randomInt(state.candidates.length);
    var turns = 4 + randomInt(3);
    runSpin(rotationForIndex(target, TAU * turns), 4200);
  }

  function flingSpin(angularVelocity) {
    if (state.spinning || state.candidates.length === 0) return;
    var speed = Math.abs(angularVelocity);            // radians per ms
    var duration = Math.min(5200, Math.max(2200, 1400 + speed * 9000));
    // easeOutQuart starts at 4x the average rate, so distance ~= v0 * d / 4.
    var distance = speed * duration / 4;
    distance = Math.min(TAU * 9, Math.max(TAU * 1.6, distance));
    var sign = angularVelocity < 0 ? -1 : 1;
    runSpin(snapRotation(state.rotation + sign * distance), duration);
  }

  // Disabling the button a keyboard user is standing on would drop focus to the
  // body and lose their place in the tab order, so remember it and put it back.
  var refocusAfterSpin = null;

  function setControlsDisabled(disabled) {
    if (disabled) {
      var active = document.activeElement;
      refocusAfterSpin =
        (active === el.btnSpin || active === el.btnReshuffle || active === el.btnMode)
          ? active
          : null;
    }

    el.btnSpin.disabled = disabled;
    el.btnReshuffle.disabled = disabled;
    el.btnMode.disabled = disabled;
    canvas.style.cursor = disabled ? "default" : "pointer";

    if (!disabled && refocusAfterSpin) {
      // preventScroll so this does not fight the result slip scrolling into view.
      refocusAfterSpin.focus({ preventScroll: true });
      refocusAfterSpin = null;
    }
  }

  /* ---------------- gestures ---------------- */

  function bindGestures() {
    var tracking = false;
    var pointerId = null;
    var samples = [];
    var moved = 0;
    var startTime = 0;

    function angleFrom(event) {
      var rect = canvas.getBoundingClientRect();
      var x = event.clientX - (rect.left + rect.width / 2);
      var y = event.clientY - (rect.top + rect.height / 2);
      return Math.atan2(y, x);
    }

    canvas.addEventListener("pointerdown", function (event) {
      if (state.spinning || tracking) return;
      tracking = true;
      pointerId = event.pointerId;
      moved = 0;
      startTime = event.timeStamp;
      samples = [{ angle: angleFrom(event), time: event.timeStamp }];
      if (canvas.setPointerCapture) canvas.setPointerCapture(pointerId);
    });

    canvas.addEventListener("pointermove", function (event) {
      if (!tracking || event.pointerId !== pointerId) return;
      event.preventDefault();
      var angle = angleFrom(event);
      var last = samples[samples.length - 1];
      var delta = angle - last.angle;
      // Unwrap across the atan2 discontinuity.
      while (delta > Math.PI) delta -= TAU;
      while (delta < -Math.PI) delta += TAU;

      moved += Math.abs(delta);
      state.rotation = state.rotation + delta;
      samples.push({ angle: angle, time: event.timeStamp });
      if (samples.length > 12) samples.shift();
      drawWheel();
    });

    function release(event) {
      if (!tracking || (event.pointerId !== undefined && event.pointerId !== pointerId)) return;
      tracking = false;
      if (canvas.releasePointerCapture && pointerId !== null) {
        try { canvas.releasePointerCapture(pointerId); } catch (err) { /* already gone */ }
      }
      pointerId = null;

      var elapsed = event.timeStamp - startTime;

      // Angular velocity over the tail of the drag.
      var velocity = 0;
      if (samples.length >= 2) {
        var last = samples[samples.length - 1];
        var ref = samples[0];
        for (var i = samples.length - 1; i >= 0; i--) {
          if (last.time - samples[i].time <= 120) ref = samples[i];
          else break;
        }
        var span = last.time - ref.time;
        if (span > 8) {
          var swept = last.angle - ref.angle;
          while (swept > Math.PI) swept -= TAU;
          while (swept < -Math.PI) swept += TAU;
          velocity = swept / span;
        }
      }

      var isTap = moved < 0.08 && elapsed < 400;
      if (isTap) { spin(); return; }
      if (Math.abs(velocity) > 0.0015) { flingSpin(velocity); return; }
      // Slow drag with no throw: settle onto the nearest spine.
      runSpin(snapRotation(state.rotation), 420);
    }

    canvas.addEventListener("pointerup", release);
    canvas.addEventListener("pointercancel", release);
  }

  /* ---------------- rendering ---------------- */

  function availableBooks() {
    return state.pool.filter(function (book) {
      return !state.dismissed[bookKey(book)];
    });
  }

  function pickCandidates() {
    var available = availableBooks();
    state.cap = currentCap();
    var take = state.cap === Infinity ? available.length : Math.min(state.cap, available.length);
    state.candidates = shuffled(available).slice(0, take);
    state.winnerIndex = -1;
    // Depends on the candidate count, so it has to follow the slice.
    state.covers = coversAllowed();
  }

  function renderRoster() {
    el.rosterList.textContent = "";

    // With the whole shelf loaded this list would be hundreds of rows nobody
    // reads, rebuilt on every reshuffle, so the disclosure goes away entirely.
    if (state.mode === "all") {
      show(el.roster, false);
      return;
    }
    show(el.roster, true);

    state.candidates.forEach(function (book) {
      var li = document.createElement("li");
      var title = document.createElement("span");
      title.textContent = book.title;
      var author = document.createElement("span");
      author.className = "roster-author";
      author.textContent = book.author ? " / " + book.author : "";
      li.appendChild(title);
      li.appendChild(author);
      el.rosterList.appendChild(li);
    });
  }

  function renderStatus() {
    var available = availableBooks().length;
    var total = state.pool.length;
    if (state.mode !== "all") {
      el.shelfStatus.textContent =
        total + " on the shelf / " + state.candidates.length + " on the wheel";
    } else if (available === total) {
      el.shelfStatus.textContent =
        total + (total === 1 ? " book on the shelf" : " books on the shelf");
    } else {
      // Some are hidden, so the shelf total would overstate what can actually win.
      el.shelfStatus.textContent =
        available + (available === 1 ? " book on the wheel" : " books on the wheel");
    }

    var hidden = total - available;
    if (hidden > 0) {
      el.hiddenCount.textContent = hidden + (hidden === 1 ? " book hidden." : " books hidden.");
      show(el.btnRestore, true);
    } else {
      el.hiddenCount.textContent = "No books hidden.";
      show(el.btnRestore, false);
    }

    canvas.setAttribute(
      "aria-label",
      "Wheel of " + state.candidates.length + " candidate books. Use the spin button to pick one."
    );
  }

  function renderSlip(book) {
    el.slipTitle.textContent = book.title;
    el.slipAuthor.textContent = book.author || "Unknown author";

    var bits = [];
    if (book.year) bits.push(book.year);
    if (book.pages) bits.push(book.pages + " pages");
    if (book.rating) bits.push(book.rating.toFixed(2) + " avg");
    el.slipMeta.textContent = bits.join("  /  ");

    el.slipLink.href = book.goodreadsUrl || "#";
    show(el.slipLink, Boolean(book.goodreadsUrl));

    show(el.slipCover, false);
    if (book.coverUrl) {
      var token = window.innerWidth < MOBILE_MAX ? "SX160" : "SX318";
      loadCover(book.coverUrl, token).then(function (img) {
        if (state.winner !== book || !img) return;
        el.slipCover.src = img.src;
        el.slipCover.alt = "Cover of " + book.title;
        show(el.slipCover, true);
      });
    }

    show(el.viewSlip, true);

    // The slip sits below the wheel, so bring it into view once it is filled in.
    if (el.viewSlip.scrollIntoView) {
      el.viewSlip.scrollIntoView({
        behavior: reduceMotion ? "auto" : "smooth",
        block: "center"
      });
    }
  }

  function renderWheelView() {
    show(el.viewLoading, false);
    show(el.viewError, false);

    if (state.pool.length === 0) {
      show(el.viewWheel, false);
      show(el.viewSlip, false);
      show(el.viewEmpty, true);
      el.emptyMessage.textContent = "The shelf came back empty.";
      show(el.btnEmptyRestore, false);
      return;
    }

    if (state.candidates.length === 0) {
      show(el.viewWheel, false);
      show(el.viewSlip, false);
      show(el.viewEmpty, true);
      el.emptyMessage.textContent =
        "All " + state.pool.length + " books are hidden. Clear the hidden list to spin again.";
      show(el.btnEmptyRestore, true);
      renderStatus();
      return;
    }

    show(el.viewEmpty, false);
    show(el.viewWheel, true);
    el.hint.textContent = reduceMotion
      ? "Tap the wheel or press spin to pick a book."
      : "Tap the wheel to spin, or flick it.";

    layoutCanvas();
    renderRoster();
    renderStatus();
    drawWheel();
    preloadCandidateCovers();
  }

  function showError(message, detail) {
    show(el.viewLoading, false);
    show(el.viewWheel, false);
    show(el.viewSlip, false);
    show(el.viewEmpty, false);
    show(el.viewError, true);
    el.errorMessage.textContent = message;
    if (detail) {
      el.errorDetail.textContent = detail;
      show(el.errorDetail, true);
    } else {
      show(el.errorDetail, false);
    }
    el.shelfStatus.textContent = "Shelf unavailable";
  }

  /* ---------------- data ---------------- */

  function loadDismissed() {
    var raw = store.get(STORE_KEY);
    if (!raw) return;
    try {
      var parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        state.dismissed = parsed;
      } else if (Array.isArray(parsed)) {
        parsed.forEach(function (key) { state.dismissed[key] = true; });
      }
    } catch (err) {
      state.dismissed = {};
    }
  }

  function saveDismissed() {
    try { store.set(STORE_KEY, JSON.stringify(state.dismissed)); }
    catch (err) { /* nothing more we can do locally */ }
  }

  function fetchShelf(refresh) {
    show(el.viewError, false);
    show(el.viewEmpty, false);
    show(el.viewWheel, false);
    show(el.viewSlip, false);
    show(el.viewLoading, true);
    el.shelfStatus.textContent = refresh ? "Refreshing from Goodreads..." : "Loading the shelf...";

    var url = refresh ? API_SHELF + "?refresh=1" : API_SHELF;

    return fetch(url, { headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json().then(
          function (body) { return { ok: response.ok, status: response.status, body: body }; },
          function () { return { ok: false, status: response.status, body: null }; }
        );
      })
      .then(function (result) {
        if (!result.ok) {
          var message = "Could not load the shelf.";
          var detail = "";
          if (result.body && typeof result.body === "object" && result.body.error) {
            message = String(result.body.error);
            if (result.body.detail) detail = String(result.body.detail);
          } else if (result.status >= 502 && result.status <= 504) {
            // Non JSON 5xx means the reverse proxy answered, not the backend.
            message = "The shelf proxy is not responding.";
            detail = "The roulette-proxy container may be stopped or still starting up. "
              + "Status " + result.status + ".";
          } else {
            detail = "The server answered with status " + result.status + ".";
          }
          showError(message, detail);
          return;
        }
        if (!Array.isArray(result.body)) {
          showError("The proxy returned an unexpected response.", "Expected a JSON array of books.");
          return;
        }
        state.pool = result.body.filter(function (book) {
          return book && book.title;
        });
        state.winner = null;
        pickCandidates();
        renderWheelView();
      })
      .catch(function (err) {
        showError("Could not reach the shelf proxy.", err && err.message ? err.message : "");
      });
  }

  /* ---------------- events ---------------- */

  function reshuffle() {
    if (state.spinning) return;
    state.winner = null;
    show(el.viewSlip, false);
    pickCandidates();
    renderWheelView();
    announce("Reshuffled. " + state.candidates.length + " books on the wheel.");
  }

  // Label stays put and aria-pressed carries the state, which is what a toggle
  // button is meant to do. A label that swaps with the state reads as ambiguous.
  function renderModeButton() {
    var whole = state.mode === "all";
    el.btnMode.setAttribute("aria-pressed", whole ? "true" : "false");
    el.btnMode.title = whole
      ? "Every book is on the wheel. Turn this off for a smaller set with titles and cover art."
      : "A readable slice of the shelf, with titles and cover art. Turn this on for every book.";
  }

  function toggleMode() {
    if (state.spinning) return;
    state.mode = state.mode === "all" ? "set" : "all";
    store.set(MODE_KEY, state.mode);
    state.winner = null;
    show(el.viewSlip, false);
    pickCandidates();
    renderModeButton();
    renderWheelView();
    announce(
      state.mode === "all"
        ? "Whole shelf on the wheel, " + state.candidates.length + " books."
        : "Readable set, " + state.candidates.length + " books on the wheel."
    );
  }

  function dismissWinner() {
    if (!state.winner) return;
    var book = state.winner;
    state.dismissed[bookKey(book)] = true;
    saveDismissed();
    state.winner = null;
    show(el.viewSlip, false);
    pickCandidates();
    renderWheelView();
    announce(book.title + " hidden. It will not appear again until you clear the hidden list.");
  }

  function restoreAll() {
    state.dismissed = {};
    saveDismissed();
    state.winner = null;
    show(el.viewSlip, false);
    pickCandidates();
    renderWheelView();
    announce("Hidden list cleared.");
  }

  function bindEvents() {
    el.btnSpin.addEventListener("click", spin);
    el.btnReshuffle.addEventListener("click", reshuffle);
    el.btnMode.addEventListener("click", toggleMode);
    el.btnAgain.addEventListener("click", function () {
      show(el.viewSlip, false);
      spin();
    });
    el.btnReading.addEventListener("click", dismissWinner);
    el.btnRestore.addEventListener("click", restoreAll);
    el.btnEmptyRestore.addEventListener("click", restoreAll);
    el.btnRetry.addEventListener("click", function () { fetchShelf(false); });
    el.btnRefresh.addEventListener("click", function () { fetchShelf(true); });

    bindGestures();

    var resizeTimer = null;
    window.addEventListener("resize", function () {
      if (resizeTimer) window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(function () {
        if (state.pool.length === 0) return;
        // In whole shelf mode the cap never changes with viewport, so crossing
        // the 500px breakpoint must not silently reshuffle the wheel.
        var capChanged = currentCap() !== state.cap;
        var coversChanged = coversAllowed() !== state.covers;
        if (capChanged) {
          pickCandidates();
          renderRoster();
          renderStatus();
        } else if (coversChanged) {
          state.covers = coversAllowed();
          preloadCandidateCovers();
        }
        layoutCanvas();
        drawWheel();
      }, 140);
    });

    var motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    reduceMotion = motionQuery.matches;
    var onMotionChange = function (event) {
      reduceMotion = event.matches;
      if (state.candidates.length) {
        el.hint.textContent = reduceMotion
          ? "Tap the wheel or press spin to pick a book."
          : "Tap the wheel to spin, or flick it.";
      }
    };
    if (motionQuery.addEventListener) motionQuery.addEventListener("change", onMotionChange);
    else if (motionQuery.addListener) motionQuery.addListener(onMotionChange);
  }

  /* ---------------- boot ---------------- */

  function init() {
    canvas = $("wheel");
    ctx = canvas.getContext("2d");

    el = {
      announce: $("announce"),
      shelfStatus: $("shelf-status"),
      viewLoading: $("view-loading"),
      viewError: $("view-error"),
      viewEmpty: $("view-empty"),
      viewWheel: $("view-wheel"),
      viewSlip: $("view-slip"),
      errorMessage: $("error-message"),
      errorDetail: $("error-detail"),
      emptyMessage: $("empty-message"),
      hint: $("wheel-hint"),
      rosterList: $("roster-list"),
      roster: $("roster"),
      hiddenCount: $("hidden-count"),
      storageNote: $("storage-note"),
      slipTitle: $("slip-title"),
      slipAuthor: $("slip-author"),
      slipMeta: $("slip-meta"),
      slipLink: $("slip-link"),
      slipCover: $("slip-cover"),
      btnSpin: $("btn-spin"),
      btnReshuffle: $("btn-reshuffle"),
      btnMode: $("btn-mode"),
      btnReading: $("btn-reading"),
      btnAgain: $("btn-again"),
      btnRestore: $("btn-restore"),
      btnEmptyRestore: $("btn-empty-restore"),
      btnRetry: $("btn-retry"),
      btnRefresh: $("btn-refresh")
    };

    if (!store.usable()) show(el.storageNote, true);

    if (store.get(MODE_KEY) === "set") state.mode = "set";
    renderModeButton();

    loadDismissed();
    bindEvents();

    // Fonts affect spine label measurement, so redraw once they are ready.
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(function () {
        if (!state.spinning && state.candidates.length) drawWheel();
      });
    }

    fetchShelf(false);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
}());
