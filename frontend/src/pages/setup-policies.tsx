import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "../components/ui/button";
import { Card, CardHeader } from "../components/ui/card";
import { Field, Input, Textarea } from "../components/ui/input";
import { Table, Td, Th } from "../components/ui/table";
import { api } from "../lib/api";
import { day, RuleFrame, StartsOn, today, type Version } from "./setup-buying";
import { type Shared, select, useChange } from "./setup-shared";

type LastChange = { at: string; by: string; reason: string } | null;
type PolicyRow = {
	key: string;
	label: string;
	help: string;
	kind: string;
	default: string;
	value: string;
	set: boolean;
	min: number | null;
	max: number | null;
	choices: string[];
	last_change: LastChange;
};
type ToleranceRow = {
	key: string;
	label: string;
	values: { category: string; value: string; last_change: LastChange }[];
};
type ToleranceVersion = Version & {
	values: { key: string; category: string; value: string }[];
};
type PoliciesData = {
	settings: PolicyRow[];
	tolerances: ToleranceRow[];
	categories: string[];
	in_force: number;
	versions: ToleranceVersion[];
};

function since(c: LastChange): string {
	return c ? `Last changed ${c.at.slice(0, 10)}: ${c.reason}` : "Never changed";
}

export function Policies(shared: Shared) {
	const data = useQuery({
		queryKey: ["setup-policies"],
		queryFn: () => api<PoliciesData>("/api/setup/policies"),
	});
	const [edits, setEdits] = useState<Record<string, string>>({});
	const [tol, setTol] = useState({
		key: "price_pct",
		category: "ANY",
		value: "",
	});
	const [from, setFrom] = useState(today());
	const save = useChange(shared, "setup-policies", (reason) =>
		api(`/api/setup/tolerances/${tol.key}`, {
			method: "PUT",
			json: {
				category: tol.category,
				value: tol.value,
				effective_from: from,
				reason,
			},
		}),
	);
	const d = data.data;
	if (!d) return null;
	const inForce = d.versions.find((v) => v.version === d.in_force);

	async function saveSetting(key: string) {
		if (!shared.reason.trim()) {
			shared.fail("Write the reason for the change at the top first.");
			return;
		}
		try {
			await api(`/api/setup/policies/${key}`, {
				method: "PUT",
				json: { value: edits[key], reason: shared.reason.trim() },
			});
			shared.fail("");
			setEdits({ ...edits, [key]: undefined as unknown as string });
			await data.refetch();
		} catch (e) {
			shared.fail((e as Error).message);
		}
	}

	return (
		<>
			<Card className="mb-4">
				<CardHeader className="font-medium">Policies</CardHeader>
				<Table>
					<thead>
						<tr>
							<Th>Policy</Th>
							<Th>Value</Th>
							<Th>History</Th>
							<Th />
						</tr>
					</thead>
					<tbody>
						{d.settings.map((p) => {
							const value = edits[p.key] ?? p.value;
							return (
								<tr key={p.key}>
									<Td>
										{p.label}
										{p.help ? (
											<div className="text-xs text-muted-foreground">
												{p.help}
											</div>
										) : null}
									</Td>
									<Td className={p.kind === "text" ? "w-[28rem]" : "w-56"}>
										{p.kind === "bool" || p.kind === "choice" ? (
											<select
												className={select}
												value={value}
												onChange={(e) =>
													setEdits({ ...edits, [p.key]: e.target.value })
												}
											>
												{(p.kind === "bool"
													? ["true", "false"]
													: p.choices
												).map((c) => (
													<option key={c}>{c}</option>
												))}
											</select>
										) : p.kind === "text" ? (
											<Textarea
												rows={12}
												value={value}
												onChange={(e) =>
													setEdits({ ...edits, [p.key]: e.target.value })
												}
											/>
										) : (
											<Input
												inputMode={
													p.kind === "email" ? "email" : p.kind === "host" ? "url" : "decimal"
												}
												placeholder={p.kind === "email" || p.kind === "host" ? "none" : undefined}
												value={value}
												onChange={(e) =>
													setEdits({ ...edits, [p.key]: e.target.value })
												}
											/>
										)}
										<div className="text-xs text-muted-foreground">
											{p.set ? "" : "default · "}
											{p.min !== null ? `from ${p.min}` : ""}
											{p.max !== null ? ` to ${p.max}` : ""}
										</div>
									</Td>
									<Td className="text-xs text-muted-foreground">
										{since(p.last_change)}
									</Td>
									<Td>
										<Button
											variant="outline"
											disabled={edits[p.key] === undefined || value === p.value}
											onClick={() => saveSetting(p.key)}
										>
											Save
										</Button>
									</Td>
								</tr>
							);
						})}
					</tbody>
				</Table>
			</Card>
			<RuleFrame
				title="Tolerances"
				current={
					<>
						<p className="mb-2" data-testid="tolerance-in-force">
							Version in force: {d.in_force}
							{inForce ? ` (from ${day(inForce.effective_from)})` : ""}. By item
							category; ANY applies where a category has none of its own.
						</p>
						<Table>
							<thead>
								<tr>
									<Th>Tolerance</Th>
									<Th>Category: value</Th>
								</tr>
							</thead>
							<tbody>
								{d.tolerances.map((t) => (
									<tr key={t.key}>
										<Td>{t.label}</Td>
										<Td>
											{t.values.length
												? t.values.map((v) => (
														<button
															type="button"
															key={v.category}
															className="mr-3 underline"
															title={since(v.last_change)}
															onClick={() =>
																setTol({
																	key: t.key,
																	category: v.category,
																	value: v.value,
																})
															}
														>
															{v.category}: {v.value}
														</button>
													))
												: "not set"}
										</Td>
									</tr>
								))}
							</tbody>
						</Table>
					</>
				}
				versions={d.versions}
				history={(x) => {
					const row = x as ToleranceVersion;
					return (
						<div className="text-xs">
							{row.values.map((v) => (
								<div key={`${v.key}-${v.category}`}>
									{v.key} ({v.category}): {v.value}
								</div>
							))}
						</div>
					);
				}}
				form={
					<div className="grid gap-3 md:grid-cols-4">
						<Field label="Tolerance">
							<select
								className={select}
								value={tol.key}
								onChange={(e) => setTol({ ...tol, key: e.target.value })}
							>
								{d.tolerances.map((t) => (
									<option key={t.key} value={t.key}>
										{t.label}
									</option>
								))}
							</select>
						</Field>
						<Field label="Category">
							<select
								className={select}
								value={tol.category}
								onChange={(e) => setTol({ ...tol, category: e.target.value })}
							>
								{d.categories.map((c) => (
									<option key={c}>{c}</option>
								))}
							</select>
						</Field>
						<Field label="Value">
							<Input
								inputMode="decimal"
								value={tol.value}
								onChange={(e) => setTol({ ...tol, value: e.target.value })}
							/>
						</Field>
						<StartsOn value={from} onChange={setFrom} />
						<div className="flex items-end">
							<Button onClick={() => save.mutate()}>Save tolerance</Button>
						</div>
					</div>
				}
			/>
		</>
	);
}

