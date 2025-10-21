// Handle forecast frame resizing
function initializeForecastFrames() {
  const frames = document.querySelectorAll('.forecast-frame');
  
  frames.forEach(frame => {
    frame.onload = () => resizeFrame(frame);
    
    // Re-check size on window resize
    window.addEventListener('resize', () => {
      setTimeout(() => resizeFrame(frame), 100);
    });
  });
}

function resizeFrame(frame) {
  try {
    // Reset height temporarily to get accurate scroll height
    frame.style.height = '0px';
    
    // Get the content height
    const contentHeight = frame.contentWindow.document.documentElement.scrollHeight;
    
    // Set new height with some padding
    frame.style.height = (contentHeight + 20) + 'px';
  } catch (e) {
    console.warn('Frame resize failed:', e);
    // Fallback to default height if cross-origin issues
    frame.style.height = '640px';
  }
}

// Navigation active state handling
function initializeNavigation() {
  const activeNavItem = document.body.getAttribute('data-nav-active');
  if (!activeNavItem) return;

  const navLinks = document.querySelectorAll('.nav-link');
  navLinks.forEach(link => {
    if (link.getAttribute('data-nav-item') === activeNavItem) {
      link.classList.add('active');
    } else {
      link.classList.remove('active');
    }
  });
}

// Mobile menu handling
function initializeMobileMenu() {
  const toggler = document.querySelector('.navbar-toggler');
  const menu = document.querySelector('#sidebarMenu');
  
  if (!toggler || !menu) return;

  // Close menu when clicking outside
  document.addEventListener('click', (e) => {
    if (!menu.contains(e.target) && !toggler.contains(e.target)) {
      menu.classList.remove('show');
    }
  });
}

// Initialize everything when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  initializeForecastFrames();
  initializeNavigation();
  initializeMobileMenu();
});
