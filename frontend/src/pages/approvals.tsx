import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, PageTitle } from "../components/ui/card";
import { Field, Input, Textarea } from "../components/ui/input";
import { api } from "../lib/api";
import { formatDateTime, formatInr, formatInrWords } from "../lib/format";

type Proposal = {
	id: number;
	action: string;
	summary: string;
	payload: Record<string, unknown>;
	proposed_by_type: string;
	proposed_by_id: string;
	risk: string;
	required_permission: string;
	value: string | null;
	status: string;
	created_at: string;
};

function ProposalCard({ p, onDone }: { p: Proposal; onDone: () => void }) {
	const [mode, setMode] = useState<"view" | "edit" | "reject">("view");
	const [payload, setPayload] = useState(JSON.stringify(p.payload, null, 2));
	const [note, setNote] = useState("");
	const [error, setError] = useState("");

	const decide = useMutation({
		mutationFn: (body: { path: string; json: unknown }) =>
			api(`/api/admin/proposals/${p.id}/${body.path}`, {
				method: "POST",
				json: body.json,
			}),
		onSuccess: onDone,
		onError: (e: Error) => setError(e.message),
	});

	function approve(edited: boolean) {
		setError("");
		let edited_payload: unknown;
		if (edited) {
			try {
				edited_payload = JSON.parse(payload);
			} catch {
				setError("The edited details are not valid JSON.");
				return;
			}
		}
		decide.mutate({
			path: "approve",
			json: { note: note || undefined, edited_payload },
		});
	}

	return (
		<Card>
			<CardBody className="space-y-3">
				<div className="flex flex-wrap items-start justify-between gap-2">
					<div className="min-w-0">
						<div className="font-medium">{p.summary}</div>
						<div className="text-xs text-muted-foreground">
							Proposed by {p.proposed_by_type} {p.proposed_by_id} ·{" "}
							{formatDateTime(p.created_at)}
						</div>
					</div>
					<div className="flex items-center gap-2">
						<Badge>{p.risk}</Badge>
						{p.value ? (
							<span className="text-sm font-medium" title={formatInr(p.value)}>
								{formatInrWords(p.value)}
							</span>
						) : null}
					</div>
				</div>
				<details className="text-sm">
					<summary className="cursor-pointer font-medium text-primary">
						What exactly will run
					</summary>
					<pre className="mt-2 max-w-full overflow-x-auto rounded-md bg-muted p-3 text-xs">
						{JSON.stringify(p.payload, null, 2)}
					</pre>
				</details>
				{p.action === "payment_run.approve" ? (
					<p className="text-sm">
						Approve it on the run's page (a one-time code is needed):{" "}
						<a className="text-primary underline" href={`/#/payments/${String(p.payload.run_no ?? "")}`}>
							open the run
						</a>
					</p>
				) : null}
				{mode === "edit" ? (
					<Field label="Edit the details before approving (JSON)">
						<Textarea
							rows={6}
							value={payload}
							onChange={(e) => setPayload(e.target.value)}
						/>
					</Field>
				) : null}
				{mode === "reject" ? (
					<Field label="Reason for rejecting (required)">
						<Input value={note} onChange={(e) => setNote(e.target.value)} />
					</Field>
				) : (
					<Field label="Note (optional)">
						<Input value={note} onChange={(e) => setNote(e.target.value)} />
					</Field>
				)}
				{error ? (
					<p role="alert" className="text-sm text-danger">
						{error}
					</p>
				) : null}
				<div className="flex flex-wrap gap-2">
					{mode === "reject" ? (
						<Button
							variant="danger"
							disabled={decide.isPending || !note.trim()}
							onClick={() =>
								decide.mutate({ path: "reject", json: { reason: note } })
							}
						>
							Confirm reject
						</Button>
					) : (
						<Button
							disabled={decide.isPending || p.action === "payment_run.approve"}
							onClick={() => approve(mode === "edit")}
						>
							{mode === "edit"
								? "Send changes for another approver"
								: "Approve"}
						</Button>
					)}
					<Button
						variant="outline"
						onClick={() => setMode(mode === "edit" ? "view" : "edit")}
					>
						{mode === "edit" ? "Cancel edit" : "Edit first"}
					</Button>
					<Button
						variant="outline"
						onClick={() => setMode(mode === "reject" ? "view" : "reject")}
					>
						{mode === "reject" ? "Cancel" : "Reject"}
					</Button>
				</div>
			</CardBody>
		</Card>
	);
}

export function ApprovalsPage() {
	const queryClient = useQueryClient();
	const { data, isLoading } = useQuery({
		queryKey: ["proposals", "pending"],
		queryFn: () => api<Proposal[]>("/api/admin/proposals?status=pending"),
	});
	return (
		<>
			<PageTitle sub="Actions an agent or service proposed. Nothing runs until you approve it.">
				Approvals
			</PageTitle>
			{isLoading ? (
				<p className="text-sm text-muted-foreground">Loading…</p>
			) : null}
			{data?.length === 0 ? (
				<Card className="max-w-3xl">
					<CardBody className="text-center">
						<div className="font-semibold">Nothing is waiting for you.</div>
						<p className="text-sm text-muted-foreground">
							New proposals appear here as agents and services make them.
						</p>
					</CardBody>
				</Card>
			) : null}
			<div className="grid max-w-3xl gap-3">
				{data?.map((p) => (
					<ProposalCard
						key={p.id}
						p={p}
						onDone={() =>
							queryClient.invalidateQueries({ queryKey: ["proposals"] })
						}
					/>
				))}
			</div>
		</>
	);
}
