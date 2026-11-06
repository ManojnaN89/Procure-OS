/**
 * Login for the app. Tokens live for the browser session (access) and in local storage (refresh), under the same keys as the
 * admin app, so signing in to one signs in to the other. Every call to the API goes through Auth.fetch, which adds the token,
 * renews it once when it has expired, and shows the login screen when there is no valid session.
 */
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
    if (role) role.textContent = `${this.me.role} · ${this.me.tenant_name}`;
    if (avatar) avatar.textContent = this.initials(this.me.display_name);
    const admin = document.getElementById("adminLink");
    if (admin) admin.style.display = this.can("admin.users", "admin.rules", "audit.view") ? "" : "none";
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
            error.textContent = res.status === 423 ? "Too many wrong passwords. Try again in 15 minutes." : "Wrong email or password.";
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
