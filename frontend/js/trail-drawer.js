/**
 * The promoted Agent Trail drawer — opened from the top bar bell,
 * mutually exclusive with the floating assistant panel.
 */
const TrailDrawer = {
  init() {
    this.drawer = document.getElementById("trailDrawer");
    this.overlay = document.getElementById("drawerOverlay");
    this.list = document.getElementById("drawerTrailList");

    document.getElementById("trailBellBtn").addEventListener("click", () => this.toggle());
    document.getElementById("trailDrawerClose").addEventListener("click", () => this.close());
    this.overlay.addEventListener("click", () => this.close());
    document.getElementById("exportTrailBtn").addEventListener("click", () => Trail.exportLog());

    Trail.subscribe(() => { if (this.drawer.classList.contains("open")) this.render(); });
  },

  render() {
    this.list.innerHTML = "";
    if (Trail.recent.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "No agent activity yet.";
      this.list.appendChild(empty);
      return;
    }
    Trail.recent.forEach((evt, i) => {
      this.list.appendChild(Trail.renderEntry(evt, { isNew: i === 0 }));
    });
  },

  toggle() {
    this.drawer.classList.contains("open") ? this.close() : this.open();
  },

  open() {
    if (typeof FloatingAssistant !== "undefined") FloatingAssistant.close();
    this.drawer.classList.add("open");
    this.overlay.classList.add("open");
    document.getElementById("trailDot").style.display = "none";
    this.render();
  },

  close() {
    this.drawer.classList.remove("open");
    this.overlay.classList.remove("open");
  },
};