type Change = {
	id: number;
	at: string;
	by_name: string | null;
	approved_by_name: string | null;
	area: string;
	key: string;
	old_value: unknown;
	new_value: unknown;
	reason: string;
};

export function History() {
	const [area, setArea] = useState("");
	const changes = useQuery({
		queryKey: ["setup-history", area],
		queryFn: () =>
			api<Change[]>(
				`/api/setup/changes?limit=200${area ? `&area=${encodeURIComponent(area)}` : ""}`,
			),
	});
	const show = (v: unknown) =>
		v === null || v === undefined ? "-" : JSON.stringify(v);
	return (
		<Card>
			<CardHeader className="flex items-center justify-between font-medium">
				<span>Every change, newest first</span>
				<select
					className={`${select} w-48`}
					value={area}
					onChange={(e) => setArea(e.target.value)}
				>
					<option value="">All areas</option>
					{[
						"person",
						"role_grant",
						"role_revoke",
						"limit",
						"manager",
						"role_permission",
						"approver_role",
						"department_head",
						"approval_rule",
						"policy",
						"tolerance",
						"automation",
					].map((a) => (
						<option key={a}>{a}</option>
					))}
				</select>
			</CardHeader>
			<Table>
				<thead>
					<tr>
						<Th>When</Th>
						<Th>Who</Th>
						<Th>Approved by</Th>
						<Th>What</Th>
						<Th>Was</Th>
						<Th>Now</Th>
						<Th>Why</Th>
					</tr>
				</thead>
				<tbody>
					{changes.data?.map((c) => (
						<tr key={c.id}>
							<Td>{c.at.slice(0, 16).replace("T", " ")}</Td>
							<Td>{c.by_name ?? "-"}</Td>
							<Td>{c.approved_by_name ?? "-"}</Td>
							<Td>
								{c.area}
								<div className="text-xs text-muted-foreground">{c.key}</div>
							</Td>
							<Td className="font-mono text-xs">{show(c.old_value)}</Td>
							<Td className="font-mono text-xs">{show(c.new_value)}</Td>
							<Td>{c.reason}</Td>
						</tr>
					))}
				</tbody>
			</Table>
		</Card>
	);
}
