// paperdesk client: the PDF in pdf.js, rendered page by page as it scrolls into view, zoomed by buttons or a pinch;
// a comment from any selection or tap, typed or spoken; the thread beside it.
const pdfjsLib = await import("https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/pdf.min.mjs");
pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/pdf.worker.min.mjs";
const viewer = document.getElementById("viewer"), stage = document.getElementById("stage"), list = document.getElementById("list"), statusEl = document.getElementById("status");
const fab = document.getElementById("fab");
const MOBILE = matchMedia("(max-width: 820px)").matches;
let SCALE = 1.35, doc = null, pages = {}, comments = [], pdfMtime = null, popup = null, pending = null, observer = null, WHO = { reviewer: "reviewer", editor: "editor" };

function fitScale(vp1) {                                    // the page fills the viewer's width, never wider than 1.35x
  const avail = viewer.clientWidth - (MOBILE ? 8 : 40);
  return Math.min(1.35, Math.max(0.4, avail / vp1.width));
}

async function loadPdf() {
  stage.innerHTML = ""; pages = {}; if (observer) observer.disconnect();
  doc = await pdfjsLib.getDocument({ url: "/pdf?" + Date.now() }).promise;
  const first = await doc.getPage(1); const vp1 = first.getViewport({ scale: 1 });
  SCALE = fitScale(vp1);
  for (let n = 1; n <= doc.numPages; n++) {
    const div = document.createElement("div"); div.className = "page blank"; div.dataset.page = n;
    stage.appendChild(div);
    pages[n] = { div, page: null, vp1: null, rendered: 0, tl: null, vp: null, canvas: null };
    div.addEventListener("pointerup", (e) => { if (e.pointerType === "mouse") onRelease(e, n); });
    div.addEventListener("click", (e) => { if (MOBILE && !window.getSelection().toString().trim()) onRelease(e, n); });
  }
  for (let n = 1; n <= doc.numPages; n++) {                   // sizes first, so the scroll range is right before anything renders
    const page = n === 1 ? first : await doc.getPage(n); pages[n].page = page; pages[n].vp1 = page.getViewport({ scale: 1 });
  }
  layout();
  observer = new IntersectionObserver((entries) => {
    for (const en of entries) { const n = parseInt(en.target.dataset.page); if (en.isIntersecting) render(n); else if (Math.abs(pages[n].rendered) > 0 && far(n)) unrender(n); }
  }, { root: viewer, rootMargin: "1200px 0px" });
  for (const n in pages) observer.observe(pages[n].div);
  drawPins();
}

function layout() {                                        // every page box at the current scale; pins follow
  for (const n in pages) { const p = pages[n]; p.vp = p.page.getViewport({ scale: SCALE }); p.div.style.width = p.vp.width + "px"; p.div.style.height = p.vp.height + "px";
    if (p.rendered && p.rendered !== SCALE) unrender(n); }
  drawPins();
}

function far(n) { const r = pages[n].div.getBoundingClientRect(); return r.bottom < -3000 || r.top > window.innerHeight + 3000; }

function unrender(n) { const p = pages[n]; p.div.querySelectorAll("canvas, .textLayer, .linkLayer").forEach(x => x.remove()); p.rendered = 0; p.tl = null; p.div.classList.add("blank"); }

async function linkLayer(page, vp) {                        // one <a> per link annotation, placed on the viewport
  const layer = document.createElement("div"); layer.className = "linkLayer";
  let anns = []; try { anns = await page.getAnnotations(); } catch (e) { return layer; }
  for (const a of anns) {
    if (a.subtype !== "Link" || (!a.url && !a.dest)) continue;
    const r = vp.convertToViewportRectangle(a.rect); const x = Math.min(r[0], r[2]), y = Math.min(r[1], r[3]);
    const el = document.createElement("a"); el.className = "plink";
    el.style.left = x + "px"; el.style.top = y + "px"; el.style.width = Math.abs(r[2] - r[0]) + "px"; el.style.height = Math.abs(r[3] - r[1]) + "px";
    if (a.url) { el.href = a.url; el.target = "_blank"; el.rel = "noopener"; el.title = a.url; }
    else { el.href = "#"; el.title = "go to " + (typeof a.dest === "string" ? a.dest : "reference"); el.onclick = async (ev) => { ev.preventDefault(); await goToDest(a.dest); }; }
    layer.appendChild(el);
  }
  return layer;
}

