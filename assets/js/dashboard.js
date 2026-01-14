// Dashboard JS — simplified for iframe display without auto-resizing

(function () {
  'use strict';

  // Remove tooltip attributes to prevent bootstrap tooltips
  function removeTooltipsAndTitles() {
    document.querySelectorAll('[data-bs-toggle="tooltip"], [data-toggle="tooltip"], [title]').forEach(el => {
      el.removeAttribute('data-bs-toggle');
      el.removeAttribute('data-toggle');
      el.removeAttribute('data-bs-original-title');
      el.removeAttribute('title');
      el.removeAttribute('data-original-title');
    });
  }

  // navigation highlighting
  function initNavigationActive() {
    const activeNavItem = document.body.getAttribute('data-nav-active');
    if (!activeNavItem) return;
    document.querySelectorAll('.nav-link').forEach(link => {
      link.classList.toggle('active', link.getAttribute('data-nav-item') === activeNavItem);
    });
  }

  // Mobile sidebar behavior
  function initMobileSidebar() {
    const toggler = document.querySelector('.navbar-toggler');
    const menu = document.querySelector('#sidebarMenu');
    if (!toggler || !menu) return;

    toggler.addEventListener('click', () => {
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

  document.addEventListener('DOMContentLoaded', () => {
    removeTooltipsAndTitles();
    initNavigationActive();
    initMobileSidebar();
  });

})();
