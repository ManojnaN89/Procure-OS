import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, PageTitle } from "../components/ui/card";
import { Field, Input } from "../components/ui/input";
import { Table, Td, Th } from "../components/ui/table";
import { api } from "../lib/api";
import { formatDateTime } from "../lib/format";

type Evaluation = {
	id: number;
	at: string;
	key: string;
	version: number;
	input: unknown;
	output: unknown;
	actor: string;
	case_ref: string | null;
};

/** A decision's inputs and outputs in words ("key: value; ..."), not raw JSON. */
function plain(value: unknown): string {
	if (value === null || value === undefined) return "-";
	if (Array.isArray(value)) return value.map(plain).join(", ") || "none";
	if (typeof value === "object")
		return Object.entries(value as Record<string, unknown>)
			.map(([k, v]) => `${k.replaceAll("_", " ")}: ${plain(v)}`)
			.join("; ");
	return String(value);
}

function Trace({ id }: { id: number }) {
	const { data } = useQuery({
		queryKey: ["rule-evaluation", id],
		queryFn: () => api<{ trace: unknown }>(`/api/admin/rule-evaluations/${id}`),
	});
	return (
		<pre className="max-w-full overflow-x-auto rounded-md bg-muted p-2 text-xs">
			{data ? plain(data.trace) : "Loading…"}
		</pre>
	);
}

export function DecisionsPage() {
	const [key, setKey] = useState("");
	const [open, setOpen] = useState<number | null>(null);
	const query = key ? `?key=${encodeURIComponent(key)}` : "";
	const { data, isLoading } = useQuery({
		queryKey: ["rule-evaluations", key],
		queryFn: () => api<Evaluation[]>(`/api/admin/rule-evaluations${query}`),
	});

	return (
		<>
			<PageTitle sub="Every decision a rule table made: what went in, what came out, and which version decided it.">
				Rule decisions
			</PageTitle>
			<Card className="mb-4">
				<CardBody className="flex flex-wrap items-end gap-2">
					<Field label="Rule table">
						<Input
							value={key}
							onChange={(e) => setKey(e.target.value)}
							placeholder="approval"
						/>
					</Field>
				</CardBody>
			</Card>
			<Card>
				{isLoading ? <CardBody>Loading…</CardBody> : null}
				<Table>
					<thead>
						<tr>
							<Th>When</Th>
							<Th>Table</Th>
							<Th>Input</Th>
							<Th>Result</Th>
							<Th>Asked by</Th>
							<Th>Case</Th>
							<Th />
						</tr>
					</thead>
					<tbody>
						{data?.map((e) => (
							<Fragment key={e.id}>
								<tr>
									<Td className="whitespace-nowrap">{formatDateTime(e.at)}</Td>
									<Td className="whitespace-nowrap">
										{e.key} <Badge tone="info">{`v${e.version}`}</Badge>
									</Td>
									<Td className="font-mono text-xs">{plain(e.input)}</Td>
									<Td className="font-mono text-xs">{plain(e.output)}</Td>
									<Td>{e.actor}</Td>
									<Td>{e.case_ref}</Td>
									<Td>
										<Button
											variant="ghost"
											aria-expanded={open === e.id}
											onClick={() => setOpen(open === e.id ? null : e.id)}
										>
											{open === e.id ? "Hide" : "Why"}
										</Button>
									</Td>
								</tr>
								{open === e.id ? (
									<tr>
										<Td colSpan={7}>
											<Trace id={e.id} />
										</Td>
									</tr>
								) : null}
							</Fragment>
						))}
					</tbody>
				</Table>
				{data?.length === 0 ? (
					<CardBody className="text-sm text-muted-foreground">
						No decisions yet.
					</CardBody>
				) : null}
			</Card>
		</>
	);
}
