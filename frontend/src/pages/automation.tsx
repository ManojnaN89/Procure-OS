import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader, PageTitle } from "../components/ui/card";
import { Field, Input } from "../components/ui/input";
import { Table, Td, Th } from "../components/ui/table";
import { api } from "../lib/api";
import { can, useMe } from "../lib/me";
import { PendingChanges, select } from "./setup-shared";

type Decision = {
	decision_type: string;
	label: string;
	help: string;
	locked: boolean;
	level: string;
	levels: string[];
	value_cap: string | null;
	min_confidence: string | null;
	max_wrong_rate: string | null;
	record: { automatic: number; wrong: number };
	note: string | null;
};
type Automation = {
	kill_switch: boolean;
	decisions: Decision[];
	last_kill_switch: { at: string; reason: string } | null;
};

const LEVEL_LABEL: Record<string, string> = {
	off: "Off: the platform does nothing",
	propose: "Recommend: a person approves each one",
	auto: "Act within the limits below",
};

export function AutomationPage() {
	const queryClient = useQueryClient();
	const me = useMe();
	const allowed = can(me.data, "admin.settings");
	const data = useQuery({
		queryKey: ["setup-automation"],
		queryFn: () => api<Automation>("/api/setup/automation"),
		enabled: allowed,
	});
	const [reason, setReason] = useState("");
	const [error, setError] = useState("");
	const [edits, setEdits] = useState<Record<string, Partial<Decision>>>({});

	async function call(path: string, json: unknown) {
		if (!reason.trim()) {
			setError("Write the reason for the change first.");
			return;
		}
		try {
			await api(path, { method: "PUT", json });
			setError("");
			setEdits({});
			await queryClient.invalidateQueries({ queryKey: ["setup-automation"] });
			await queryClient.invalidateQueries({ queryKey: ["setup-history"] });
			await queryClient.invalidateQueries({ queryKey: ["setup-pending"] });
		} catch (e) {
			setError((e as Error).message);
		}
	}
	const d = data.data;
	if (me.data && !allowed)
		return (
			<PageTitle sub="This page is for the Admin.">
				Automation controls
			</PageTitle>
		);
	if (!d) return null;

	return (
		<>
			<PageTitle sub="How much the platform may do without a person. Payment runs, activating a supplier and releasing a payment block are always decided by a person.">
				Automation controls
			</PageTitle>
			<Card className={`mb-4 ${d.kill_switch ? "border-danger" : ""}`}>
				<CardBody className="flex flex-wrap items-center justify-between gap-3">
					<div>
						<strong>
							{d.kill_switch
								? "The kill switch is ON: nothing runs automatically."
								: "The kill switch is off."}
						</strong>
						<div className="text-sm text-muted-foreground">
							Switching it on stops every automatic action at the next decision.
							People can still do everything by hand. Switching it off again
							waits for a second administrator.
							{d.last_kill_switch
								? ` Last changed ${d.last_kill_switch.at.slice(0, 10)}: ${d.last_kill_switch.reason}`
								: ""}
						</div>
					</div>
					<Button
						variant={d.kill_switch ? "outline" : "danger"}
						onClick={() =>
							call("/api/setup/automation-kill-switch", {
								on: !d.kill_switch,
								reason: reason.trim(),
							})
						}
					>
						{d.kill_switch ? "Switch off" : "Stop all automation"}
					</Button>
				</CardBody>
			</Card>
			<Card className="mb-4">
				<CardBody>
					<Field label="Reason for the change you are about to make (needed for every change)">
						<Input
							value={reason}
							onChange={(e) => setReason(e.target.value)}
							placeholder="For example: no wrong awards for a month"
						/>
					</Field>
				</CardBody>
			</Card>
			{error ? (
				<p role="alert" className="mb-3 text-sm text-danger">
					{error}
				</p>
			) : null}
			<PendingChanges />
			<Card>
				<CardHeader className="font-medium">Decisions</CardHeader>
				<Table>
					<thead>
						<tr>
							<Th>Decision</Th>
							<Th>Level</Th>
							<Th>Value cap (₹)</Th>
							<Th>Confidence at least</Th>
							<Th>Wrong rate at most</Th>
							<Th>Last 30 days</Th>
							<Th />
						</tr>
					</thead>
					<tbody>
						{d.decisions.map((row) => {
							const edit = edits[row.decision_type] ?? {};
							const level = edit.level ?? row.level;
							const cap = edit.value_cap ?? row.value_cap ?? "";
							const conf = edit.min_confidence ?? row.min_confidence ?? "";
							const wrong = edit.max_wrong_rate ?? row.max_wrong_rate ?? "";
							const set = (patch: Partial<Decision>) =>
								setEdits({
									...edits,
									[row.decision_type]: { ...edit, ...patch },
								});
							return (
								<tr key={row.decision_type}>
									<Td>
										{row.label}
										<div className="text-xs text-muted-foreground">
											{row.locked ? row.note : row.help}
										</div>
									</Td>
									<Td className="w-64">
										{row.locked ? (
											<Badge tone="info">person only</Badge>
										) : (
											<select
												className={select}
												value={level}
												onChange={(e) => set({ level: e.target.value })}
											>
												{row.levels.map((l) => (
													<option key={l} value={l}>
														{LEVEL_LABEL[l] ?? l}
													</option>
												))}
											</select>
										)}
									</Td>
									{row.locked || row.decision_type === "po.send" ? (
										<Td colSpan={3} className="text-xs text-muted-foreground">
											{row.note}
										</Td>
									) : (
										<>
											<Td>
												<Input
													inputMode="decimal"
													value={cap}
													placeholder="no cap"
													onChange={(e) => set({ value_cap: e.target.value })}
												/>
											</Td>
											<Td>
												<Input
													inputMode="decimal"
													value={conf}
													onChange={(e) =>
														set({ min_confidence: e.target.value })
													}
												/>
											</Td>
											<Td>
												<Input
													inputMode="decimal"
													value={wrong}
													onChange={(e) =>
														set({ max_wrong_rate: e.target.value })
													}
												/>
											</Td>
										</>
									)}
									<Td>
										{row.record.automatic} automatic
										{row.record.wrong ? (
											<span className="text-danger">
												, {row.record.wrong} wrong
											</span>
										) : null}
									</Td>
									<Td>
										{row.locked ? null : (
											<Button
												variant="outline"
												disabled={!edits[row.decision_type]}
												onClick={() =>
													call(`/api/setup/automation/${row.decision_type}`, {
														level,
														value_cap: cap === "" ? null : cap,
														min_confidence: conf === "" ? "0.9" : conf,
														max_wrong_rate: wrong === "" ? "0.02" : wrong,
														reason: reason.trim(),
													})
												}
											>
												Save
											</Button>
										)}
									</Td>
								</tr>
							);
						})}
					</tbody>
				</Table>
			</Card>
		</>
	);
}
