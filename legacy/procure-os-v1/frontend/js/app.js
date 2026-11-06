/**
 * Bootstraps the whole app: Trail (WebSocket), the drawer, the floating
 * assistant, global search, Focus Mode, notifications, and the router.
 * Conversation is already initialized in state.js before this runs.
 */
(async function () {
  await AppConfig.load(); // currency symbol + launch preference must be known before the first render

  // reconcile the optimistic boot-time guess (localStorage) with the real setting
  try { localStorage.setItem("procureos_launch_focus_mode", String(AppConfig.launchInFocusMode)); } catch {}

  Trail.init(Conversation.sessionId);
  TrailDrawer.init();
  FloatingAssistant.init();
  Search.init();
  FocusMode.init();
  Notifications.init();
  document.getElementById("settingsBtn").addEventListener("click", () => {
    window.location.hash = "#/settings";
  });
  Router.init();

  const rawHash = window.location.hash.replace(/^#\//, "");
  const isDefaultRoute = !rawHash || rawHash === "dashboard";

  if (AppConfig.launchInFocusMode && isDefaultRoute) {
    FocusMode.enter({ instant: true });
  } else if (document.documentElement.classList.contains("fm-boot-hide")) {
    // the boot-time guess said "hide the dashboard" but the real setting
    // (or an incoming deep link) says otherwise — undo the guess; #app's
    // own transition makes this a smooth fade-in rather than a jarring snap
    document.documentElement.classList.remove("fm-boot-hide");
  }
})();
