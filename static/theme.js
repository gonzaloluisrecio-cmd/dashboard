// Light/dark toggle shared by every page.
"use strict";
const THEME_KEY = "dashboard-theme";
const applyTheme = (t) => (t ? document.documentElement.setAttribute("data-theme", t) : document.documentElement.removeAttribute("data-theme"));
try { applyTheme(localStorage.getItem(THEME_KEY)); } catch {}
document.getElementById("theme")?.addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  const next = dark ? "light" : "dark";
  applyTheme(next);
  try { localStorage.setItem(THEME_KEY, next); } catch {}
});

