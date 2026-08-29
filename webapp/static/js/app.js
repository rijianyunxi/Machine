/* Shared JS helpers for the panel (vanilla, no framework). */

const PAGE_LABELS = {
  dashboard: "总览", cameras: "相机管理", models: "模型管理", rules: "规则配置",
  detect: "检测测试台", alerts: "告警记录", snapshots: "快照库",
  settings: "系统设置", logs: "日志",
};

/* ---------------- fetch (with 401 -> styled login modal) ---------------- */

let _loginPromise = null;

function showLoginModal() {
  if (_loginPromise) return _loginPromise;
  _loginPromise = new Promise(resolve => {
    const mask = document.createElement("div");
    mask.className = "modal-mask open";
    mask.innerHTML = `<div class="modal" style="width:380px">
      <div class="modal-h"><h3>登录面板</h3></div>
      <div class="modal-b">
        <label>用户名</label>
        <input id="lm-user" style="width:100%" autocomplete="username">
        <label>密码</label>
        <input id="lm-pass" style="width:100%" type="password"
          autocomplete="current-password">
        <p class="muted" id="lm-err" style="color:var(--red);margin-top:10px;display:none"></p>
      </div>
      <div class="modal-f"><button id="lm-go" style="width:100%">登 录</button></div>
    </div>`;
    document.body.appendChild(mask);
    const done = ok => { mask.remove(); _loginPromise = null; resolve(ok); };
    const submit = async () => {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: mask.querySelector("#lm-user").value.trim(),
          password: mask.querySelector("#lm-pass").value,
        }),
      });
      if (res.ok) return done(true);
      const err = mask.querySelector("#lm-err");
      err.textContent = (await res.json()).detail || "登录失败";
      err.style.display = "block";
    };
    mask.querySelector("#lm-go").onclick = submit;
    mask.querySelector("#lm-pass").addEventListener("keydown",
      e => { if (e.key === "Enter") submit(); });
    mask.querySelector("#lm-user").focus();
  });
  return _loginPromise;
}

async function api(path, opts = {}) {
  const send = () => fetch(path, {
    headers: opts.body !== undefined && !(opts.body instanceof FormData)
      ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: opts.body !== undefined && !(opts.body instanceof FormData)
      ? JSON.stringify(opts.body) : opts.body,
  });
  let res = await send();
  if (res.status === 401 && !path.startsWith("/api/login")) {
    if (await showLoginModal()) res = await send();
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

/* sidebar footer system status */
async function initSysStatus() {
  try {
    const d = await api("/api/system/info");
    const dot = document.getElementById("sys-dot");
    const el = document.getElementById("sys-mode");
    if (!dot || !el) return;
    dot.classList.add(d.mode.startsWith("standalone") ? "yellow" : "green");
    el.textContent = d.mode.startsWith("standalone") ? "独立只读模式" : "检测系统运行中";
  } catch {
    const dot = document.getElementById("sys-dot");
    const el = document.getElementById("sys-mode");
    if (dot) dot.classList.add("red");
    if (el) el.textContent = "API 不可达";
  }
}
if (document.getElementById("sys-mode")) initSysStatus();

/* button loading state helper */
async function withLoading(btn, fn) {
  if (!btn) return fn();
  btn.classList.add("loading");
  btn.disabled = true;
  try { return await fn(); }
  finally { btn.classList.remove("loading"); btn.disabled = false; }
}

/* ---------------- format ---------------- */

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function tsToTime(ts) {
  return ts ? new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false }) : "-";
}

function relTime(ts) {
  if (!ts) return "";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
  if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
  return Math.floor(diff / 86400) + " 天前";
}

/* ---------------- chips / badges ---------------- */

function badge(text, color) {
  return `<span class="chip ${color || "plain"}">${esc(text)}</span>`;
}

function statusBadge(status) {
  const map = {
    new: ["新告警", "blue"], confirmed: ["确认违规", "red"],
    false_positive: ["误报", "yellow"], resolved: ["已处理", "green"],
  };
  const [t, c] = map[status] || [status, "plain"];
  return badge(t, c);
}

function connectedBadge(cam) {
  if (!cam.enabled) return `<span class="chip plain"><span class="dot"></span>停用</span>`;
  if (cam.connected) return `<span class="chip green"><span class="dot green pulse"></span>在线</span>`;
  if (cam.thread_alive) return `<span class="chip yellow"><span class="dot yellow pulse"></span>重连中</span>`;
  return `<span class="chip red"><span class="dot red"></span>离线</span>`;
}

/* ---------------- SVG bar chart ---------------- */