async function goToDest(dest) {                             // an internal reference: scroll to its page and, when given, its height
  try {
    const d = typeof dest === "string" ? await doc.getDestination(dest) : dest; if (!d) return;
    const idx = await doc.getPageIndex(d[0]); const p = pages[idx + 1]; if (!p) return;
    const kind = d[1] && d[1].name; let topPdf = null;            // the destination's height on the page, PDF units from the bottom
    if (kind === "XYZ" && typeof d[3] === "number") topPdf = d[3];
    else if ((kind === "FitH" || kind === "FitBH") && typeof d[2] === "number") topPdf = d[2];
    const yIn = topPdf === null ? 0 : (p.vp1.height - topPdf) * SCALE * currentCssScale();
    const vr = viewer.getBoundingClientRect(), pr = p.div.getBoundingClientRect();
    viewer.scrollTo({ top: viewer.scrollTop + (pr.top - vr.top) + yIn - 16, behavior: "smooth" });
  } catch (e) {}
}

async function render(n) {
  const p = pages[n]; if (p.rendered === SCALE || p.rendering) return;
  p.rendering = true; const vp = p.vp;
  const canvas = document.createElement("canvas"); const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(vp.width * dpr); canvas.height = Math.round(vp.height * dpr);
  const tl = document.createElement("div"); tl.className = "textLayer";
  const ctx = canvas.getContext("2d"); ctx.scale(dpr, dpr);
  await p.page.render({ canvasContext: ctx, viewport: vp }).promise;
  const tc = await p.page.getTextContent();
  for (const item of tc.items) {                             // a plain text layer: one span per item, placed on the viewport
    if (!item.str) continue;
    const t = pdfjsLib.Util.transform(vp.transform, item.transform); const fontH = Math.hypot(t[2], t[3]);
    const s = document.createElement("span"); s.textContent = item.str; s.dataset.w = item.width;
    s.style.left = t[4] + "px"; s.style.top = (t[5] - fontH) + "px"; s.style.fontSize = fontH + "px"; s.style.fontFamily = "sans-serif";
    tl.appendChild(s);
  }
  const links = await linkLayer(p.page, vp);                   // the PDF's own links: URLs open, references jump
  if (vp !== p.vp) { p.rendering = false; return; }            // the scale moved while rendering: the observer renders again
  p.div.querySelectorAll("canvas, .textLayer, .linkLayer").forEach(x => x.remove());
  p.div.prepend(links); p.div.prepend(tl); p.div.prepend(canvas); p.div.classList.remove("blank");
  for (const s of tl.children) { const w = s.getBoundingClientRect().width / currentCssScale(); if (w > 0 && s.dataset.w) s.style.transform = `scaleX(${(parseFloat(s.dataset.w) * SCALE) / w})`; }
  p.tl = tl; p.rendered = SCALE; p.rendering = false;
}

let cssScale = 1;                                          // a transient CSS zoom during a pinch, before pages re-render
function currentCssScale() { return cssScale; }

function setScale(next, focus) {                           // focus: {x, y} in viewer client coordinates kept fixed
  next = Math.max(0.4, Math.min(4, next)); if (next === SCALE) return;
  const r = viewer.getBoundingClientRect(); const fx = focus ? focus.x - r.left : r.width / 2, fy = focus ? focus.y - r.top : r.height / 2;
  const docX = (viewer.scrollLeft + fx) / SCALE, docY = (viewer.scrollTop + fy) / SCALE;
  SCALE = next; layout();
  viewer.scrollLeft = docX * SCALE - fx; viewer.scrollTop = docY * SCALE - fy;
  for (const n in pages) if (!far(n)) render(n);
}

