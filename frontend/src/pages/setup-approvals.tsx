import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader } from "../components/ui/card";
import { Field, Input } from "../components/ui/input";
import { Table, Td, Th } from "../components/ui/table";
import { api } from "../lib/api";
import { type Shared, select, useChange } from "./setup-shared";

type ApproverRole = {
	role: string;
	source: string;
	value: string | null;
	resolves_to: string;
	in_use: boolean;
};
type ApproversData = {
	roles: ApproverRole[];
	sources: string[];
	departments: { code: string; name: string; head_user: string | null }[];
	designations: string[];
	people: { code: string; name: string; designation: string | null }[];
};

const SOURCE_LABEL: Record<string, string> = {
	requester_manager: "The requester's own manager",
	department_head: "Head of the request's department",
	department_head_of: "Head of a named department",
	designation: "Whoever holds a designation",
	location_head: "Head of the request's delivery location",
};

export function Approvers(shared: Shared) {
	const data = useQuery({
		queryKey: ["setup-approvers"],
		queryFn: () => api<ApproversData>("/api/setup/approvers"),
	});
	const [form, setForm] = useState({
		role: "",
		source: "designation",
		value: "",
	});
	const [head, setHead] = useState({ code: "", person: "" });
	const save = useChange(shared, "setup-approvers", (reason) =>
		api(`/api/setup/approvers/${form.role}`, {
			method: "PUT",
			json: { source: form.source, value: form.value || null, reason },
		}),
	);
	const setHeadMutation = useChange(shared, "setup-approvers", (reason) =>
		api(`/api/setup/departments/${head.code}/head`, {
			method: "PUT",
			json: { person_code: head.person, reason },
		}),
	);
	const d = data.data;
	if (!d) return null;

	async function remove(role: string) {
		if (!shared.reason.trim()) {
			shared.fail("Write the reason for the change at the top first.");
			return;
		}
		try {
			await api(
				`/api/setup/approvers/${role}?reason=${encodeURIComponent(shared.reason.trim())}`,
				{ method: "DELETE" },
			);
			shared.fail("");
			await data.refetch();
		} catch (e) {
			shared.fail((e as Error).message);
		}
	}

	return (
		<>
			<Card className="mb-4">
				<CardHeader className="font-medium">
					Who fills each role in an approval chain
				</CardHeader>
				<Table>
					<thead>
						<tr>
							<Th>Role</Th>
							<Th>Found by</Th>
							<Th>Who that is now</Th>
							<Th>Used by a chain</Th>
							<Th />
						</tr>
					</thead>
					<tbody>
						{d.roles.map((r) => (
							<tr key={r.role}>
								<Td>
									<button
										type="button"
										className="underline"
										onClick={() =>
											setForm({
												role: r.role,
												source: r.source,
												value: r.value ?? "",
											})
										}
									>
										{r.role}
									</button>
								</Td>
								<Td>
									{SOURCE_LABEL[r.source] ?? r.source}
									{r.value ? `: ${r.value}` : ""}
								</Td>
								<Td>{r.resolves_to}</Td>
								<Td>{r.in_use ? "yes" : "no"}</Td>
								<Td>
									{r.in_use ? null : (
										<button
											type="button"
											className="text-xs text-danger underline"
											onClick={() => remove(r.role)}
										>
											remove
										</button>
									)}
								</Td>
							</tr>
						))}
					</tbody>
				</Table>
			</Card>
			<div className="grid gap-4 lg:grid-cols-2">
				<Card>
					<CardHeader className="font-medium">
						Add or change a role (click a role above to edit it)
					</CardHeader>
					<CardBody className="space-y-3">
						<Field label="Role name (lower case, for example procurement_head)">
							<Input
								value={form.role}
								onChange={(e) => setForm({ ...form, role: e.target.value })}
							/>
						</Field>
						<Field label="Found by">
							<select
								className={select}
								value={form.source}
								onChange={(e) => setForm({ ...form, source: e.target.value })}
							>
								{d.sources.map((s) => (
									<option key={s} value={s}>
										{SOURCE_LABEL[s] ?? s}
									</option>
								))}
							</select>
						</Field>
						{form.source === "department_head_of" ? (
							<Field label="Department">
								<select
									className={select}
									value={form.value}
									onChange={(e) => setForm({ ...form, value: e.target.value })}
								>
									<option value="">Choose</option>
									{d.departments.map((x) => (
										<option key={x.code} value={x.code}>
											{x.name} ({x.code})
										</option>
									))}
								</select>
							</Field>
						) : null}
						{form.source === "designation" ? (
							<Field label="Designation">
								<select
									className={select}
									value={form.value}
									onChange={(e) => setForm({ ...form, value: e.target.value })}
								>
									<option value="">Choose</option>
									{d.designations.map((x) => (
										<option key={x}>{x}</option>
									))}
								</select>
							</Field>
						) : null}
						<Button onClick={() => save.mutate()}>Save role</Button>
					</CardBody>
				</Card>
				<Card>
					<CardHeader className="font-medium">Head of a department</CardHeader>
					<CardBody className="space-y-3">
						<Field label="Department">
							<select
								className={select}
								value={head.code}
								onChange={(e) => setHead({ ...head, code: e.target.value })}
							>
								<option value="">Choose</option>
								{d.departments.map((x) => (
									<option key={x.code} value={x.code}>
										{x.name}: now {x.head_user ?? "nobody"}
									</option>
								))}
							</select>
						</Field>
						<Field label="New head">
							<select
								className={select}
								value={head.person}
								onChange={(e) => setHead({ ...head, person: e.target.value })}
							>
								<option value="">Choose</option>
								{d.people.map((p) => (
									<option key={p.code} value={p.code}>
										{p.name} ({p.designation})
									</option>
								))}
							</select>
						</Field>
						<Button onClick={() => setHeadMutation.mutate()}>Set head</Button>
					</CardBody>
				</Card>
			</div>
		</>
	);
}

