(() => {
  const body = document.body;
  const activeKey = body?.dataset?.navActive;
  if (activeKey) {
    const activeLinks = document.querySelectorAll(`[data-nav-item="${activeKey}"]`);
    activeLinks.forEach((link) => link.classList.add("active"));
  }

  const frameByType = {};
  document.querySelectorAll("iframe[data-resize-type]").forEach((frame) => {
    const type = frame.dataset.resizeType;
    if (type) {
      frameByType[type] = frame;
    }
  });

  window.addEventListener("message", (event) => {
    if (!event || !event.data) return;
    const { type, height } = event.data;
    if (!type || typeof height !== "number") return;

    const frame = frameByType[type];
    if (!frame) return;

    const safeHeight = Math.max(360, Math.min(height, 2200));
    const nextValue = `${safeHeight}px`;
    if (frame.style.height !== nextValue) {
      frame.style.height = nextValue;
    }
  });
})();
