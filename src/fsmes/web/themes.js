/* Theme choice, remembered per browser.

   The choice lands on <html data-theme> before first paint (see the inline
   snippet each page carries), so a daylight user never gets a flash of the
   dark control-room palette. This file only builds the picker. */

(function () {
  const KEY = "fsmes-theme";
  const THEMES = [
    ["control-room", "Control room"],
    ["daylight", "Daylight"],
    ["high-contrast", "High contrast"],
    ["night-shift", "Night shift"],
  ];

  function apply(name) {
    document.documentElement.setAttribute("data-theme", name);
    try { localStorage.setItem(KEY, name); } catch (err) { /* private window */ }
  }

  function build() {
    const header = document.querySelector("header");
    if (!header || header.querySelector(".theme-pick")) return;

    let current = "control-room";
    try { current = localStorage.getItem(KEY) || current; } catch (err) { /* ignore */ }

    const select = document.createElement("select");
    select.className = "theme-pick";
    select.setAttribute("aria-label", "Screen theme");
    for (const [value, label] of THEMES) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      if (value === current) option.selected = true;
      select.appendChild(option);
    }
    select.addEventListener("change", () => apply(select.value));

    // Before the sign-out button if there is one, otherwise at the end.
    const signOut = header.querySelector("#logout");
    header.insertBefore(select, signOut || null);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