/* Renders a bar chart that always fills its container.
 * data items: { label, value } or stacked { label, value,
 *   segments: [{ v, c, name }] } (bottom-up).
 * height: number | "fill" — "fill" stretches to the element's height.
 * Safe to call repeatedly (polling): the ResizeObserver is created once. */
function renderBars(el, data, opts = {}) {
  if (!el) return;
  el._barsData = data;
  el._barsOpts = opts;
  drawBarsIn(el);
  if (!el._barsRO) {
    let raf = 0;
    el._barsRO = new ResizeObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => drawBarsIn(el));
    });
    el._barsRO.observe(el);
  }
}

function drawBarsIn(el) {
  const data = el._barsData, { height: height = 170 } = el._barsOpts || {};
  const w = el.clientWidth, fill = height === "fill";
  const h = fill ? Math.max(el.clientHeight, 170) : height;
  if (!w || !data || !data.length || data.every(d => !d.value)) {
    el.innerHTML = data && data.length
      ? `<div class="empty"><p>暂无告警数据</p></div>` : "";
    return;
  }
  const pad = { l: 34, r: 10, t: 20, b: 26 };
  const max = Math.max(...data.map(d => d.value), 1);
  /* round the axis max up to a nice number so gridlines read cleanly */
  const p = Math.pow(10, Math.floor(Math.log10(max)));
  const top = [1, 2, 2.5, 5, 10].find(k => k * p >= max) * p;
  const iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
  const bw = iw / data.length;
  let g = "";
  for (let i = 0; i <= 2; i++) {
    const y = pad.t + ih - (ih * i / 2);
    g += `<line x1="${pad.l}" y1="${y}" x2="${w - pad.r}" y2="${y}"
      stroke="rgba(148,163,184,.09)"></line>
      <text x="${pad.l - 7}" y="${y + 3.5}" fill="var(--muted)" font-size="10"
      text-anchor="end">${Math.round(top * i / 2)}</text>`;
  }
  let bars = "";
  data.forEach((d, i) => {
    const cx = pad.l + i * bw;
    let yBottom = pad.t + ih;
    if (d.segments) {
      const shown = d.segments.filter(s => s.v);
      shown.forEach((s, si) => {
        const sh = Math.max(s.v / top * ih, 2);
        yBottom -= sh;
        const rx = si === shown.length - 1 ? ` rx="2.5"` : "";
        bars += `<rect x="${(cx + bw * 0.14).toFixed(1)}" y="${yBottom.toFixed(1)}"
          width="${(bw * 0.72).toFixed(1)}" height="${sh.toFixed(1)}"${rx}
          fill="${s.c}"><title>${esc(d.label)}：${esc(s.name)} ${s.v} 条</title></rect>`;
      });
    } else {
      const bh = Math.max((d.value / top) * ih, d.value ? 3 : 0);
      yBottom = pad.t + ih - bh;
      bars += `<rect x="${(cx + bw * 0.14).toFixed(1)}" y="${yBottom.toFixed(1)}"
        width="${(bw * 0.72).toFixed(1)}" height="${bh.toFixed(1)}" rx="3"
        fill="url(#grad)"><title>${esc(d.label)}：${d.value} 条</title></rect>`;
    }
    if (d.value) {
      bars += `<text x="${(cx + bw / 2).toFixed(1)}" y="${(yBottom - 5).toFixed(1)}"
        fill="var(--text-2)" font-size="10" font-weight="600"
        text-anchor="middle">${d.value}</text>`;
    }
    bars += `<text x="${(cx + bw / 2).toFixed(1)}" y="${h - 8}"
      fill="var(--muted)" font-size="10"
      text-anchor="middle">${esc(String(d.label).slice(5))}</text>`;
  });
  el.innerHTML = `<svg width="100%" height="${h}" viewBox="0 0 ${w} ${h}"
    preserveAspectRatio="none">
    <defs><linearGradient id="grad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#22d3ee"/><stop offset="100%" stop-color="#4d9fff"/>
    </linearGradient></defs>${g}${bars}</svg>`;
}

/* ---------------- toast ---------------- */

function toast(msg, ok = true) {
  let root = document.getElementById("toast-root");
  if (!root) {
    root = document.createElement("div");
    root.id = "toast-root";
    document.body.appendChild(root);
  }
  const t = document.createElement("div");
  t.className = "toast" + (ok ? "" : " err");
  t.innerHTML = `<span>${ok ? "✓" : "✕"}</span><span>${esc(msg)}</span>`;
  root.appendChild(t);
  setTimeout(() => {
    t.style.transition = "opacity .25s"; t.style.opacity = "0";
    setTimeout(() => t.remove(), 260);
  }, 2600);
}

