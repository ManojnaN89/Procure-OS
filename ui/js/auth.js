/**
 * Login for the app. Tokens live for the browser session (access) and in local storage (refresh), under the same keys as the
 * admin app, so signing in to one signs in to the other. Every call to the API goes through Auth.fetch, which adds the token,
 * renews it once when it has expired, and shows the login screen when there is no valid session.
 */
// shown under the company's name until it is live (the texts of block 0d, 4.1)
const STATE_BANNERS = {
  setup: "Setup: what you do here is test data. It is archived at cutover.",
  cutover: "Cutover: loading open documents. Sign-in opens at go-live.",
};

const Auth = {
  ACCESS: "p2p.access",
  REFRESH: "p2p.refresh",
  me: null,

  token() { try { return sessionStorage.getItem(this.ACCESS); } catch { return null; } },
  refreshToken() { try { return localStorage.getItem(this.REFRESH); } catch { return null; } },

  store(tokens) {
    sessionStorage.setItem(this.ACCESS, tokens.access_token);
    localStorage.setItem(this.REFRESH, tokens.refresh_token);
  },

  clear() {
    try { sessionStorage.removeItem(this.ACCESS); localStorage.removeItem(this.REFRESH); } catch { /* storage blocked */ }
    this.me = null;
  },

  async renew() {
    const refresh = this.refreshToken();
    if (!refresh) return false;
    const res = await fetch("/api/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!res.ok) return false;
    this.store(await res.json());
    return true;
  },

  /** fetch() with the login token. A 401 renews once, and if that fails shows the login screen. */
  async fetch(url, options = {}) {
    const send = () => {
      const headers = new Headers(options.headers || {});
      const token = this.token();
      if (token) headers.set("Authorization", `Bearer ${token}`);
      return fetch(url, { ...options, headers });
    };
    let res = await send();
    if (res.status === 401 && (await this.renew())) res = await send();
    if (res.status === 401) {
      this.clear();
      this.showLogin();
    }
    return res;
  },

  /** Resolves once somebody is signed in. Shows the login screen first when nobody is. */
  async ensure() {
    if (this.token() || (await this.renew())) {
      const res = await fetch("/api/auth/me", { headers: { Authorization: `Bearer ${this.token()}` } });
      if (res.ok) {
        this.me = await res.json();
        this.applyIdentity();
        return this.me;
      }
      this.clear();
    }
    return this.askToSignIn();
  },

  can(...actions) {
    return !!this.me && actions.some((a) => this.me.permissions.includes(a));
  },

  initials(name) {
    return (name || "?").split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  },

  applyIdentity() {
    const who = document.querySelector(".user-badge .who");
    const role = document.querySelector(".user-badge .role");
    const avatar = document.querySelector(".user-badge .avatar-circle");
    if (who) who.textContent = this.me.display_name;
    if (role) role.textContent = this.me.role;
    if (avatar) avatar.textContent = this.initials(this.me.display_name);
    const company = document.getElementById("companyName");
    // a company not yet live says so under its name (D-04, D-05)
    const banner = STATE_BANNERS[this.me.company_state];
    if (company) {
      company.innerHTML = `<i class="ti ti-building" aria-hidden="true"></i>${escapeHtml(this.me.tenant_name || "")}` +
        (banner ? ` <small class="muted" role="status">${escapeHtml(banner)}</small>` : "");
      company.title = banner || "Company";
    }
    const admin = document.getElementById("adminLink");
    if (admin) admin.style.display = this.can("admin.users", "admin.rules", "audit.view", "request.approve", "po.approve", "invoice.approve", "payment.approve", "rfq.send", "supplier.activate") ? "" : "none";
    // the menu shows what this person's role can use, so each person sees their own flow
    document.querySelectorAll("#navList .nav-item[data-needs]").forEach((el) => {
      el.style.display = this.can(...el.dataset.needs.split(" ")) ? "" : "none";
    });
    document.querySelectorAll("#navList .sidebar-label").forEach((label) => {
      let next = label.nextElementSibling;
      let any = false;
      while (next && !next.classList.contains("sidebar-label")) {
        if (next.classList.contains("nav-item") && next.style.display !== "none") any = true;
        next = next.nextElementSibling;
      }
      label.style.display = any ? "" : "none";
    });
  },

  showLogin() {
    const overlay = document.getElementById("loginOverlay");
    if (overlay) overlay.classList.add("open");
  },

  askToSignIn() {
    return new Promise((resolve) => {
      const overlay = document.getElementById("loginOverlay");
      const form = document.getElementById("loginForm");
      const error = document.getElementById("loginError");
      const button = document.getElementById("loginButton");
      overlay.classList.add("open");
      form.onsubmit = async (e) => {
        e.preventDefault();
        error.textContent = "";
        button.disabled = true;
        try {
          const res = await fetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: form.email.value, password: form.password.value }),
          });
          if (!res.ok) {
            // a company in cutover or suspended says why nobody may sign in now
            const said = res.status === 403 ? (await res.json().catch(() => ({}))).detail : "";
            error.textContent = res.status === 423 ? "Too many wrong passwords. Try again in 15 minutes."
              : said || "Wrong email or password.";
            return;
          }
          this.store(await res.json());
          // a new sign-in starts the app afresh, so nothing from a previous person is left on screen
          window.location.reload();
          resolve(null);
        } finally {
          button.disabled = false;
        }
      };
    });
  },

  async signOut() {
    const refresh = this.refreshToken();
    if (refresh) {
      await fetch("/api/auth/logout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      }).catch(() => undefined);
    }
    this.clear();
    window.location.reload();
  },

  /** WebSocket address with the token in the query string (browsers cannot set headers on a WebSocket). */
  socketUrl(path) {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${window.location.host}${path}?token=${encodeURIComponent(this.token() || "")}`;
  },
};
