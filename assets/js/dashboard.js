// Dashboard JS — updated to remove tooltips and ensure iframe/table content is not clipped.
//
// Behavior:
// - Remove tooltip attributes at startup (prevents bootstrap tooltips and title-based tooltips).
// - Resize same-origin iframes to their content height and inject CSS into same-origin iframes
//   to force tables/text to wrap (prevents truncated/ellipsed content).
// - For cross-origin frames we set a tall but safe fallback (90vh) to avoid clipped content.
// - Keeps nav active state and mobile sidebar handling as before.

(function () {
  'use strict';

  // Remove tooltip attributes and title attributes to prevent native tooltips / bootstrap tooltip display
  function removeTooltipsAndTitles() {
    // Remove attributes used by bootstrap tooltips and plain title attributes
    document.querySelectorAll('[data-bs-toggle="tooltip"], [data-toggle="tooltip"], [title]').forEach(el => {
      el.removeAttribute('data-bs-toggle');
      el.removeAttribute('data-toggle');
      el.removeAttribute('data-bs-original-title');
      el.removeAttribute('title');
      el.removeAttribute('data-original-title');
    });
  }

  // Inject CSS into same-origin iframe to ensure tables and cells wrap rather than ellipsize
  function injectWrappingCSSIntoIframe(frame) {
    try {
      const doc = frame.contentDocument || frame.contentWindow.document;
      if (!doc) return;
      // Avoid injecting repeatedly
      if (doc.getElementById('wg-observatory-injected-wrap-style')) return;
      const style = doc.createElement('style');
      style.id = 'wg-observatory-injected-wrap-style';
      style.textContent = `
        /* Enforce wrapping inside embedded content so cells/columns don't clip */
        table, thead, tbody, th, td, p, div, span {
          white-space: normal !important;
          word-break: break-word !important;
          overflow-wrap: anywhere !important;
        }
        /* Ensure any elements that previously had ellipsis are allowed to wrap */
        .text-truncate, .truncate, .ellipsis {
          white-space: normal !important;
          text-overflow: clip !important;
        }
      `;
      (doc.head || doc.documentElement).appendChild(style);
    } catch (e) {
      // cross-origin: cannot inject
    }
  }

  // Resize a same-origin iframe to its content height; fallback for cross-origin frames.
  function adjustFrameHeight(frame) {
    try {
      const cw = frame.contentWindow;
      const doc = cw.document;
      if (!doc) {
        frame.style.height = '90vh';
        return;
      }

      // Inject wrapping CSS to avoid truncation inside iframe
      injectWrappingCSSIntoIframe(frame);

      // compute height robustly
      const body = doc.body;
      const html = doc.documentElement;
      const height = Math.max(
        body ? body.scrollHeight : 0,
        html ? html.scrollHeight : 0,
        body ? body.offsetHeight : 0,
        html ? html.offsetHeight : 0
      );

      // apply with small buffer
      if (height && Number.isFinite(height)) {
        frame.style.height = (height + 24) + 'px';
      } else {
        // fallback to a tall viewport-based height if measurement failed
        frame.style.height = Math.min(window.innerHeight * 0.95, 1200) + 'px';
      }
    } catch (err) {
      // cross-origin or other access denied: use tall fallback so content isn't clipped
      console.warn('Could not auto-resize iframe (likely cross-origin). Using tall fallback height.', err);
      frame.style.height = Math.min(window.innerHeight * 0.9, 1200) + 'px';
      // ensure iframe minimum width does not force horizontal clipping
      frame.style.minWidth = '0';
    }
  }

  // Initialize frames: try to resize same-origin frames after load, and listen for postMessage
  function initForecastFrames() {
    const frames = Array.from(document.querySelectorAll('.forecast-frame'));
    frames.forEach(frame => {
      // ensure frame is not forcing horizontal overflow
      frame.style.minWidth = '0';

      // When the frame loads, attempt to adjust its height
      frame.addEventListener('load', () => {
        adjustFrameHeight(frame);

        // If iframe supports messaging internally, the inner page can post { type: 'resize' } to request recalculation
        // We keep a window-level listener below to handle that.
      });

      // Try periodic recalculation for the first few seconds in case inner content changes height after load
      let tries = 0;
      const interval = setInterval(() => {
        try {
          if (tries++ > 8) {
            clearInterval(interval);
            return;
          }
          adjustFrameHeight(frame);
        } catch (e) {
          clearInterval(interval);
        }
      }, 400);
    });

    // Listen for postMessage resizing requests from same-origin or cooperating embeds
    window.addEventListener('message', (ev) => {
      // Accept objects shaped like { type: 'resize', height: <optional> } and only act on frames we control
      try {
        if (!ev || !ev.data) return;
        const d = ev.data;
        if (d && (d.type === 'resize' || d.resize === true)) {
          // find the frame that matches event source
          frames.forEach(frame => {
            if (frame.contentWindow === ev.source) {
              if (d.height && Number.isFinite(d.height)) {
                frame.style.height = (d.height + 24) + 'px';
              } else {
                adjustFrameHeight(frame);
              }
            }
          });
        }
      } catch (e) {
        // ignore
      }
    });
  }

  // navigation highlighting (unchanged from previous behavior)
  function initNavigationActive() {
    const activeNavItem = document.body.getAttribute('data-nav-active');
    if (!activeNavItem) return;
    document.querySelectorAll('.nav-link').forEach(link => {
      link.classList.toggle('active', link.getAttribute('data-nav-item') === activeNavItem);
    });
  }

  // Mobile sidebar behavior (unchanged)
  function initMobileSidebar() {
    const toggler = document.querySelector('.navbar-toggler');
    const menu = document.querySelector('#sidebarMenu');
    if (!toggler || !menu) return;

    toggler.addEventListener('click', () => {
      // small delay to let bootstrap toggle classes; then add a body indicator
      setTimeout(() => {
        document.body.classList.toggle('menu-open', menu.classList.contains('show'));
      }, 50);
    });

    document.addEventListener('click', (e) => {
      if (!menu.classList.contains('show')) return;
      if (menu.contains(e.target) || toggler.contains(e.target)) return;
      menu.classList.remove('show');
      document.body.classList.remove('menu-open');
    });
  }

  // Wrap any forecast tables that might not be wrapped already
  function wrapForecastTables() {
    document.querySelectorAll('.forecast-table').forEach(tbl => {
      if (tbl.parentElement && tbl.parentElement.classList.contains('forecast-table-container')) return;
      const wrap = document.createElement('div');
      wrap.className = 'forecast-table-container';
      tbl.parentNode.insertBefore(wrap, tbl);
      wrap.appendChild(tbl);
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    removeTooltipsAndTitles();
    initNavigationActive();
    initMobileSidebar();
    wrapForecastTables();
    initForecastFrames();

    // Set the small timestamp detail in the navbar if present
    const timestamp = document.querySelector('.nav-link.px-3.text-light');
    if (timestamp) {
      const now = new Date();
      timestamp.textContent = `Updated: ${now.toUTCString()}`;
    }
  });

})();