/* ---------------- styled confirm dialog ---------------- */

function confirmDialog(message, { danger = true, okText = "确认" } = {}) {
  return new Promise(resolve => {
    let root = document.getElementById("confirm-root");
    if (!root) {
      root = document.createElement("div");
      root.id = "confirm-root";
      root.className = "modal-mask";
      root.innerHTML = `<div class="modal" style="width:400px">
        <div class="modal-b" style="padding-top:22px">
          <p id="confirm-msg" style="font-size:14px;line-height:1.7"></p>
        </div>
        <div class="modal-f">
          <button class="ghost" id="confirm-no">取消</button>
          <button id="confirm-yes"></button>
        </div></div>`;
      document.body.appendChild(root);
    }
    root.querySelector("#confirm-msg").textContent = message;
    const yes = root.querySelector("#confirm-yes");
    yes.textContent = okText;
    yes.className = danger ? "danger" : "";
    root.classList.add("open");
    const done = v => { root.classList.remove("open"); resolve(v); };
    yes.onclick = () => done(true);
    root.querySelector("#confirm-no").onclick = () => done(false);
    root.onclick = e => { if (e.target === root) done(false); };
  });
}

/* Esc closes any open modal */
document.addEventListener("keydown", e => {
  if (e.key === "Escape") {
    document.querySelectorAll(".modal-mask.open").forEach(m => m.classList.remove("open"));
  }
  if (e.key === "ArrowLeft") galleryNav(-1);
  if (e.key === "ArrowRight") galleryNav(1);
});

/* ---------------- image lightbox (no new tabs) ---------------- */

/* showGallery(items, index) — items: [{src, title}]; arrow keys and on-screen
 * arrows page through the set, ±1 neighbours are preloaded.
 * showImageModal(src, title) is the single-image convenience wrapper. */
let _gallery = { items: [], index: 0 };

function showGallery(items, index = 0) {
  if (!items || !items.length) return;
  let m = document.getElementById("img-modal");
  if (!m) {
    m = document.createElement("div");
    m.id = "img-modal";
    m.className = "modal-mask img-modal";
    m.innerHTML = `<figure class="img-lightbox">
      <button class="lightbox-x" title="关闭 (Esc)">✕</button>
      <button class="lb-arrow prev" title="上一张 (←)">‹</button>
      <button class="lb-arrow next" title="下一张 (→)">›</button>
      <div class="lb-spinner"></div>
      <img alt="快照预览"><figcaption></figcaption></figure>`;
    m.addEventListener("click", () => m.classList.remove("open"));
    document.body.appendChild(m);
  }
  _gallery = { items, index };
  m.classList.add("open");   // _renderGallery only renders an open modal
  _renderGallery();
}

function showImageModal(src, title = "") {
  showGallery([{ src, title }], 0);
}

function _renderGallery() {
  const m = document.getElementById("img-modal");
  if (!m || !m.classList.contains("open")) return;
  const { items, index } = _gallery;
  const it = items[index];
  const img = m.querySelector("img");
  const spinner = m.querySelector(".lb-spinner");
  spinner.style.display = "";
  img.onload = () => { spinner.style.display = "none"; };
  if (img.src === it.src && img.complete) spinner.style.display = "none";
  img.src = it.src;
  const many = items.length > 1;
  m.querySelector(".lb-arrow.prev").style.display = many ? "" : "none";
  m.querySelector(".lb-arrow.next").style.display = many ? "" : "none";
  const cap = m.querySelector("figcaption");
  cap.textContent = it.title || "";
  if (many) cap.textContent = `${it.title ? it.title + " · " : ""}${index + 1} / ${items.length}`;
  cap.style.display = cap.textContent ? "" : "none";
  if (many) {  // preload neighbours for snappy paging
    [index - 1, index + 1].forEach(i => {
      const n = items[(i + items.length) % items.length];
      if (n) new Image().src = n.src;
    });
  }
}

function galleryNav(delta) {
  const m = document.getElementById("img-modal");
  if (!m || !m.classList.contains("open") || _gallery.items.length < 2) return;
  const n = _gallery.items.length;
  _gallery.index = (_gallery.index + delta + n) % n;
  _renderGallery();
}

function poll(fn, ms) { fn(); return setInterval(fn, ms); }

/* keep --ph-h in sync with the sticky page-head height (wrap-aware) */
(function () {
  const ph = document.querySelector(".page-head");
  if (!ph || !document.querySelector(".filter-bar")) return;
  const update = () =>
    document.documentElement.style.setProperty("--ph-h", ph.offsetHeight + "px");
  update();
  window.addEventListener("resize", update);
})();
