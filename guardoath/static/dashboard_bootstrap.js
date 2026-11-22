"use strict";
// Native parallel script fetches can exhaust the small production origin.
// Load the content-addressed manifest serially; never start a partial app.
(() => {
  const panel = document.getElementById("dashboard-startup");
  const message = document.getElementById("dashboard-startup-message");
  const retry = document.getElementById("dashboard-startup-retry");
  const app = document.getElementById("dashboard-app");
  const manifest = [...document.querySelectorAll('script[type="application/guardoath-script"][data-src]')];
  let ready;
  window.guardoathDashboardStartup = new Promise(resolve => { ready = resolve; });
  let position = 0, running = false, reloadRequired = false;
  const attempts = 3;

  function load(entry) {
    return new Promise((resolve, reject) => {
      const url = new URL(entry.dataset.src, location.origin);
      if (url.origin !== location.origin || !/^\/assets\/[a-z0-9_-]+\.js$/.test(url.pathname)) {
        reject({reloadRequired: true});
        return;
      }
      const script = document.createElement("script");
      script.src = url.href;
      script.async = false;
      script.onload = () => {
        script.onload = script.onerror = null;
        const complete = () => {
          const exported = window[entry.dataset.export];
          const initialized = entry.dataset.exportType === "ready"
            ? exported === true : typeof exported?.render === "function";
          if (!initialized) {
            // Execution already occurred. Repeating it could duplicate handlers or
            // redeclare globals, so this condition requires a clean page reload.
            reject({reloadRequired: true});
          } else resolve();
        };
        // A helper may register its initializer before DOMContentLoaded. Its
        // success marker is set inside that initializer, never at registration.
        if (entry.dataset.exportType === "ready" && document.readyState === "loading") {
          document.addEventListener("DOMContentLoaded", complete, {once: true});
        } else complete();
      };
      script.onerror = () => {
        script.onload = script.onerror = null;
        script.remove();
        reject({reloadRequired: false});
      };
      document.head.append(script);
    });
  }

  async function loadWithRetry(entry) {
    for (let attempt = 1; attempt <= attempts; attempt++) {
      try {
        await load(entry);
        return;
      } catch (failure) {
        if (failure.reloadRequired || attempt === attempts) throw failure;
        // A bounded delay gives the origin time to finish its current requests.
        await new Promise(resolve => setTimeout(resolve, 300 * attempt));
      }
    }
  }

  async function start() {
    if (running) return;
    running = true;
    retry.hidden = true;
    message.textContent = "Loading the controls needed to connect and check your application.";
    try {
      if (!manifest.length) throw {reloadRequired: true};
      while (position < manifest.length) {
        const entry = manifest[position];
        try {
          await loadWithRetry(entry);
        } catch (failure) {
          // Sigma already has an explicit unavailable editor that preserves its
          // saved policy. The five core editors and every other module are required.
          if (entry.dataset.optional !== "true" || failure.reloadRequired) throw failure;
        }
        position++;
      }
      app.inert = false;
      app.hidden = false;
      panel.hidden = true;
      ready();
    } catch (failure) {
      reloadRequired = Boolean(failure.reloadRequired);
      message.textContent = "The dashboard could not finish loading. Your settings have not changed. " +
        (reloadRequired ? "Reload the dashboard to try again." : "Retry loading to continue.");
      retry.textContent = reloadRequired ? "Reload dashboard" : "Retry loading dashboard";
      retry.hidden = false;
    } finally {
      running = false;
    }
  }

  retry.addEventListener("click", () => {
    if (reloadRequired) location.reload();
    else start(); // Resume at the failed module; never re-execute successful ones.
  });
  start();
})();
