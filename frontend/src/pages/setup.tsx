import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader, PageTitle } from "../components/ui/card";
import { Field, Input } from "../components/ui/input";
import { Table, Td, Th } from "../components/ui/table";
import { api } from "../lib/api";
import { can, useMe } from "../lib/me";
import { ApprovalChains, Approvers } from "./setup-approvals";
import {
	BuyingRoute,
	ForwardBuying,
	NonPoRules,
	PriceThresholds,
	SingleSource,
	SupplierSelection,
	Touchless,
} from "./setup-buying";
import { GoLive } from "./setup-golive";
import { History, Policies } from "./setup-policies";
import { PendingChanges, type Shared, select, useChange } from "./setup-shared";

type Person = {
	user_id: number;
	name: string;
	email: string | null;
	active: boolean;
	roles: { id: number; role: string; location: string | null }[];
	limits: { id: number; document: string; max_value: string }[];
	person: {
		code: string;
		name: string;
		designation: string | null;
		manager_code: string | null;
	} | null;
};
type People = {
	people: Person[];
	roles: string[];
	org: { code: string; name: string; designation: string | null }[];
	documents: string[];
};
type RoleRow = { role: string; permissions: string[] };
type Roles = {
	roles: RoleRow[];
	permissions: { action: string; label: string; admin_only: boolean }[];
};

const TABS = [
	{ id: "people", label: "People", need: ["admin.users"] },
	{ id: "roles", label: "Roles", need: ["admin.users"] },
	{ id: "approvers", label: "Approvers", need: ["admin.rules"] },
	{ id: "chains", label: "Approval chains", need: ["admin.rules"] },
	{ id: "route", label: "Buying route", need: ["admin.rules"] },
	{ id: "selection", label: "Supplier selection", need: ["admin.rules"] },
	{ id: "touchless", label: "Touchless orders", need: ["admin.rules"] },
	{ id: "forward-buy", label: "Forward buying", need: ["admin.rules"] },
	{ id: "thresholds", label: "Price thresholds", need: ["admin.rules"] },
	{ id: "non-po", label: "Bills without an order", need: ["admin.rules"] },
	{ id: "single", label: "Single-source items", need: ["admin.rules"] },
	{ id: "policies", label: "Policies", need: ["admin.settings"] },
	{ id: "history", label: "History", need: ["audit.view", "admin.settings"] },
	{
		id: "golive",
		label: "Go-live",
		need: ["admin.users", "admin.rules", "admin.settings", "audit.view"],
	},
];

export function SetupPage() {
	const me = useMe();
	const tabs = TABS.filter((t) => can(me.data, ...t.need));
	const [tab, setTab] = useState("");
	const [reason, setReason] = useState("");
	const [error, setError] = useState("");
	const active = tab || tabs[0]?.id || "";
	const shared: Shared = {
		reason,
		fail: (message) => setError(message),
	};

	return (
		<>
			<PageTitle sub="Who the people are and what they may do, who approves what, how things are bought, and the company's policies. Every change is logged with its reason.">
				Company setup
			</PageTitle>
			<Card className="mb-4">
				<CardBody className="space-y-3">
					<Field label="Reason for the change you are about to make (needed for every change)">
						<Input
							value={reason}
							onChange={(e) => setReason(e.target.value)}
							placeholder="For example: new buyer joined, tolerance reviewed with finance"
						/>
					</Field>
					<div className="flex flex-wrap gap-2" role="tablist">
						{tabs.map((t) => (
							<Button
								key={t.id}
								role="tab"
								aria-selected={active === t.id}
								variant={active === t.id ? "default" : "outline"}
								onClick={() => {
									setTab(t.id);
									setError("");
								}}
							>
								{t.label}
							</Button>
						))}
					</div>
				</CardBody>
			</Card>
			{error ? (
				<p role="alert" className="mb-3 text-sm text-danger">
					{error}
				</p>
			) : null}
			<PendingChanges />
			{me.data && tabs.length === 0 ? (
				<p className="text-sm text-muted-foreground">
					You do not have access to the company setup. It is for the Admin.
				</p>
			) : null}
			{active === "people" ? <PeopleTab {...shared} /> : null}
			{active === "roles" ? <RolesTab {...shared} /> : null}
			{active === "approvers" ? <Approvers {...shared} /> : null}
			{active === "chains" ? <ApprovalChains {...shared} /> : null}
			{active === "route" ? <BuyingRoute {...shared} /> : null}
			{active === "selection" ? <SupplierSelection {...shared} /> : null}
			{active === "touchless" ? <Touchless {...shared} /> : null}
			{active === "forward-buy" ? <ForwardBuying {...shared} /> : null}
			{active === "thresholds" ? <PriceThresholds {...shared} /> : null}
			{active === "non-po" ? <NonPoRules {...shared} /> : null}
			{active === "single" ? <SingleSource {...shared} /> : null}
			{active === "policies" ? <Policies {...shared} /> : null}
			{active === "history" ? <History /> : null}
			{active === "golive" ? <GoLive {...shared} /> : null}
		</>
	);
}

