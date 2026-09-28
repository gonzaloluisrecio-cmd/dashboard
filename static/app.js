// Generic renderer: every widget returns {sections: [...]} (see app/widgets/base.py),
// so adding a widget on the server needs no changes here.
"use strict";

const grid = document.getElementById("grid");
const tpl = document.getElementById("card-tpl");
const cards = new Map(); // id -> {el, timer, spec}

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : null);

function renderText(text) {
  // "- " lines become a bullet list; everything else stays as paragraphs.
  const out = [];
  let list = [];
  const flush = () => {
    if (list.length) out.push(`<ul>${list.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>`);
    list = [];
  };
  for (const line of String(text).split("\n")) {
    const m = line.match(/^\s*[-*•]\s+(.*)/);
    if (m) list.push(m[1]);
    else { flush(); if (line.trim()) out.push(`<div>${esc(line)}</div>`); }
  }
  flush();
  return `<div class="text">${out.join("")}</div>`;
}

function renderStats(stats) {
  return `<div class="stats">${stats.map((s) => `
    <div class="stat ${s.trend ? "trend-" + esc(s.trend) : ""}">
      <div class="label">${esc(s.label)}</div>
      <div class="value">${esc(s.value)}</div>
      ${s.sub ? `<div class="sub">${esc(s.sub)}</div>` : ""}
    </div>`).join("")}</div>`;
}

function renderTable(t) {
  const cls = (v) => (/^\+/.test(v) ? "up" : /^-\$|^-\d|^-[A-Z]{3}/.test(v) ? "down" : "");
  return `<div class="table-wrap"><table><thead><tr>${t.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
    <tbody>${t.rows.map((r) => `<tr>${r.map((v) => `<td class="${cls(String(v))}">${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function renderItems(items) {
  return `<ul class="items">${items.map((it) => {
    const url = safeUrl(it.url);
    const title = url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(it.title)}</a>` : esc(it.title);
    const img = safeUrl(it.image) ? `<img src="${esc(it.image)}" alt="" loading="lazy">` : "";
    const line2 = [it.subtitle, it.meta].filter(Boolean).map(esc).join(" · ");
    return `<li>${img}<div class="body">
      <div class="title">${it.badge ? `<span class="badge">${esc(it.badge)}</span>` : ""}${title}</div>
      ${line2 ? `<div class="line2">${line2}</div>` : ""}
      ${it.summary ? `<div class="summary">${esc(it.summary)}</div>` : ""}
    </div></li>`;
  }).join("")}</ul>`;
}

function renderSections(sections) {
  return sections.map((s) => {
    const parts = [];
    if (s.heading) parts.push(`<h3>${esc(s.heading)}</h3>`);
    if (s.stats?.length) parts.push(renderStats(s.stats));
    if (s.table?.rows?.length) parts.push(renderTable(s.table));
    if (s.items?.length) parts.push(renderItems(s.items));
    if (s.text) parts.push(renderText(s.text));
    const hasContent = s.stats?.length || s.table?.rows?.length || s.items?.length || s.text;
    if (!hasContent && s.empty) parts.push(`<p class="muted">${esc(s.empty)}</p>`);
    return `<div class="section">${parts.join("")}</div>`;
  }).join("");
}

function timeAgo(epochSeconds) {
  const s = Date.now() / 1000 - epochSeconds;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

async function loadWidget(id, force = false) {
  const card = cards.get(id);
  if (!card) return;
  const { el, spec } = card;
  el.classList.add("loading");
  try {
    const res = await fetch(`/api/widgets/${encodeURIComponent(id)}${force ? "?force=true" : ""}`);
    if (res.status === 401) return (location.href = "/login");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    card.payload = payload;
    const body = el.querySelector(".card-body");
    const setup = /^Not set up yet/.test(payload.error || "") && !payload.data;
    const err = !payload.error ? ""
      : setup ? `<p class="muted">${esc(payload.error.replace(/ Add it in Settings.*$/, ""))} <a href="/settings">Open Settings →</a></p>`
      : `<div class="error">${esc(payload.error)}${payload.data ? " (showing last good data)" : ""}</div>`;
    body.innerHTML = err + (payload.data ? renderSections(payload.data.sections || []) : "");
  } catch (e) {
    el.querySelector(".card-body").innerHTML = `<div class="error">${esc(e.message)}</div>`;
  } finally {
    el.classList.remove("loading");
    updateStatus(id);
  }
  clearTimeout(card.timer);
  if (spec.refresh_minutes) card.timer = setTimeout(() => loadWidget(id), spec.refresh_minutes * 60 * 1000);
}

function updateStatus(id) {
  const card = cards.get(id);
  const t = card?.payload?.updated_at;
  card.el.querySelector(".status").textContent = t ? timeAgo(t) : "";
}

async function init() {
  const res = await fetch("/api/layout");
  if (res.status === 401) return (location.href = "/login");
  const layout = await res.json();
  document.title = layout.title;
  document.getElementById("title").textContent = layout.title;
  const tz = layout.timezone;
  const tick = () => {
    document.getElementById("clock").textContent = new Date().toLocaleString(undefined, {
      timeZone: tz, weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit",
    });
    cards.forEach((_, id) => updateStatus(id));
  };
  tick();
  setInterval(tick, 30000);

  if (!layout.widgets.length) {
    grid.innerHTML = `<p class="muted">Nothing to show yet. Open <a href="/settings">Settings</a> to add your accounts.</p>`;
  }
  for (const spec of layout.widgets) {
    const el = tpl.content.firstElementChild.cloneNode(true);
    el.classList.add(`size-${spec.size}`, `type-${spec.type}`);
    el.querySelector("h2").textContent = spec.title;
    el.querySelector(".refresh").addEventListener("click", () => loadWidget(spec.id, true));
    grid.appendChild(el);
    cards.set(spec.id, { el, spec, timer: null });
    loadWidget(spec.id);
  }
}

document.getElementById("refresh-all").addEventListener("click", () => cards.forEach((_, id) => loadWidget(id, true)));

init().catch((e) => { grid.innerHTML = `<div class="error">Could not load dashboard: ${esc(e.message)}</div>`; });
