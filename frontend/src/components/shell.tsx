import { useQueryClient } from "@tanstack/react-query";
import {
	Link,
	Outlet,
	useNavigate,
	useRouterState,
} from "@tanstack/react-router";
import { useState } from "react";
import { logout } from "../lib/api";
import { can, useMe } from "../lib/me";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";

type NavItem = {
	to: string;
	label: string;
	show: (permissions: string[]) => boolean;
	group?: string;
};

const any =
	(...actions: string[]) =>
	(p: string[]) =>
		actions.some((a) => p.includes(a));

const DECIDE = [
	"request.approve",
	"po.approve",
	"invoice.approve",
	"payment.approve",
	"rfq.send",
	"supplier.activate",
];

const NAV: NavItem[] = [
	{ to: "/", label: "Home", show: () => true },
	{
		to: "/approvals",
		label: "Approvals",
		show: any(...DECIDE),
		group: "Decisions",
	},
	{
		to: "/setup",
		label: "Company setup",
		show: any("admin.users", "admin.rules", "admin.settings"),
		group: "Company",
	},
	{
		to: "/automation",
		label: "Automation",
		show: any("admin.settings"),
		group: "Company",
	},
	{
		to: "/audit",
		label: "Audit",
		show: any("audit.view"),
		group: "Records and logs",
	},
	{
		to: "/runs",
		label: "Agent runs",
		show: any("audit.view"),
		group: "Records and logs",
	},
	{
		to: "/decisions",
		label: "Rule decisions",
		show: any("audit.view"),
		group: "Records and logs",
	},
	{
		to: "/model-calls",
		label: "Model calls",
		show: any("audit.view"),
		group: "Records and logs",
	},
	{ to: "/rules", label: "Rules", show: any("admin.rules"), group: "Advanced" },
	{ to: "/users", label: "Users", show: any("admin.users"), group: "Advanced" },
];

// What each page needs. A person who opens one by its address without the right gets a message, not a failed request.
const NEEDS: [string, string[], string][] = [
	[
		"/approvals",
		DECIDE,
		"people who decide requests, orders, invoices and payments",
	],
	["/rules", ["admin.rules"], "the administrator"],
	["/users", ["admin.users"], "the administrator"],
	["/audit", ["audit.view"], "the administrator and auditors"],
	["/runs", ["audit.view"], "the administrator and auditors"],
	["/decisions", ["audit.view"], "the administrator and auditors"],
	["/model-calls", ["audit.view"], "the administrator and auditors"],
];

function NoAccess({ who, role }: { who: string; role?: string }) {
	return (
		<div className="max-w-xl rounded-lg border border-border bg-background p-6">
			<h1 className="text-lg font-semibold">This page is not for your role</h1>
			<p className="mt-2 text-sm text-muted-foreground">
				It is for {who}
				{role ? `; you are signed in as ${role}` : ""}. Everything you do day to
				day is in the main app.
			</p>
			<a
				href="/"
				className="mt-4 inline-block rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground"
			>
				Back to the app
			</a>
		</div>
	);
}

export function Shell() {
	const me = useMe();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const [open, setOpen] = useState(false);
	const permissions = me.data?.permissions ?? [];
	const items = NAV.filter((n) => n.show(permissions));
	const path = useRouterState({ select: (st) => st.location.pathname });
	const blocked = NEEDS.find(
		([prefix, needs]) =>
			me.data !== undefined &&
			(path === prefix || path.startsWith(`${prefix}/`)) &&
			!needs.some((a) => permissions.includes(a)),
	);

	async function signOut() {
		await logout();
		queryClient.clear();
		navigate({ to: "/login" });
	}

	const grouped: { title?: string; items: NavItem[] }[] = [];
	for (const n of items) {
		const last = grouped[grouped.length - 1];
		if (last && last.title === n.group) last.items.push(n);
		else grouped.push({ title: n.group, items: [n] });
	}
	const nav = (
		<nav className="flex flex-col gap-1 p-3" aria-label="Main">
			{grouped.map((g) => (
				<div key={g.title ?? "top"} className="flex flex-col gap-1">
					{g.title ? (
						<div className="mt-3 px-3 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
							{g.title}
						</div>
					) : null}
					{g.items.map((n) => (
						<Link
							key={n.to}
							to={n.to}
							onClick={() => setOpen(false)}
							className="rounded-md px-3 py-2 text-sm hover:bg-muted"
							activeProps={{
								className: "bg-primary/15 font-medium text-foreground",
							}}
							activeOptions={{ exact: n.to === "/" }}
						>
							{n.label}
						</Link>
					))}
				</div>
			))}
		</nav>
	);

	return (
		<div className="min-h-screen">
			<header className="sticky top-0 z-20 flex items-center gap-3 border-b border-border bg-background px-3 py-2 md:px-4">
				<Button
					variant="ghost"
					className="md:hidden"
					aria-label="Menu"
					aria-expanded={open}
					onClick={() => setOpen(!open)}
				>
					☰
				</Button>
				<div className="min-w-0 flex-1">
					<div className="truncate text-sm font-semibold">
						{me.data?.tenant_name ?? "…"}
					</div>
					<div className="truncate text-xs text-muted-foreground">Admin</div>
				</div>
				<div className="hidden text-right text-xs sm:block">
					<div className="font-medium">{me.data?.display_name}</div>
					<div className="text-muted-foreground">{me.data?.role}</div>
				</div>
				<a
					href="/"
					className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted"
				>
					Back to the app
				</a>
				<Button variant="outline" onClick={signOut}>
					Sign out
				</Button>
			</header>
			<div className="flex">
				<aside className="sticky top-[53px] hidden h-[calc(100vh-53px)] w-52 shrink-0 border-r border-border md:block">
					{nav}
				</aside>
				<div
					className={cn(
						"fixed inset-x-0 top-[53px] z-10 border-b border-border bg-background md:hidden",
						open ? "block" : "hidden",
					)}
				>
					{nav}
				</div>
				<main className="min-w-0 flex-1 p-3 md:p-6">
					{me.isError ? (
						<p className="text-danger">Could not load your profile.</p>
					) : me.data === undefined ? (
						<p className="text-sm text-muted-foreground">Loading…</p>
					) : blocked ? (
						<NoAccess who={blocked[2]} role={me.data?.role} />
					) : (
						<Outlet />
					)}
				</main>
			</div>
		</div>
	);
}

export { can };
