import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Card, CardBody, PageTitle } from "../components/ui/card";
import { api } from "../lib/api";
import { can, useMe } from "../lib/me";

const GO = [
	{
		to: "/setup",
		label: "Company setup",
		what: "people, roles, who approves what, policies",
		needs: ["admin.users", "admin.rules", "admin.settings"],
	},
	{
		to: "/automation",
		label: "Automation",
		what: "what the system may do by itself, and the off switch",
		needs: ["admin.settings"],
	},
	{
		to: "/audit",
		label: "Audit",
		what: "every change and decision, and who made it",
		needs: ["audit.view"],
	},
] as const;

export function HomePage() {
	const me = useMe();
	const canDecide = can(
		me.data,
		"request.approve",
		"po.approve",
		"invoice.approve",
		"payment.approve",
		"rfq.send",
		"supplier.activate",
	);
	const pending = useQuery({
		queryKey: ["proposals", "pending"],
		queryFn: () => api<unknown[]>("/api/admin/proposals?status=pending"),
		enabled: canDecide,
	});

	return (
		<>
			<PageTitle sub={`${me.data?.tenant_name ?? ""} · ${me.data?.role ?? ""}`}>
				Welcome, {me.data?.display_name}
			</PageTitle>
			<div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
				{canDecide ? (
					<Card>
						<CardBody>
							<div className="text-3xl font-semibold">
								{pending.data?.length ?? "…"}
							</div>
							<div className="text-sm text-muted-foreground">
								waiting for your decision
							</div>
							<Link
								to="/approvals"
								className="mt-2 inline-block text-sm text-primary underline"
							>
								Open Approvals
							</Link>
						</CardBody>
					</Card>
				) : null}
				<Card>
					<CardBody>
						<div className="mb-2 text-sm font-medium">What is here</div>
						<ul className="space-y-1 text-sm text-muted-foreground">
							{GO.filter((g) => can(me.data, ...g.needs)).map((g) => (
								<li key={g.to}>
									<span className="font-medium text-foreground">{g.label}</span>{" "}
									· {g.what}
								</li>
							))}
							<li>
								Requests, orders, invoices and payments are in the main app.
							</li>
						</ul>
					</CardBody>
				</Card>
			</div>
		</>
	);
}
