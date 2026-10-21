/**
 * Keep journey stage sizes consistent (Proof → Demo → Value → Calendar).
 * Layout tuner UI only with ?tune=1.
 */
(function () {
  const shell = document.querySelector(".assessment-shell--journey, .assessment-shell--result");
  const card = document.querySelector(".assessment-result");
  if (!shell || !card) return;

  const params = new URLSearchParams(window.location.search);
  const tuneOn = params.get("tune") === "1";

  const LOCKED = {
    width_px: 1658,
    card_min_height_px: 821,
  };

  function applySize(widthPx, cardMinH) {
    const w = Math.round(Number(widthPx) || LOCKED.width_px);
    const h = Math.round(Number(cardMinH) || LOCKED.card_min_height_px);
    shell.style.width = w + "px";
    shell.style.maxWidth = "none";
    card.style.minHeight = h + "px";
    return { width_px: w, card_min_height_px: h };
  }

  // Same size on every stage so Proof → Demo does not jump.
  applySize(LOCKED.width_px, LOCKED.card_min_height_px);

  fetch("/api/dev/layout-tune", {
    headers: (function () {
      var meta = document.querySelector('meta[name="csrf-token"]');
      var headers = {};
      if (meta && meta.content) headers["X-CSRF-Token"] = meta.content;
      return headers;
    })(),
  })
    .then(function (r) {
      return r.json();
    })
    .then(function (data) {
      if (data && data.ok && data.saved) {
        applySize(data.saved.width_px, data.saved.card_min_height_px);
      }
    })
    .catch(function () {});

  if (!tuneOn) return;

  card.classList.add("fb-layout-tune-card");

  const handle = document.createElement("div");
  handle.className = "fb-layout-tune-handle";
  handle.title = "Drag to resize width & card height";
  shell.appendChild(handle);

  const hud = document.createElement("div");
  hud.className = "fb-layout-tune-hud";
  hud.innerHTML =
    "<strong>Layout tuner</strong>" +
    '<div class="fb-layout-tune-vals"></div>' +
    '<button type="button">Save for agent</button>' +
    '<div class="fb-layout-tune-status">Drag the teal handle on the bottom-right.</div>';
  document.body.appendChild(hud);

  const valsEl = hud.querySelector(".fb-layout-tune-vals");
  const statusEl = hud.querySelector(".fb-layout-tune-status");
  const saveBtn = hud.querySelector("button");

  function measure() {
    const shellRect = shell.getBoundingClientRect();
    const cardRect = card.getBoundingClientRect();
    const width = Math.round(shellRect.width);
    const minHeight = Math.round(cardRect.height);
    valsEl.textContent = "width " + width + "px · card height " + minHeight + "px";
    return {
      width_px: width,
      card_min_height_px: minHeight,
      page: location.pathname,
      viewport_width: window.innerWidth,
      viewport_height: window.innerHeight,
    };
  }

  function save(reason) {
    const payload = measure();
    statusEl.textContent = "Saving…";
    fetch("/api/dev/layout-tune", {
      method: "POST",
      headers: (function () {
        var headers = { "Content-Type": "application/json" };
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) headers["X-CSRF-Token"] = meta.content;
        return headers;
      })(),
      body: JSON.stringify(payload),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { res: res, data: data };
        });
      })
      .then(function (out) {
        if (!out.res.ok || !out.data.ok) throw new Error(out.data.error || "save failed");
        statusEl.textContent =
          "Saved " + payload.width_px + "×" + payload.card_min_height_px + " (" + reason + "). Tell the agent.";
        window.__FB_LAYOUT__ = payload;
        applySize(payload.width_px, payload.card_min_height_px);
      })
      .catch(function (err) {
        statusEl.textContent = "Save failed: " + (err.message || err);
      });
  }

  var dragging = false;
  var startX = 0;
  var startY = 0;
  var startW = 0;
  var startH = 0;

  handle.addEventListener("pointerdown", function (ev) {
    ev.preventDefault();
    dragging = true;
    handle.classList.add("is-dragging");
    handle.setPointerCapture(ev.pointerId);
    var shellRect = shell.getBoundingClientRect();
    var cardRect = card.getBoundingClientRect();
    startX = ev.clientX;
    startY = ev.clientY;
    startW = shellRect.width;
    startH = cardRect.height;
  });

  handle.addEventListener("pointermove", function (ev) {
    if (!dragging) return;
    var dx = ev.clientX - startX;
    var dy = ev.clientY - startY;
    var nextW = Math.max(480, Math.min(window.innerWidth - 32, startW + dx * 2));
    var nextH = Math.max(280, Math.min(window.innerHeight * 0.92, startH + dy));
    applySize(nextW, nextH);
    measure();
  });

  function endDrag(ev) {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove("is-dragging");
    try {
      handle.releasePointerCapture(ev.pointerId);
    } catch (e) {}
    save("drag end");
  }

  handle.addEventListener("pointerup", endDrag);
  handle.addEventListener("pointercancel", endDrag);
  saveBtn.addEventListener("click", function () {
    save("button");
  });

  measure();
})();
