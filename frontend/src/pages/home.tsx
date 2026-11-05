import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Card, CardBody, PageTitle } from "../components/ui/card";
import { api } from "../lib/api";
import { can, useMe } from "../lib/me";

export function HomePage() {
	const me = useMe();
	const canDecide = can(
		me.data,
		"request.approve",
		"po.approve",
		"invoice.approve",
		"payment.approve",
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
								Open approvals
							</Link>
						</CardBody>
					</Card>
				) : null}
				<Card>
					<CardBody>
						<div className="mb-1 text-sm font-medium">What you can do</div>
						<ul className="list-inside list-disc text-sm text-muted-foreground">
							{(me.data?.permissions ?? []).map((p) => (
								<li key={p}>{p}</li>
							))}
						</ul>
					</CardBody>
				</Card>
			</div>
		</>
	);
}