function PeopleTab(shared: Shared) {
	const queryClient = useQueryClient();
	const data = useQuery({
		queryKey: ["setup-people"],
		queryFn: () => api<People>("/api/setup/people"),
	});
	const [add, setAdd] = useState({
		email: "",
		display_name: "",
		password: "",
		role: "Requester",
	});
	const [grant, setGrant] = useState({
		user_id: "",
		role: "Store",
		location: "",
	});
	const [limit, setLimit] = useState({
		user_id: "",
		document: "po",
		max_value: "",
	});
	const [manager, setManager] = useState({ user_id: "", manager_code: "" });
	const addPerson = useChange(shared, "setup-people", (reason) =>
		api("/api/setup/people", { method: "POST", json: { ...add, reason } }),
	);
	const giveRole = useChange(shared, "setup-people", (reason) =>
		api(`/api/setup/people/${grant.user_id}/roles`, {
			method: "POST",
			json: {
				role: grant.role,
				location: grant.location || null,
				reason,
			},
		}),
	);
	const setLimitMutation = useChange(shared, "setup-people", (reason) =>
		api(`/api/setup/people/${limit.user_id}/limits`, {
			method: "PUT",
			json: { ...limit, reason },
		}),
	);
	const setManagerMutation = useChange(shared, "setup-people", (reason) =>
		api(`/api/setup/people/${manager.user_id}/manager`, {
			method: "PUT",
			json: { manager_code: manager.manager_code || null, reason },
		}),
	);
	const people = data.data?.people ?? [];

	async function act(path: string, init: RequestInit & { json?: unknown }) {
		if (!shared.reason.trim()) {
			shared.fail("Write the reason for the change at the top first.");
			return;
		}
		try {
			await api(path, init);
			shared.fail("");
			await data.refetch();
			await queryClient.invalidateQueries({ queryKey: ["setup-pending"] });
		} catch (e) {
			shared.fail((e as Error).message);
		}
	}
	const why = encodeURIComponent(shared.reason.trim());
	const userSelect = (value: string, onChange: (v: string) => void) => (
		<select
			className={select}
			value={value}
			onChange={(e) => onChange(e.target.value)}
		>
			<option value="">Choose a person</option>
			{people.map((p) => (
				<option key={p.user_id} value={p.user_id}>
					{p.name}
				</option>
			))}
		</select>
	);

	return (
		<>
			<Card className="mb-4">
				<Table>
					<thead>
						<tr>
							<Th>Person</Th>
							<Th>Roles</Th>
							<Th>Approval limits</Th>
							<Th>Reports to</Th>
							<Th>Login</Th>
						</tr>
					</thead>
					<tbody>
						{people.map((p) => (
							<tr key={p.user_id}>
								<Td>
									{p.name}
									<div className="text-xs text-muted-foreground">{p.email}</div>
									{p.person ? (
										<div className="text-xs text-muted-foreground">
											{p.person.designation}
										</div>
									) : null}
								</Td>
								<Td>
									{p.roles.map((r) => (
										<div key={r.id} className="flex items-center gap-2">
											<span>
												{r.role}
												{r.location ? ` (${r.location})` : ""}
											</span>
											<button
												type="button"
												className="text-xs text-danger underline"
												onClick={() =>
													act(
														`/api/setup/people/${p.user_id}/roles/${r.id}?reason=${why}`,
														{ method: "DELETE" },
													)
												}
											>
												remove
											</button>
										</div>
									))}
								</Td>
								<Td>
									{p.limits.map((l) => (
										<div key={l.id} className="flex items-center gap-2">
											<span>
												{l.document}: ₹{l.max_value}
											</span>
											<button
												type="button"
												className="text-xs text-danger underline"
												onClick={() =>
													act(
														`/api/setup/people/${p.user_id}/limits/${l.id}?reason=${why}`,
														{ method: "DELETE" },
													)
												}
											>
												remove
											</button>
										</div>
									))}
								</Td>
								<Td>
									{(data.data?.org ?? []).find(
										(o) => o.code === p.person?.manager_code,
									)?.name ??
										p.person?.manager_code ??
										"-"}
								</Td>
								<Td>
									<div className="flex items-center gap-2">
										<Badge tone={p.active ? "success" : "danger"}>
											{p.active ? "Active" : "Inactive"}
										</Badge>
										<button
											type="button"
											className="text-xs underline"
											onClick={() =>
												act(`/api/setup/people/${p.user_id}/active`, {
													method: "POST",
													json: {
														active: !p.active,
														reason: shared.reason.trim(),
													},
												})
											}
										>
											{p.active ? "deactivate" : "activate"}
										</button>
									</div>
								</Td>
							</tr>
						))}
					</tbody>
				</Table>
			</Card>
			<div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-4">
				<Card>
					<CardHeader className="font-medium">Add a person</CardHeader>
					<CardBody className="space-y-3">
						<Field label="Email">
							<Input
								type="email"
								value={add.email}
								onChange={(e) => setAdd({ ...add, email: e.target.value })}
							/>
						</Field>
						<Field label="Name">
							<Input
								value={add.display_name}
								onChange={(e) =>
									setAdd({ ...add, display_name: e.target.value })
								}
							/>
						</Field>
						<Field label="Password (at least 10 characters)">
							<Input
								type="password"
								autoComplete="new-password"
								value={add.password}
								onChange={(e) => setAdd({ ...add, password: e.target.value })}
							/>
						</Field>
						<Field label="Role">
							<select
								className={select}
								value={add.role}
								onChange={(e) => setAdd({ ...add, role: e.target.value })}
							>
								{(data.data?.roles ?? []).map((r) => (
									<option key={r}>{r}</option>
								))}
							</select>
						</Field>
						<Button onClick={() => addPerson.mutate()}>Add person</Button>
					</CardBody>
				</Card>
				<Card>
					<CardHeader className="font-medium">Give another role</CardHeader>
					<CardBody className="space-y-3">
						<Field label="Person">
							{userSelect(grant.user_id, (v) =>
								setGrant({ ...grant, user_id: v }),
							)}
						</Field>
						<Field label="Role">
							<select
								className={select}
								value={grant.role}
								onChange={(e) => setGrant({ ...grant, role: e.target.value })}
							>
								{(data.data?.roles ?? []).map((r) => (
									<option key={r}>{r}</option>
								))}
							</select>
						</Field>
						<Field label="Only at location (optional)">
							<Input
								value={grant.location}
								onChange={(e) =>
									setGrant({ ...grant, location: e.target.value })
								}
							/>
						</Field>
						<Button onClick={() => giveRole.mutate()}>Give role</Button>
					</CardBody>
				</Card>
				<Card>
					<CardHeader className="font-medium">Set an approval limit</CardHeader>
					<CardBody className="space-y-3">
						<Field label="Person">
							{userSelect(limit.user_id, (v) =>
								setLimit({ ...limit, user_id: v }),
							)}
						</Field>
						<Field label="Document">
							<select
								className={select}
								value={limit.document}
								onChange={(e) =>
									setLimit({ ...limit, document: e.target.value })
								}
							>
								{(data.data?.documents ?? []).map((d) => (
									<option key={d}>{d}</option>
								))}
							</select>
						</Field>
						<Field label="Up to (₹)">
							<Input
								inputMode="decimal"
								value={limit.max_value}
								onChange={(e) =>
									setLimit({ ...limit, max_value: e.target.value })
								}
							/>
						</Field>
						<Button onClick={() => setLimitMutation.mutate()}>Set limit</Button>
					</CardBody>
				</Card>
				<Card>
					<CardHeader className="font-medium">Who they report to</CardHeader>
					<CardBody className="space-y-3">
						<Field label="Person">
							{userSelect(manager.user_id, (v) =>
								setManager({ ...manager, user_id: v }),
							)}
						</Field>
						<Field label="Manager">
							<select
								className={select}
								value={manager.manager_code}
								onChange={(e) =>
									setManager({ ...manager, manager_code: e.target.value })
								}
							>
								<option value="">Nobody</option>
								{(data.data?.org ?? []).map((o) => (
									<option key={o.code} value={o.code}>
										{o.name} ({o.designation})
									</option>
								))}
							</select>
						</Field>
						<Button onClick={() => setManagerMutation.mutate()}>
							Set manager
						</Button>
					</CardBody>
				</Card>
			</div>
		</>
	);
}

