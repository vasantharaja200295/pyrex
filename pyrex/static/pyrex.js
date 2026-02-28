/**
 * pyrex.js — Pyrex client runtime
 *
 * Responsibilities:
 *   1. SPA navigation  — intercept <a> clicks, fetch fragments, morph <main> with Idiomorph
 *   2. Server action caller — shared fetch wrapper used by all generated action proxies
 *   3. Hot reload (dev only) — WebSocket connection, morph on change
 *
 * Injected into every Pyrex page via <script defer src="/__pyrex_static/pyrex.js">.
 * Depends on Idiomorph (loaded before this file) and Alpine (deferred, loaded before this).
 */
(function () {
  'use strict';

  // ── Config — read from meta tags injected at serve time ───────────────────
  // <meta name="pyrex-csrf"> is present in production only
  // <meta name="pyrex-dev">  is present in dev mode only
  var _csrfMeta = document.querySelector('meta[name="pyrex-csrf"]');
  var CSRF_TOKEN = _csrfMeta ? _csrfMeta.getAttribute('content') : '';
  var IS_DEV = document.querySelector('meta[name="pyrex-dev"]') !== null;

  // ── Responsibility 2: Server Action Caller ─────────────────────────────────
  // All generated action proxies delegate here instead of containing their own
  // fetch implementation. This is the single place where CSRF tokens, headers,
  // and error handling live.

  async function _call(actionId, args) {
    var res = await fetch('/__pyrex/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-pyrex-token': CSRF_TOKEN,
      },
      body: JSON.stringify({ i: actionId, a: args }),
    });
    if (!res.ok) {
      var errData = await res.json().catch(function () { return {}; });
      throw new Error(errData.error || ('Server action failed: ' + res.status));
    }
    return await res.json();
  }

  // ── Responsibility 1: SPA Navigation ──────────────────────────────────────

  // Scripts already loaded from previous navigations — keyed by content to
  // avoid re-evaluating the same function definitions on every nav.
  var _loadedScripts = new Set();

  function _isInternalLink(a) {
    if (!a || !a.href) return false;
    try {
      var url = new URL(a.href);
      // External origin — let the browser handle it
      if (url.origin !== location.origin) return false;
      // Hash-only change on the same page — let the browser scroll
      if (url.hash && url.pathname === location.pathname && url.search === location.search) return false;
    } catch (_) {
      return false;
    }
    if (a.target === '_blank') return false;
    if (a.hasAttribute('download')) return false;
    // data-reload is an opt-out escape hatch for links that must do a full reload
    if (a.hasAttribute('data-reload')) return false;
    return true;
  }

  async function navigate(url, pushState) {
    if (pushState === undefined) pushState = true;
    try {
      var res = await fetch(url, {
        headers: { 'X-Pyrex-Nav': '1' },
      });

      if (!res.ok) {
        location.href = url;
        return;
      }

      var data = await res.json();
      var mainEl = document.querySelector('main');

      if (!mainEl) {
        // No <main> element — fall back to full navigation
        location.href = url;
        return;
      }

      // Tear down Alpine state on the CURRENT <main> content BEFORE touching
      // the DOM.  Idiomorph reuses DOM nodes that structurally match between
      // old and new pages — those nodes carry Alpine's internal markers from
      // the previous scope.  If we morph first and initTree second, Alpine sees
      // the old markers, considers those nodes already initialised, and skips
      // them, leaving x-text / @click bindings with no live scope ("count is
      // not defined").  destroyTree clears every marker and effect so initTree
      // can start completely fresh on whatever content appears after the morph.
      if (window.Alpine) {
        Alpine.destroyTree(mainEl);
      }

      // Load any new component factory / action proxy scripts that the
      // incoming page requires but the current page did not have.
      // Deduplication prevents redundant re-execution across navigations.
      if (data.scripts && Array.isArray(data.scripts)) {
        data.scripts.forEach(function (src) {
          if (_loadedScripts.has(src)) return;
          _loadedScripts.add(src);
          var s = document.createElement('script');
          s.textContent = src;
          document.head.appendChild(s);
        });
      }

      // Replace <main> content.  Idiomorph minimises DOM mutations (smoother
      // transitions, fewer repaints) but Alpine state is always reset fresh
      // because we called destroyTree above.
      if (window.Idiomorph) {
        Idiomorph.morph(mainEl, '<main>' + data.html + '</main>');
      } else {
        mainEl.innerHTML = data.html;
      }

      // Update page title
      if (data.title) document.title = data.title;

      // Push the new URL into browser history (skip for popstate navigation)
      if (pushState) history.pushState(null, data.title || '', url);

      // Initialise Alpine on the new content.  destroyTree above ensured every
      // element is unmarked, so initTree will pick up all x-data roots cleanly.
      if (window.Alpine) {
        Alpine.initTree(mainEl);
      }

    } catch (err) {
      // Network failure, malformed JSON, or anything unexpected.
      // Log so the developer can see exactly what went wrong, then fall back
      // to a full browser navigation so the user is never left stuck.
      console.error('[pyrex] SPA navigation failed — falling back to full load:', err);
      location.href = url;
    }
  }

  // Intercept all <a> clicks on the page (event delegation — works for
  // elements added after initial parse too).
  document.addEventListener('click', function (e) {
    var a = e.target.closest('a');
    if (!a || !_isInternalLink(a)) return;
    e.preventDefault();
    navigate(a.href);
  });

  // Handle browser Back / Forward buttons
  window.addEventListener('popstate', function () {
    navigate(location.href, false);
  });

  // ── Responsibility 3: Hot Reload (dev only) ────────────────────────────────
  // Connects to the server's WebSocket endpoint. On a "reload" message, tries
  // to morph the current page content. Falls back to location.reload() if that
  // fails for any reason (e.g. a parse error in the rebuilt file).

  if (IS_DEV) {
    function _connectWs() {
      var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      var ws = new WebSocket(proto + '//' + location.host + '/__pyrex_ws');

      ws.onmessage = function (e) {
        if (e.data !== 'reload') return;
        location.reload();
      };

      ws.onclose = function () {
        // Reconnect after a short delay — handles server restarts gracefully
        setTimeout(_connectWs, 1000);
      };
    }
    _connectWs();
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  window.__pyrex = {
    call: _call,
    navigate: navigate,
  };

}());