// pinch: two fingers scale the stage visually; when they lift, the pages re-render at the new scale
let pinch = null;
viewer.addEventListener("touchstart", (e) => { if (e.touches.length === 2) { const [a, b] = e.touches; pinch = { d0: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY), cx: (a.clientX + b.clientX) / 2, cy: (a.clientY + b.clientY) / 2, f: 1 }; closePopup(); } }, { passive: true });
viewer.addEventListener("touchmove", (e) => {
  if (!pinch || e.touches.length !== 2) return; e.preventDefault();
  const [a, b] = e.touches; pinch.f = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY) / pinch.d0;
  const r = viewer.getBoundingClientRect(); const ox = viewer.scrollLeft + (pinch.cx - r.left), oy = viewer.scrollTop + (pinch.cy - r.top);
  cssScale = pinch.f; stage.style.transformOrigin = `${ox}px ${oy}px`; stage.style.transform = `scale(${pinch.f})`;
}, { passive: false });
viewer.addEventListener("touchend", (e) => {
  if (!pinch) return; const f = pinch.f, cx = pinch.cx, cy = pinch.cy; pinch = null; cssScale = 1; stage.style.transform = "";
  if (Math.abs(f - 1) > 0.02) setScale(SCALE * f, { x: cx, y: cy });
}, { passive: true });
document.getElementById("zoomin").onclick = () => setScale(SCALE * 1.25);
document.getElementById("zoomout").onclick = () => setScale(SCALE / 1.25);

function pageOfNode(node) { const el = (node.nodeType === 1 ? node : node.parentElement)?.closest(".page"); return el ? parseInt(el.dataset.page) : null; }

function toPt(n, clientX, clientY) {                    // viewport pixels -> TeX points from the page's top-left
  const r = pages[n].div.getBoundingClientRect();
  return { x_pt: (clientX - r.left) / SCALE, y_pt: (clientY - r.top) / SCALE, xv: clientX - r.left, yv: clientY - r.top };
}

function closePopup() { if (popup) { popup.remove(); popup = null; } stopRecorder(); }

function selectionInfo() {
  const sel = window.getSelection(); const quote = sel ? sel.toString().trim() : "";
  if (!quote || !sel.rangeCount) return null;
  const range = sel.getRangeAt(0); const n = pageOfNode(range.startContainer); if (!n) return null;
  const rr = range.getBoundingClientRect(); const pr = pages[n].div.getBoundingClientRect();
  return { n, quote, rect: { x: (rr.left - pr.left) / SCALE, y: (rr.top - pr.top) / SCALE, w: rr.width / SCALE, h: rr.height / SCALE },
           at: toPt(n, rr.left, rr.top + rr.height / 2) };
}

async function onRelease(e, n) {
  if (e.target.closest(".pin") || e.target.closest(".plink") || e.target.closest("#popup") || pinch) return;
  const si = selectionInfo();
  if (si) return openPopup(si.n, si.at, si.quote, si.rect);
  return openPopup(n, toPt(n, e.clientX, e.clientY), "", null);
}