type Rule = {
	id: number;
	rule_code: string;
	document: string;
	min_value: string;
	max_value: string;
	category: string;
	department: string;
	location: string;
	spend_type: string;
	adds: boolean;
	chain: string[];
	version: number;
	effective_from: string;
	effective_to: string | null;
	in_force: boolean;
};
type RulesData = {
	rules: Rule[];
	problems: string[];
	roles: string[];
	documents: string[];
};

function tomorrow(): string {
	const d = new Date();
	d.setDate(d.getDate() + 1);
	return d.toISOString().slice(0, 10);
}

export function ApprovalChains(shared: Shared) {
	const data = useQuery({
		queryKey: ["setup-chains"],
		queryFn: () => api<RulesData>("/api/setup/approval-rules"),
	});
	const [all, setAll] = useState(false);
	const [form, setForm] = useState({
		rule_code: "",
		document: "request",
		min_value: "",
		max_value: "",
		spend_type: "opex",
		category_code: "ANY",
		department_code: "ANY",
		location_code: "ANY",
		approver_chain: "",
		effective_from: tomorrow(),
	});
	const save = useChange(shared, "setup-chains", (reason) =>
		api("/api/setup/approval-rules", {
			method: "POST",
			json: { ...form, rule_code: form.rule_code || null, reason },
		}),
	);
	const d = data.data;
	if (!d) return null;
	const shown = d.rules.filter((r) => all || r.in_force);
	const edit = (r: Rule) =>
		setForm({
			rule_code: r.rule_code,
			document: r.document,
			min_value: r.min_value,
			max_value: r.max_value,
			spend_type: r.spend_type,
			category_code: r.category,
			department_code: r.department,
			location_code: r.location,
			approver_chain: (r.adds ? "+" : "") + r.chain.join(">"),
			effective_from: tomorrow(),
		});

	return (
		<>
			{d.problems.length ? (
				<Card className="mb-4 border-danger">
					<CardBody className="space-y-1 text-sm text-danger">
						<strong>The rule table has a problem:</strong>
						{d.problems.map((p) => (
							<div key={p}>{p}</div>
						))}
					</CardBody>
				</Card>
			) : null}
			<Card className="mb-4">
				<CardHeader className="flex items-center justify-between font-medium">
					<span>Who approves what</span>
					<label className="flex items-center gap-2 text-sm font-normal">
						<input
							type="checkbox"
							checked={all}
							onChange={(e) => setAll(e.target.checked)}
						/>
						show earlier versions
					</label>
				</CardHeader>
				<Table>
					<thead>
						<tr>
							<Th>Rule</Th>
							<Th>For</Th>
							<Th>Value from – to (₹)</Th>
							<Th>Approved by, in order</Th>
							<Th>Version</Th>
							<Th>From – to</Th>
						</tr>
					</thead>
					<tbody>
						{shown.map((r) => (
							<tr key={r.id}>
								<Td>
									<button
										type="button"
										className="underline"
										onClick={() => edit(r)}
									>
										{r.rule_code}
									</button>
								</Td>
								<Td>
									{r.document}
									<div className="text-xs text-muted-foreground">
										{r.spend_type} · {r.category} · {r.department} ·{" "}
										{r.location}
									</div>
								</Td>
								<Td>
									{r.min_value} – {r.max_value}
								</Td>
								<Td>
									{r.adds ? "adds " : ""}
									{r.chain.join(" → ")}
									{r.chain[0] === "auto" ? " (approves itself)" : ""}
								</Td>
								<Td>
									v{r.version} {r.in_force ? <Badge>approved</Badge> : null}
								</Td>
								<Td>
									{r.effective_from} – {r.effective_to ?? "open"}
								</Td>
							</tr>
						))}
					</tbody>
				</Table>
			</Card>
			<Card>
				<CardHeader className="font-medium">
					Change who approves (click a rule above to start from it). The change
					is a new version that starts on the date below; decisions already made
					keep the version they used.
				</CardHeader>
				<CardBody className="grid gap-3 md:grid-cols-4">
					<Field label="Rule (empty for a new one)">
						<Input
							value={form.rule_code}
							onChange={(e) => setForm({ ...form, rule_code: e.target.value })}
						/>
					</Field>
					<Field label="Document">
						<select
							className={select}
							value={form.document}
							onChange={(e) => setForm({ ...form, document: e.target.value })}
						>
							{d.documents.map((x) => (
								<option key={x}>{x}</option>
							))}
						</select>
					</Field>
					<Field label="Value from (₹)">
						<Input
							inputMode="decimal"
							value={form.min_value}
							onChange={(e) => setForm({ ...form, min_value: e.target.value })}
						/>
					</Field>
					<Field label="Value up to (₹)">
						<Input
							inputMode="decimal"
							value={form.max_value}
							onChange={(e) => setForm({ ...form, max_value: e.target.value })}
						/>
					</Field>
					<Field label="Spend type">
						<select
							className={select}
							value={form.spend_type}
							onChange={(e) => setForm({ ...form, spend_type: e.target.value })}
						>
							{["opex", "capex", "ANY"].map((x) => (
								<option key={x}>{x}</option>
							))}
						</select>
					</Field>
					<Field label="Location (ANY for every location)">
						<Input
							value={form.location_code}
							onChange={(e) =>
								setForm({ ...form, location_code: e.target.value || "ANY" })
							}
						/>
					</Field>
					<Field
						label={`Approvers in order, with ">" between (${d.roles.join(", ")}), "auto", or "+" first to add to the chain chosen by value`}
					>
						<Input
							value={form.approver_chain}
							onChange={(e) =>
								setForm({ ...form, approver_chain: e.target.value })
							}
							placeholder="manager>dept_head"
						/>
					</Field>
					<Field label="Starts on">
						<Input
							type="date"
							value={form.effective_from}
							onChange={(e) =>
								setForm({ ...form, effective_from: e.target.value })
							}
						/>
					</Field>
					<div className="flex items-end">
						<Button onClick={() => save.mutate()}>Save new version</Button>
					</div>
				</CardBody>
			</Card>
		</>
	);
}
