/** Opens and closes the menu drawer on small screens. */
(function () {
  const close = () => document.body.classList.remove("nav-open");
  document.addEventListener("click", (e) => {
    if (e.target.closest("#menuBtn")) document.body.classList.toggle("nav-open");
    else if (e.target.closest("#navBackdrop") || e.target.closest(".sidebar .nav-item")) close();
  });
  window.addEventListener("resize", () => { if (window.innerWidth > 900) close(); });
})();