// ---- voice: MediaRecorder for the note, the browser's dictation into the textarea when it has one
let rec = null, VOICE = null;                            // VOICE: the server's verdict on transcription, from /api/status
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
function micButton(ta, onBlob) {
  const b = document.createElement("button"); b.className = "mic"; b.textContent = SR ? "🎤 speak" : "🎤 record"; b.type = "button";
  if (VOICE && !VOICE.ok) { b.textContent += SR ? " (dictation only)" : " (no transcriber)"; b.title = VOICE.reason; }
  let chunks = [], mr = null, sr = null, t0 = 0, timer = null;
  b.onclick = async () => {
    if (mr) {                                               // stop
      mr.stop(); if (sr) sr.stop(); clearInterval(timer); return;
    }
    let stream; try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch (err) { alert("no microphone: " + err.message); return; }
    chunks = []; mr = new MediaRecorder(stream); rec = { mr, stream };
    mr.ondataavailable = (ev) => { if (ev.data.size) chunks.push(ev.data); };
    mr.onstop = () => { stream.getTracks().forEach(t => t.stop()); const blob = new Blob(chunks, { type: mr.mimeType || "audio/webm" }); mr = null; rec = null;
      b.classList.remove("rec"); b.textContent = "🎤 re-record"; onBlob(blob); };
    mr.start(250); t0 = Date.now(); b.classList.add("rec");
    timer = setInterval(() => { b.textContent = `■ stop ${Math.round((Date.now() - t0) / 1000)} s`; }, 500);
    if (SR) {                                               // live words, editable, kept as the dictation beside the audio
      sr = new SR(); sr.continuous = true; sr.interimResults = true; sr.lang = navigator.language || "en-US";
      const base = ta.value; let finalText = "";
      sr.onresult = (ev) => { let interim = ""; for (let i = ev.resultIndex; i < ev.results.length; i++) { const r = ev.results[i]; if (r.isFinal) finalText += r[0].transcript + " "; else interim += r[0].transcript; }
        ta.value = (base ? base + " " : "") + finalText + interim; };
      sr.onerror = () => {}; try { sr.start(); } catch (e) {}
    }
  };
  return b;
}
function stopRecorder() { if (rec) { try { rec.mr.stop(); } catch (e) {} rec = null; } }

async function api(path, init) {                       // a failed request is shown, never swallowed
  const r = await fetch(path, init);
  let body = null; try { body = await r.json(); } catch (e) { body = null; }
  if (!r.ok) { const msg = (body && body.error) || `${r.status} ${r.statusText}`; statusEl.textContent = "failed: " + msg; alert("paperdesk: " + msg); throw new Error(msg); }
  return body;
}

async function postVoice(blob, params) {
  const q = new URLSearchParams(params).toString();
  return api("/api/voice?" + q, { method: "POST", headers: { "Content-Type": blob.type || "audio/webm" }, body: blob });
}

async function openPopup(n, at, quote, rect) {
  closePopup(); fab.style.display = "none";
  const anchor = await (await fetch("/api/resolve", { method: "POST", body: JSON.stringify({ page: n, x_pt: at.x_pt, y_pt: at.y_pt, quote }) })).json();
  popup = document.createElement("div"); popup.id = "popup";
  const pw = Math.min(320, window.innerWidth * 0.9);
  popup.style.left = Math.max(4, Math.min(at.xv + 12, pages[n].vp.width - pw - 4)) + "px"; popup.style.top = (at.yv + 12) + "px";
  const where = anchor.file && anchor.line ? `${anchor.file}:${anchor.unit === "paragraph" ? "¶" : ""}${anchor.line}\n${anchor.section || ""}${anchor.float ? " [" + anchor.float + " " + (anchor.label || "") + "]" : ""}` : "unresolved (" + (anchor.error || "") + ")";
  popup.innerHTML = `<div class="where">${escapeHtml(where)}</div>` + (quote ? `<div class="where">“${escapeHtml(quote.slice(0, 160))}”</div>` : "") +
    `<textarea placeholder="your comment, typed or spoken"></textarea><div class="row"></div>`;
  pages[n].div.appendChild(popup);
  const ta = popup.querySelector("textarea"); const row = popup.querySelector(".row"); let blob = null;
  const mic = micButton(ta, (b) => { blob = b; });
  const cancel = document.createElement("button"); cancel.textContent = "cancel"; cancel.onclick = closePopup;
  const save = document.createElement("button"); save.textContent = "save"; save.className = "primary";
  row.append(mic, cancel, save);
  save.onclick = async () => {
    const text = ta.value.trim(); if (!text && !blob) return;
    if (blob) { const r = await postVoice(blob, { page: n, x_pt: at.x_pt, y_pt: at.y_pt, quote, rect: rect ? JSON.stringify(rect) : "", dictation: text }); if (r && r.warning) alert("paperdesk: " + r.warning); }
    else await api("/api/comments", { method: "POST", body: JSON.stringify({ page: n, x_pt: at.x_pt, y_pt: at.y_pt, quote, rect, text }) });
    closePopup(); window.getSelection().removeAllRanges(); await refresh();
  };
  if (!MOBILE) ta.focus();
  ta.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) save.click(); });
}

