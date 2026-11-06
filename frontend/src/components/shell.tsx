import { useQueryClient } from "@tanstack/react-query";
import { Link, Outlet, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { logout } from "../lib/api";
import { can, useMe } from "../lib/me";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";

type NavItem = {
	to: string;
	label: string;
	show: (permissions: string[]) => boolean;
};

const any =
	(...actions: string[]) =>
	(p: string[]) =>
		actions.some((a) => p.includes(a));

const NAV: NavItem[] = [
	{ to: "/", label: "Home", show: () => true },
	{
		to: "/approvals",
		label: "Approvals",
		show: any(
			"request.approve",
			"po.approve",
			"invoice.approve",
			"payment.approve",
		),
	},
	{ to: "/admin/rules", label: "Rules", show: any("admin.rules") },
	{ to: "/admin/users", label: "Users", show: any("admin.users") },
	{ to: "/admin/audit", label: "Audit", show: any("audit.view") },
	{ to: "/admin/runs", label: "Agent runs", show: any("audit.view") },
	{ to: "/admin/decisions", label: "Rule decisions", show: any("audit.view") },
	{ to: "/admin/model-calls", label: "Model calls", show: any("audit.view") },
];

export function Shell() {
	const me = useMe();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const [open, setOpen] = useState(false);
	const permissions = me.data?.permissions ?? [];
	const items = NAV.filter((n) => n.show(permissions));

	async function signOut() {
		await logout();
		queryClient.clear();
		navigate({ to: "/login" });
	}

	const nav = (
		<nav className="flex flex-col gap-1 p-3" aria-label="Main">
			{items.map((n) => (
				<Link
					key={n.to}
					to={n.to}
					onClick={() => setOpen(false)}
					className="rounded-md px-3 py-2 text-sm hover:bg-muted"
					activeProps={{ className: "bg-primary/10 font-medium text-primary" }}
					activeOptions={{ exact: n.to === "/" }}
				>
					{n.label}
				</Link>
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
					<div className="truncate text-xs text-muted-foreground">
						P2P platform
					</div>
				</div>
				<div className="hidden text-right text-xs sm:block">
					<div className="font-medium">{me.data?.display_name}</div>
					<div className="text-muted-foreground">{me.data?.role}</div>
				</div>
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
					) : (
						<Outlet />
					)}
				</main>
			</div>
		</div>
	);
}

export { can };
