import { useQuery } from "@tanstack/react-query";
import { type FormEvent, Fragment, useState } from "react";
import { Button } from "../components/ui/button";
import { Card, CardBody, PageTitle } from "../components/ui/card";
import { Field, Input } from "../components/ui/input";
import { Table, Td, Th } from "../components/ui/table";
import { api } from "../lib/api";
import { formatDateTime } from "../lib/format";

type AuditRow = {
	id: number;
	at: string;
	actor_type: string;
	actor_id: string;
	action: string;
	entity: string;
	entity_id: string;
	before: unknown;
	after: unknown;
	reason: string | null;
	rule_version: string | null;
	model: string | null;
	prompt_version: string | null;
};

const EMPTY = { entity: "", entity_id: "", action: "", actor_id: "" };

export function AuditPage() {
	const [form, setForm] = useState(EMPTY);
	const [filters, setFilters] = useState(EMPTY);
	const [open, setOpen] = useState<number | null>(null);
	const query = new URLSearchParams(
		Object.entries(filters).filter(([, v]) => v),
	).toString();
	const { data, isLoading } = useQuery({
		queryKey: ["audit", query],
		queryFn: () => api<AuditRow[]>(`/api/admin/audit?${query}`),
	});

	function submit(e: FormEvent) {
		e.preventDefault();
		setFilters(form);
	}

	return (
		<>
			<PageTitle sub="Every change, who made it, and why. The log cannot be edited.">
				Audit
			</PageTitle>
			<Card className="mb-4">
				<CardBody>
					<form
						onSubmit={submit}
						className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5"
					>
						<Field label="Entity">
							<Input
								value={form.entity}
								onChange={(e) => setForm({ ...form, entity: e.target.value })}
								placeholder="user, proposal, rule_table"
							/>
						</Field>
						<Field label="Entity id">
							<Input
								value={form.entity_id}
								onChange={(e) =>
									setForm({ ...form, entity_id: e.target.value })
								}
							/>
						</Field>
						<Field label="Action">
							<Input
								value={form.action}
								onChange={(e) => setForm({ ...form, action: e.target.value })}
								placeholder="rule.publish"
							/>
						</Field>
						<Field label="Actor id">
							<Input
								value={form.actor_id}
								onChange={(e) => setForm({ ...form, actor_id: e.target.value })}
							/>
						</Field>
						<div className="flex items-end gap-2">
							<Button type="submit">Search</Button>
							<Button
								variant="outline"
								onClick={() => {
									setForm(EMPTY);
									setFilters(EMPTY);
								}}
							>
								Clear
							</Button>
						</div>
					</form>
				</CardBody>
			</Card>
			<Card>
				{isLoading ? <CardBody>Loading…</CardBody> : null}
				<Table>
					<thead>
						<tr>
							<Th>When</Th>
							<Th>Who</Th>
							<Th>Action</Th>
							<Th>On</Th>
							<Th>Why</Th>
							<Th />
						</tr>
					</thead>
					<tbody>
						{data?.map((r) => (
							<Fragment key={r.id}>
								<tr>
									<Td className="whitespace-nowrap">{formatDateTime(r.at)}</Td>
									<Td className="whitespace-nowrap">
										{r.actor_type} {r.actor_id}
									</Td>
									<Td>{r.action}</Td>
									<Td>
										{r.entity} {r.entity_id}
									</Td>
									<Td>{r.reason}</Td>
									<Td>
										<Button
											variant="ghost"
											aria-expanded={open === r.id}
											onClick={() => setOpen(open === r.id ? null : r.id)}
										>
											{open === r.id ? "Hide" : "Details"}
										</Button>
									</Td>
								</tr>
								{open === r.id ? (
									<tr>
										<Td colSpan={6}>
											<pre className="max-w-full overflow-x-auto rounded-md bg-muted p-2 text-xs">
												{JSON.stringify(
													{
														before: r.before,
														after: r.after,
														rule_version: r.rule_version,
														model: r.model,
														prompt_version: r.prompt_version,
													},
													null,
													2,
												)}
											</pre>
										</Td>
									</tr>
								) : null}
							</Fragment>
						))}
					</tbody>
				</Table>
				{data?.length === 0 ? (
					<CardBody className="text-sm text-muted-foreground">
						No matching entries.
					</CardBody>
				) : null}
			</Card>
		</>
	);
}