// on a phone the selection handles are the interaction; a floating button turns the selection into a comment
document.addEventListener("selectionchange", () => {
  if (!MOBILE || popup) return;
  const si = selectionInfo();
  if (!si) { fab.style.display = "none"; pending = null; return; }
  pending = si;
  const rr = window.getSelection().getRangeAt(0).getBoundingClientRect();
  fab.style.display = "block"; fab.style.left = Math.max(8, Math.min(rr.left, window.innerWidth - 120)) + "px";
  fab.style.top = Math.min(window.innerHeight - 60, rr.bottom + 10) + "px";
});
fab.addEventListener("click", () => { if (pending) openPopup(pending.n, pending.at, pending.quote, pending.rect); });

// resolved comments hidden from the page and the list (the default; remembered per browser)
const hideBox = document.getElementById("hideres");
let hideResolved = true; try { hideResolved = localStorage.getItem("paperdesk.hideResolved") !== "0"; } catch (e) {}
hideBox.checked = hideResolved;
hideBox.onchange = () => { hideResolved = hideBox.checked; try { localStorage.setItem("paperdesk.hideResolved", hideResolved ? "1" : "0"); } catch (e) {} drawPins(); renderList(); };
const shown = (c) => !(hideResolved && c.status === "resolved");

function drawPins() {
  for (const n in pages) { pages[n].div.querySelectorAll(".pin, .mark").forEach(x => x.remove()); }
  for (const c of comments) {
    const p = pages[c.page]; if (!p || !shown(c)) continue;
    if (c.rect) { const m = document.createElement("div"); m.className = "mark";
      m.style.left = c.rect.x * SCALE + "px"; m.style.top = c.rect.y * SCALE + "px"; m.style.width = c.rect.w * SCALE + "px"; m.style.height = c.rect.h * SCALE + "px"; p.div.appendChild(m); }
    const pin = document.createElement("div"); pin.className = "pin" + (c.status === "resolved" ? " resolved" : ""); pin.textContent = c.id;
    pin.style.left = c.x_pt * SCALE + "px"; pin.style.top = c.y_pt * SCALE + "px"; pin.title = c.text;
    pin.onclick = (ev) => { ev.stopPropagation(); if (MOBILE) document.body.classList.add("side"); document.getElementById("c" + c.id)?.scrollIntoView({ behavior: "smooth", block: "center" }); };
    p.div.appendChild(pin);
  }
}

function audioHtml(x) { return x.audio ? `<audio controls preload="none" src="/audio/${x.audio}"></audio>` + (x.transcribed ? `<div class="dict">transcribed: ${escapeHtml(x.transcribed)}${x.dictation && x.dictation !== x.text ? " · dictated: " + escapeHtml(x.dictation) : ""}</div>` : "") : ""; }

