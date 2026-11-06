import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The backend runs on 8000; the browser only talks to Vite, which forwards /api.
export default defineConfig({
	base: "/admin/", // the admin app is served under /admin; the main interface owns /
	plugins: [react(), tailwindcss()],
	server: {
		host: "127.0.0.1",
		port: 5173,
		proxy: { "/api": "http://127.0.0.1:8000" },
	},
});
