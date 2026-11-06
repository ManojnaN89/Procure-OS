// Talks to the backend. The access token lives for the browser session; the refresh token is used once on a 401.

const ACCESS = "p2p.access";
const REFRESH = "p2p.refresh";

export class ApiError extends Error {
	status: number;

	constructor(status: number, message: string) {
		super(message);
		this.status = status;
	}
}

export type Claims = { sub: string; tenant: string; role: string; exp: number };

function read(storage: Storage, key: string): string | null {
	try {
		return storage.getItem(key);
	} catch {
		return null;
	}
}

export function claims(): Claims | null {
	const token = read(sessionStorage, ACCESS);
	if (!token) return null;
	try {
		const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
		return JSON.parse(atob(payload)) as Claims;
	} catch {
		return null;
	}
}

function store(tokens: { access_token: string; refresh_token: string }) {
	sessionStorage.setItem(ACCESS, tokens.access_token);
	localStorage.setItem(REFRESH, tokens.refresh_token);
}

export function clearSession() {
	sessionStorage.removeItem(ACCESS);
	localStorage.removeItem(REFRESH);
}

async function detail(res: Response): Promise<string> {
	try {
		const body = await res.json();
		return typeof body.detail === "string"
			? body.detail
			: JSON.stringify(body.detail ?? body);
	} catch {
		return res.statusText;
	}
}

export async function login(email: string, password: string) {
	const res = await fetch("/api/auth/login", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ email, password }),
	});
	if (!res.ok) throw new ApiError(res.status, await detail(res));
	store(await res.json());
}

export async function logout() {
	const refresh = read(localStorage, REFRESH);
	if (refresh) {
		await fetch("/api/auth/logout", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ refresh_token: refresh }),
		}).catch(() => undefined);
	}
	clearSession();
}

async function refreshTokens(): Promise<boolean> {
	const refresh = read(localStorage, REFRESH);
	if (!refresh) return false;
	const res = await fetch("/api/auth/refresh", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ refresh_token: refresh }),
	});
	if (!res.ok) return false;
	store(await res.json());
	return true;
}

export async function api<T>(
	path: string,
	init: RequestInit & { json?: unknown } = {},
): Promise<T> {
	const send = () => {
		const headers = new Headers(init.headers);
		const token = read(sessionStorage, ACCESS);
		if (token) headers.set("Authorization", `Bearer ${token}`);
		let body = init.body;
		if (init.json !== undefined) {
			headers.set("Content-Type", "application/json");
			body = JSON.stringify(init.json);
		}
		return fetch(path, { ...init, headers, body });
	};
	let res = await send();
	if (res.status === 401 && (await refreshTokens())) res = await send();
	if (res.status === 401) {
		clearSession();
		window.location.assign("/admin/login");
	}
	if (!res.ok) throw new ApiError(res.status, await detail(res));
	return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}