function renderList() {
  list.innerHTML = "";
  const hidden = comments.filter(c => !shown(c)).length;
  if (hidden) { const h = document.createElement("div"); h.className = "hint"; h.style.display = "block"; h.textContent = `${hidden} resolved hidden`; list.appendChild(h); }
  for (const c of [...comments].sort((a, b) => b.id - a.id)) {
    if (!shown(c)) continue;
    const a = c.anchor || {}; const d = document.createElement("div"); d.className = "c " + c.status; d.id = "c" + c.id;
    const where = a.file && a.line ? `${a.file}:${a.unit === "paragraph" ? "¶" : ""}${a.line} · ${a.section || ""}${a.float ? " · " + a.float + " " + (a.label || "") : ""}` : `page ${c.page}, unresolved`;
    d.innerHTML = `<div class="where">#${c.id} · ${c.created} · ${escapeHtml(where)}</div>` + (c.quote ? `<div class="quote">${escapeHtml(c.quote.slice(0, 220))}</div>` : "") +
      `<div>${escapeHtml(c.text)}</div>${audioHtml(c)}` + (c.replies || []).map(r => `<div class="reply ${r.author === WHO.editor ? "claude" : ""}"><b>${r.author}</b> · ${r.created}<br>${escapeHtml(r.text)}${audioHtml(r)}</div>`).join("") +
      `<textarea rows="2" placeholder="reply, typed or spoken"></textarea><div class="actions"><button data-a="reply">reply</button>` +
      (c.status === "open" ? `<button data-a="resolve">resolve</button>` : `<button data-a="reopen">reopen</button>`) + `<button data-a="goto">go to</button><button data-a="delete">delete</button></div>`;
    const ta = d.querySelector("textarea"); let blob = null;
    d.querySelector(".actions").prepend(micButton(ta, (b) => { blob = b; }));
    d.querySelectorAll("button[data-a]").forEach(b => b.onclick = async () => {
      const act = b.dataset.a;
      if (act === "goto") { if (MOBILE) document.body.classList.remove("side"); pages[c.page]?.div.scrollIntoView({ behavior: "smooth", block: "start" }); viewer.scrollBy(0, c.y_pt * SCALE - 120); return; }
      if (act === "delete" && !confirm("delete comment #" + c.id + "?")) return;
      if (act === "reply") {
        const text = ta.value.trim(); if (!text && !blob) return;
        if (blob) { const r = await postVoice(blob, { reply_to: c.id, author: WHO.reviewer, dictation: text }); if (r && r.warning) alert("paperdesk: " + r.warning); }
        else await fetch(`/api/comments/${c.id}/reply`, { method: "POST", body: JSON.stringify({ author: WHO.reviewer, text }) });
      } else {
        await fetch(`/api/comments/${c.id}/${act}`, { method: "POST", body: "{}" });
      }
      await refresh();
    });
    list.appendChild(d);
  }
}

function escapeHtml(s) { return (s || "").replace(/[&<>]/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[ch])); }

let lastJson = "";
async function refresh() {
  comments = await (await fetch("/api/comments")).json();
  const st = await (await fetch("/api/status")).json();
  document.getElementById("paper").textContent = st.paper; WHO = { reviewer: st.reviewer, editor: st.editor }; VOICE = st.voice || null;
  if (st.kind === "docx") document.querySelector("#side .hint").textContent = "Select words on the page, or click a figure or a table, then write. Each comment is anchored to the paragraph of the Word document those words come from, its heading path and the table or image beside it.";
  statusEl.textContent = (st.build.running ? "building… " : (st.build.ok === false ? "build FAILED " : "")) + `${st.n_open} open` + (st.watched ? "" : " · unwatched");
  statusEl.title = st.watched ? `${st.editor} is watching` : `nobody is watching this desk: comments are saved but ${st.editor} will not see them until "desk.py watch" runs`;
  if (st.build.ok === false) console.warn(st.build.log);
  if (pdfMtime !== null && st.pdf_mtime !== pdfMtime && !st.build.running) { pdfMtime = st.pdf_mtime; await loadPdf(); }
  pdfMtime = st.pdf_mtime;
  const j = JSON.stringify(comments);
  if (j !== lastJson && !rec) { lastJson = j; renderList(); drawPins(); }   // never rebuild the list mid-recording
  document.getElementById("toggle").textContent = `comments (${st.n_open})`;
}

document.getElementById("rebuild").onclick = async () => { await fetch("/api/rebuild", { method: "POST" }); statusEl.textContent = "building…"; };
document.getElementById("reload").onclick = loadPdf;
document.getElementById("toggle").onclick = () => document.body.classList.toggle("side");
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePopup(); });
window.__desk = { get pages() { return pages; }, get comments() { return comments; }, get scale() { return SCALE; }, openPopup, refresh, toPt, setScale };
await loadPdf(); await refresh(); setInterval(refresh, 4000);
