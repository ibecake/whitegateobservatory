// Responsive dashboard script: iframe resizing, nav highlight, mobile menu improvements

// Resize a same-origin iframe by looking at its document height.
// If cross-origin, fallback to a fixed height to avoid infinite loops.
function adjustFrameHeight(frame) {
  try {
    // reset temporarily to measure
    frame.style.height = '120px';
    const doc = frame.contentWindow.document;
    const body = doc.body;
    const html = doc.documentElement;
    const height = Math.max(
      body ? body.scrollHeight : 0,
      html ? html.scrollHeight : 0,
      body ? body.offsetHeight : 0,
      html ? html.offsetHeight : 0
    );
    // Add a small buffer so scrollbars don't appear
    frame.style.height = (height + 24) + 'px';
  } catch (err) {
    // cross-origin or other access error — fallback safe height
    console.warn('Could not access iframe content; using fallback height', err);
    frame.style.height = '640px';
  }
}

function initForecastFrames() {
  const frames = document.querySelectorAll('.forecast-frame');

  frames.forEach(frame => {
    // If the iframe is same-origin (local files), we can resize after load.
    frame.addEventListener('load', () => {
      adjustFrameHeight(frame);

      // If the embedded page posts a resize message, respond to it
      // (useful if the inner page sends {type:"resize"}).
      window.addEventListener('message', (ev) => {
        try {
          if (!ev || !ev.data) return;
          // If the message originates from this frame and requests resize
          if (ev.source === frame.contentWindow && (ev.data.type === 'resize' || ev.data.resize === true)) {
            adjustFrameHeight(frame);
          }
        } catch (e) {
          /* ignore */
        }
      });
    });

    // window resize debounce
    let tid;
    window.addEventListener('resize', () => {
      clearTimeout(tid);
      tid = setTimeout(() => adjustFrameHeight(frame), 120);
    });
  });
}

function initNavigationActive() {
  const active = document.body.getAttribute('data-nav-active');
  if (!active) return;
  document.querySelectorAll('.nav-link').forEach(a => {
    a.classList.toggle('active', a.getAttribute('data-nav-item') === active);
  });
}

function initMobileSidebar() {
  const toggler = document.querySelector('.navbar-toggler');
  const menu = document.querySelector('#sidebarMenu');
  if (!toggler || !menu) return;

  toggler.addEventListener('click', () => {
    // Bootstrap handles 'show' but ensure body state for potential styles
    setTimeout(() => {
      document.body.classList.toggle('menu-open', menu.classList.contains('show'));
    }, 50);
  });

  // Click outside to close on small screens
  document.addEventListener('click', (e) => {
    if (!menu.classList.contains('show')) return;
    if (menu.contains(e.target) || toggler.contains(e.target)) return;
    // collapse via bootstrap class removal
    menu.classList.remove('show');
    document.body.classList.remove('menu-open');
  });
}

function wrapForecastTables() {
  // Wrap any .forecast-table that wasn't wrapped yet
  document.querySelectorAll('.forecast-table').forEach(tbl => {
    if (tbl.parentElement && tbl.parentElement.classList.contains('forecast-table-container')) return;
    const wrap = document.createElement('div');
    wrap.className = 'forecast-table-container';
    tbl.parentNode.insertBefore(wrap, tbl);
    wrap.appendChild(tbl);
  });
}

document.addEventListener('DOMContentLoaded', () => {
  initNavigationActive();
  initMobileSidebar();
  wrapForecastTables();
  initForecastFrames();

  // Update the small timestamp text in the navbar if present
  const stamp = document.querySelector('.nav-link.px-3.text-light');
  if (stamp) {
    const now = new Date();
    stamp.textContent = `Updated: ${now.toUTCString()}`;
  }
});