const GROUPS: [string, string][] = [
	["request", "Requests"],
	["rfq", "Quotations"],
	["po", "Purchase orders"],
	["supplier", "Suppliers"],
	["grn", "Goods"],
	["stock", "Stock"],
	["replenishment", "Replenishment"],
	["invoice", "Invoices"],
	["payment", "Payments"],
	["budget", "Budgets"],
	["admin", "Setup"],
	["audit", "Audit"],
];

function RolesTab(shared: Shared) {
	const data = useQuery({
		queryKey: ["setup-roles"],
		queryFn: () => api<Roles>("/api/setup/roles"),
	});
	const [drafts, setDrafts] = useState<Record<string, string[]>>({});
	if (!data.data) return null;
	const perms = data.data.permissions;

	async function saveRole(role: string) {
		if (!shared.reason.trim()) {
			shared.fail("Write the reason for the change at the top first.");
			return;
		}
		try {
			await api(`/api/setup/roles/${role}`, {
				method: "PUT",
				json: { permissions: drafts[role], reason: shared.reason.trim() },
			});
			shared.fail("");
			setDrafts({ ...drafts, [role]: undefined as unknown as string[] });
			await data.refetch();
		} catch (e) {
			shared.fail((e as Error).message);
		}
	}

	return (
		<div className="grid gap-4 lg:grid-cols-2">
			{data.data.roles.map((r: RoleRow) => {
				const current = drafts[r.role] ?? r.permissions;
				const isAdmin = r.role === "Admin";
				const changed = drafts[r.role] !== undefined;
				return (
					<Card key={r.role}>
						<CardHeader className="flex items-center justify-between font-medium">
							<span>{r.role}</span>
							{isAdmin ? <Badge tone="info">setup role only</Badge> : null}
						</CardHeader>
						<CardBody className="space-y-3">
							{GROUPS.map(([prefix, label]) => {
								const items = perms.filter((p) => p.action.startsWith(prefix));
								if (!items.length) return null;
								return (
									<fieldset key={prefix}>
										<legend className="mb-1 text-xs font-medium text-muted-foreground">
											{label}
										</legend>
										{items.map((p) => {
											const blocked = isAdmin
												? !p.admin_only && !p.action.startsWith("audit.")
												: p.admin_only;
											return (
												<label
													key={p.action}
													className="flex items-center gap-2 py-0.5 text-sm"
												>
													<input
														type="checkbox"
														disabled={blocked}
														checked={current.includes(p.action)}
														onChange={(e) =>
															setDrafts({
																...drafts,
																[r.role]: e.target.checked
																	? [...current, p.action]
																	: current.filter((a) => a !== p.action),
															})
														}
													/>
													<span>{p.label}</span>
												</label>
											);
										})}
									</fieldset>
								);
							})}
							<Button disabled={!changed} onClick={() => saveRole(r.role)}>
								Save {r.role}
							</Button>
						</CardBody>
					</Card>
				);
			})}
		</div>
	);
}
