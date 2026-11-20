import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader } from "../components/ui/card";
import { Field, Input } from "../components/ui/input";
import { Table, Td, Th } from "../components/ui/table";
import { api } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { can, useMe } from "../lib/me";

export const select =
	"w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm min-h-9";

/** Every change in the setup needs a reason: one box at the top of the page serves all the buttons below it. */
export type Shared = { reason: string; fail: (message: string) => void };

/** A mutation that sends the shared reason along, refreshes a list and reports an error in the shared place. */
export function useChange(
	shared: Shared,
	key: string,
	call: (reason: string) => Promise<unknown>,
	after?: () => void,
) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: () => {
			if (!shared.reason.trim())
				throw new Error("Write the reason for the change at the top first.");
			return call(shared.reason.trim());
		},
		onSuccess: () => {
			shared.fail("");
			queryClient.invalidateQueries({ queryKey: [key] });
			queryClient.invalidateQueries({ queryKey: ["setup-history"] });
			queryClient.invalidateQueries({ queryKey: ["setup-pending"] });
			after?.();
		},
		onError: (e: Error) => shared.fail(e.message),
	});
}

export function Section({
	title,
	children,
}: {
	title: string;
	children: ReactNode;
}) {
	return (
		<Card className="mb-4">
			<CardHeader className="font-medium">{title}</CardHeader>
			<CardBody>{children}</CardBody>
		</Card>
	);
}

type Pending = {
	proposal_id: number;
	area: string;
	key: string;
	old: unknown;
	new: unknown;
	reason: string;
	by_name: string;
	at: string;
	mine: boolean;
	problem: string | null;
	expired: boolean;
	expires_at: string | null;
};

/** A stored value in words: a single value shown as it is, nothing as a dash, anything else as compact JSON. */
function shown(value: unknown): string {
	if (value === null || value === undefined) return "-";
	if (typeof value === "object" && !Array.isArray(value)) {
		const entries = Object.entries(value as Record<string, unknown>);
		if (entries.length === 1) return shown(entries[0][1]);
	}
	return typeof value === "string" ? value : JSON.stringify(value);
}

/** Changes to the controls wait for a second administrator (D-22). Who proposed one cannot approve it. */
export function PendingChanges() {
	const me = useMe();
	const queryClient = useQueryClient();
	const [why, setWhy] = useState("");
	const [error, setError] = useState("");
	const data = useQuery({
		queryKey: ["setup-pending"],
		queryFn: () => api<Pending[]>("/api/setup/pending"),
		enabled: can(me.data, "audit.view"),
		refetchInterval: 5000,
	});
	const decide = useMutation({
		mutationFn: (d: { id: number; approve: boolean }) =>
			api<{ execution?: string; result?: { error?: string } }>(
				`/api/admin/proposals/${d.id}/${d.approve ? "approve" : "reject"}`,
				{ method: "POST", json: d.approve ? {} : { reason: why.trim() } },
			),
		onSuccess: (r) => {
			setError(
				r?.execution === "failed"
					? `Approved, but it could not apply: ${r.result?.error ?? ""}`
					: "",
			);
			setWhy("");
			queryClient.invalidateQueries();
		},
		onError: (e: Error) => setError(e.message),
	});
	const rows = data.data ?? [];
	if (rows.length === 0) return null;
	return (
		<Card className="mb-4">
			<CardHeader className="font-medium">
				Changes waiting for a second administrator
			</CardHeader>
			<CardBody className="space-y-3">
				<p className="text-sm text-muted-foreground">
					A change to rules, policies, automation, roles, limits, who may log in
					or a person's manager applies only when another administrator approves
					it. A change not approved within 7 days expires and nothing is
					applied.
				</p>
				<Table>
					<thead>
						<tr>
							<Th>Change</Th>
							<Th>Now</Th>
							<Th>Becomes</Th>
							<Th>Reason</Th>
							<Th>Proposed by</Th>
							<Th />
						</tr>
					</thead>
					<tbody>
						{rows.map((p) => (
							<tr key={p.proposal_id}>
								<Td>
									{p.area.replace(/_/g, " ")} {p.key}
									{p.problem ? (
										<div className="text-xs text-danger">{p.problem}</div>
									) : null}
									{p.expired ? (
										<div className="text-xs text-danger">
											Expired unapproved: nothing was applied. Propose it again
											if it is still needed.
										</div>
									) : null}
								</Td>
								<Td>{shown(p.old)}</Td>
								<Td>{shown(p.new)}</Td>
								<Td>{p.reason}</Td>
								<Td>
									{p.by_name}, {formatDateTime(p.at)}
								</Td>
								<Td className="whitespace-nowrap">
									{p.expired ? (
										<span className="text-xs text-muted-foreground">
											Expired {p.expires_at ? formatDateTime(p.expires_at) : ""}
										</span>
									) : p.mine ? (
										<span className="text-xs text-muted-foreground">
											Yours: another administrator approves it
										</span>
									) : (
										<Button
											disabled={decide.isPending}
											onClick={() =>
												decide.mutate({ id: p.proposal_id, approve: true })
											}
										>
											Approve
										</Button>
									)}{" "}
									{p.expired ? null : (
										<Button
											variant="outline"
											disabled={decide.isPending || !why.trim()}
											onClick={() =>
												decide.mutate({ id: p.proposal_id, approve: false })
											}
										>
											Reject
										</Button>
									)}
								</Td>
							</tr>
						))}
					</tbody>
				</Table>
				<Field label="Reason for rejecting (needed to reject)">
					<Input value={why} onChange={(e) => setWhy(e.target.value)} />
				</Field>
				{error ? (
					<p role="alert" className="text-sm text-danger">
						{error}
					</p>
				) : null}
			</CardBody>
		</Card>
	);
}
