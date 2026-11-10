/**
 * Buttons made of a <div class="btn"> are not buttons to a keyboard or a screen reader. This gives each of them the button
 * role, a place in the tab order and Enter/Space to press it, wherever it appears (screens are drawn after this loads).
 */
(function () {
  const SELECTOR = ".btn:not(button):not(a), .tab:not(button):not(a)";

  function enhance(root) {
    root.querySelectorAll(SELECTOR).forEach((el) => {
      if (el.dataset.a11y) return;
      el.dataset.a11y = "1";
      el.setAttribute("role", "button");
      if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "0");
    });
  }

  document.addEventListener("keydown", (e) => {
    if ((e.key !== "Enter" && e.key !== " ") || !e.target.matches || !e.target.matches('[role="button"][data-a11y]')) return;
    e.preventDefault();
    e.target.click();
  });

  const start = () => {
    enhance(document);
    new MutationObserver((records) => {
      for (const r of records) r.addedNodes.forEach((n) => { if (n.nodeType === 1) { if (n.matches && n.matches(SELECTOR)) enhance({ querySelectorAll: () => [n] }); enhance(n); } });
    }).observe(document.body, { childList: true, subtree: true });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
