// Settings page: lists what's configured and lets you add / delete it.
"use strict";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) { location.href = "/login"; throw new Error("login required"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

// Each list section: which settings key it shows and how to describe an entry.
const LISTS = {
  email: { key: "email_accounts", label: (e) => e.name, sub: (e) => e.name !== e.username ? e.username : e.provider },
  gcal: { key: "google_calendars", label: (e) => e.name, sub: () => "iCal link" },
  youtube: { key: "youtube_channels", label: (e) => e.name, sub: (e) => e.channel_id },
  podcasts: { key: "podcasts", label: (e) => e.name, sub: (e) => new URL(e.url).hostname },
};

function render(settings) {
  for (const [id, spec] of Object.entries(LISTS)) {
    const ul = document.querySelector(`#${id} .entries`);
    const entries = settings[spec.key] || [];
    ul.innerHTML = entries.length
      ? entries.map((e) => `<li><div><div class="title">${esc(spec.label(e))}</div>
          <div class="line2">${esc(spec.sub(e))}</div></div>
          <button type="button" class="danger" data-key="${spec.key}" data-uid="${esc(e.uid)}">Delete</button></li>`).join("")
      : `<li class="muted">Nothing added yet.</li>`;
  }
  const cal = settings.calendly || {};
  document.querySelector("#calendly .status-line").textContent = cal.has_token ? "✅ Connected" : "Not connected.";
  document.querySelector("#calendly .remove").hidden = !cal.has_token;
  const ai = settings.ai || {};
  document.querySelector("#ai .status-line").textContent = ai.has_api_key ? "✅ AI summaries are on" : "AI summaries are off.";
  document.querySelector("#ai .remove").hidden = !ai.has_api_key;
  if (ai.language) document.querySelector("#ai [name=language]").value = ai.language;
}

// Submit a form: shows "Checking…" and any error inline, and clears the form on success.
function handle(form, action) {
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = form.querySelector("button[type=submit]");
    const err = form.querySelector(".error");
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Checking…";
    if (err) err.hidden = true;
    try {
      const values = Object.fromEntries(new FormData(form));
      const result = await action(values);
      if (result) { render(result); form.reset(); syncEmailForm(); }
    } catch (e) {
      if (err) { err.textContent = e.message; err.hidden = false; } else alert(e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  });
}

document.addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-uid]");
  if (!btn) return;
  const name = btn.closest("li").querySelector(".title").textContent;
  if (!confirm(`Delete “${name}”?`)) return;
  btn.disabled = true;
  try { render(await api("DELETE", `/api/settings/${btn.dataset.key}/${btn.dataset.uid}`)); }
  catch (e) { alert(e.message); btn.disabled = false; }
});

// Email: show the server fields only for "Other", the Primary-tab box only for Gmail.
const emailForm = document.querySelector("#email form");
function syncEmailForm() {
  const provider = emailForm.provider.value;
  emailForm.querySelector(".only-other").hidden = provider !== "other";
  emailForm.host.required = provider === "other";
  emailForm.querySelector(".only-gmail").hidden = provider !== "gmail";
}
emailForm.provider.addEventListener("change", syncEmailForm);
syncEmailForm();
handle(emailForm, (v) => api("POST", "/api/settings/email_accounts", {
  ...v, gmail_primary: emailForm.gmail_primary.checked,
}));

handle(document.querySelector("#gcal form"), (v) => api("POST", "/api/settings/google_calendars", v));
handle(document.querySelector("#youtube form"), (v) => api("POST", "/api/settings/youtube_channels", v));
handle(document.querySelector("#podcasts form.add"), (v) => api("POST", "/api/settings/podcasts", v));
handle(document.querySelector("#calendly form"), (v) => api("PUT", "/api/settings/calendly", v));
handle(document.querySelector("#ai form"), (v) => api("PUT", "/api/settings/ai", { provider: "gemini", ...v }));

document.querySelector("#calendly .remove").addEventListener("click", async () => {
  if (confirm("Disconnect Calendly?")) render(await api("PUT", "/api/settings/calendly", { token: "" }));
});
document.querySelector("#ai .remove").addEventListener("click", async () => {
  if (confirm("Turn off AI summaries?")) render(await api("PUT", "/api/settings/ai", { provider: "gemini", api_key: "" }));
});

// Podcast search via Apple's free directory; each result has an Add button.
const results = document.querySelector("#podcasts .results");
handle(document.querySelector("#podcasts form.search"), async (v) => {
  const found = await api("GET", `/api/podcast-search?q=${encodeURIComponent(v.q)}`);
  results.innerHTML = found.length
    ? found.map((p) => `<li>${p.image ? `<img src="${esc(p.image)}" alt="">` : ""}
        <div><div class="title">${esc(p.name)}</div><div class="line2">${esc(p.author)}</div></div>
        <button type="button" data-feed="${esc(p.url)}" data-name="${esc(p.name)}">Add</button></li>`).join("")
    : `<li class="muted">No podcasts found.</li>`;
  return null;
});
results.addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-feed]");
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = "Adding…";
  try {
    render(await api("POST", "/api/settings/podcasts", { url: btn.dataset.feed, name: btn.dataset.name }));
    btn.textContent = "Added ✓";
  } catch (e) {
    alert(e.message);
    btn.disabled = false;
    btn.textContent = "Add";
  }
});

api("GET", "/api/settings").then(render).catch((e) => alert(`Could not load settings: ${e.message}`));
