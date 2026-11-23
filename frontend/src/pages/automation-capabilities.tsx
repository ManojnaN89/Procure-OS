import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Table, Td, Th } from "../components/ui/table";
import { api } from "../lib/api";

type Capability = {
	code: string;
	label: string;
	enabled: boolean;
	decisions: { decision_type: string; label: string; level: string }[];
	checks: Record<string, unknown> | null;
	changed_at: string | null;
	changed_by: string | null;
	waiting: number | null;
	open_work: Record<string, number>;
};
type Capabilities = { capabilities: Capability[]; kill_switch: boolean };
type Cost = {
	month: string;
	spent_usd: string;
	cap_usd: string | null;
	warn_pct: number;
	by_capability: { capability: string; calls: number; cost_usd: string }[];
	unpriced_models: string[];
};
type Checks = {
	permission_tests: string;
	eval: string;
	eval_threshold: string;
	eval_result: string;
	tracing: boolean;
	switch_off_tested: boolean;
};
const NO_CHECKS: Checks = {
	permission_tests: "",
	eval: "",
	eval_threshold: "",
	eval_result: "",
	tracing: false,
	switch_off_tested: false,
};

/** The AI capabilities (D49): one switch each. On waits for a second administrator with the D54 checks; off is at once
 * and hands the open work to people. Below it, this month's AI spend against its budget. */
export function CapabilitiesCard({ reason }: { reason: string }) {
	const queryClient = useQueryClient();
	const data = useQuery({
		queryKey: ["setup-capabilities"],
		queryFn: () => api<Capabilities>("/api/setup/capabilities"),
	});
	const cost = useQuery({
		queryKey: ["setup-ai-cost"],
		queryFn: () => api<Cost>("/api/setup/ai-cost"),
	});
	const [opening, setOpening] = useState<string | null>(null);
	const [checks, setChecks] = useState<Checks>(NO_CHECKS);
	const [message, setMessage] = useState("");

	async function put(code: string, json: unknown) {
		if (!reason.trim()) {
			setMessage("Write the reason for the change first.");
			return;
		}
		try {
			const got = await api<{ handed_over?: Record<string, number> }>(
				`/api/setup/capabilities/${code}`,
				{ method: "PUT", json },
			);
			setMessage(
				got.handed_over
					? `Switched off. Handed to people: ${got.handed_over.tasks} tasks.`
					: "Saved for approval: another administrator has to approve it.",
			);
			setOpening(null);
			setChecks(NO_CHECKS);
			await queryClient.invalidateQueries({ queryKey: ["setup-capabilities"] });
			await queryClient.invalidateQueries({ queryKey: ["setup-pending"] });
		} catch (e) {
			setMessage((e as Error).message);
		}
	}
	const d = data.data;
	if (!d) return null;
	const c = cost.data;
	return (
		<Card className="mb-4">
			<CardHeader className="font-medium">AI capabilities</CardHeader>
			{message ? (
				<p role="status" className="px-4 pt-2 text-sm">
					{message}
				</p>
			) : null}
			<Table>
				<thead>
					<tr>
						<Th>Capability</Th>
						<Th>State</Th>
						<Th>Decisions it owns</Th>
						<Th>Open work</Th>
						<Th />
					</tr>
				</thead>
				<tbody>
					{d.capabilities.map((cap) => (
						<tr key={cap.code}>
							<Td>
								{cap.label}
								<div className="text-xs text-muted-foreground">{cap.code}</div>
							</Td>
							<Td>
								<Badge tone={cap.enabled ? "success" : "info"}>
									{cap.enabled ? "on" : "off"}
								</Badge>
								{cap.waiting ? (
									<div className="text-xs text-muted-foreground">
										switch-on waiting (change {cap.waiting})
									</div>
								) : null}
							</Td>
							<Td className="text-xs">
								{cap.decisions
									.map((x) => `${x.label}: ${x.level}`)
									.join(", ") || "-"}
							</Td>
							<Td className="text-xs">
								{Object.entries(cap.open_work)
									.filter(([, n]) => n)
									.map(([k, n]) => `${n} ${k}`)
									.join(", ") || "none"}
							</Td>
							<Td>
								{cap.enabled ? (
									<Button
										variant="danger"
										onClick={() =>
											put(cap.code, { on: false, reason: reason.trim() })
										}
									>
										Switch off now
									</Button>
								) : (
									<Button
										variant="outline"
										disabled={cap.waiting !== null}
										onClick={() =>
											setOpening(opening === cap.code ? null : cap.code)
										}
									>
										Ask to switch on
									</Button>
								)}
							</Td>
						</tr>
					))}
				</tbody>
			</Table>
			{opening ? (
				<CardBody className="space-y-2">
					<div className="text-sm">
						Before {opening} is switched on (D54): name the permission tests,
						the eval with its threshold and result, and confirm tracing and the
						switch-off were tried. A second administrator approves.
					</div>
					{(
						[
							"permission_tests",
							"eval",
							"eval_threshold",
							"eval_result",
						] as const
					).map((k) => (
						<Input
							key={k}
							placeholder={k.replaceAll("_", " ")}
							value={checks[k]}
							onChange={(e) => setChecks({ ...checks, [k]: e.target.value })}
						/>
					))}
					{(["tracing", "switch_off_tested"] as const).map((k) => (
						<label key={k} className="mr-4 text-sm">
							<input
								type="checkbox"
								checked={checks[k]}
								onChange={(e) =>
									setChecks({ ...checks, [k]: e.target.checked })
								}
							/>{" "}
							{k === "tracing"
								? "Runs and model calls are traced"
								: "Switching it off was tried"}
						</label>
					))}
					<div>
						<Button
							onClick={() =>
								put(opening, { on: true, reason: reason.trim(), checks })
							}
						>
							Ask to switch on
						</Button>
					</div>
				</CardBody>
			) : null}
			{c ? (
				<CardBody className="text-sm">
					<strong>AI spend in {c.month}:</strong> ${c.spent_usd}
					{c.cap_usd
						? ` of a budget of $${c.cap_usd} (warning at ${c.warn_pct}%)`
						: " (no budget set)"}
					.{" "}
					{c.by_capability
						.map((r) => `${r.capability}: ${r.calls} calls, $${r.cost_usd}`)
						.join("; ")}
					{c.unpriced_models.length
						? ` Models with no known price: ${c.unpriced_models.join(", ")}.`
						: ""}
				</CardBody>
			) : null}
		</Card>
	);
}
