import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Badge } from "../components/ui/badge";
import { Card, CardBody, PageTitle } from "../components/ui/card";
import { Table, Td, Th } from "../components/ui/table";
import { api } from "../lib/api";
import { formatDateTime, formatNumber } from "../lib/format";

type Run = {
	id: number;
	thread_id: string;
	agent: string;
	status: string;
	input: string | null;
	result: string | null;
	waiting_for: string | null;
	started_at: string;
	finished_at: string | null;
};

type Step = {
	id: number;
	at: string;
	kind: string;
	name: string | null;
	detail: unknown;
};

export function RunsPage() {
	const { data, isLoading } = useQuery({
		queryKey: ["runs"],
		queryFn: () => api<Run[]>("/api/admin/agent-runs"),
	});
	return (
		<>
			<PageTitle sub="Each agent run with its steps. Open one to follow it from start to finish.">
				Agent runs
			</PageTitle>
			<Card>
				{isLoading ? <CardBody>Loading…</CardBody> : null}
				<Table>
					<thead>
						<tr>
							<Th>Started</Th>
							<Th>Agent</Th>
							<Th>Status</Th>
							<Th>Task</Th>
							<Th>Waiting for</Th>
							<Th />
						</tr>
					</thead>
					<tbody>
						{data?.map((r) => (
							<tr key={r.id}>
								<Td className="whitespace-nowrap">
									{formatDateTime(r.started_at)}
								</Td>
								<Td>{r.agent}</Td>
								<Td>
									<Badge>{r.status}</Badge>
								</Td>
								<Td>{r.input}</Td>
								<Td>{r.waiting_for}</Td>
								<Td>
									<Link
										to="/admin/runs/$runId"
										params={{ runId: String(r.id) }}
										className="text-primary underline"
									>
										Open
									</Link>
								</Td>
							</tr>
						))}
					</tbody>
				</Table>
				{data?.length === 0 ? (
					<CardBody className="text-sm text-muted-foreground">
						No agent runs yet.
					</CardBody>
				) : null}
			</Card>
		</>
	);
}

export function RunDetailPage({ runId }: { runId: string }) {
	const { data } = useQuery({
		queryKey: ["run", runId],
		queryFn: () =>
			api<{ run: Run; steps: Step[] }>(`/api/admin/agent-runs/${runId}`),
	});
	return (
		<>
			<PageTitle sub={data ? `${data.run.agent} · ${data.run.thread_id}` : ""}>
				Run {runId}
			</PageTitle>
			{data ? (
				<div className="max-w-3xl space-y-3">
					<Card>
						<CardBody className="space-y-1 text-sm">
							<div>
								<Badge>{data.run.status}</Badge>
							</div>
							<div>
								<span className="text-muted-foreground">Task: </span>
								{data.run.input}
							</div>
							{data.run.result ? (
								<div>
									<span className="text-muted-foreground">Result: </span>
									{data.run.result}
								</div>
							) : null}
						</CardBody>
					</Card>
					<ol className="space-y-2">
						{data.steps.map((s, i) => (
							<li key={s.id}>
								<Card>
									<CardBody className="space-y-1">
										<div className="flex flex-wrap items-center gap-2 text-sm">
											<span className="text-muted-foreground">{i + 1}.</span>
											<Badge
												tone={
													s.kind === "error"
														? "danger"
														: s.kind === "wait"
															? "warning"
															: "info"
												}
											>
												{s.kind}
											</Badge>
											<span className="font-medium">{s.name}</span>
											<span className="text-xs text-muted-foreground">
												{formatDateTime(s.at)}
											</span>
										</div>
										<pre className="max-w-full overflow-x-auto rounded-md bg-muted p-2 text-xs">
											{JSON.stringify(s.detail, null, 2)}
										</pre>
									</CardBody>
								</Card>
							</li>
						))}
					</ol>
				</div>
			) : null}
		</>
	);
}

type Calls = {
	calls: {
		id: number;
		at: string;
		step: string;
		provider: string | null;
		model: string | null;
		prompt_version: string | null;
		tokens_in: number;
		tokens_out: number;
		cost_usd: string;
		ms: number;
		attempts: number;
		outcome: string;
		error: string | null;
	}[];
	totals: {
		calls: number;
		tokens_in: number;
		tokens_out: number;
		cost_usd: string;
	};
};

export function ModelCallsPage() {
	const { data } = useQuery({
		queryKey: ["model-calls"],
		queryFn: () => api<Calls>("/api/admin/model-calls"),
	});
	return (
		<>
			<PageTitle sub="Every call to an AI model: what it cost, how long it took and how it ended.">
				Model calls
			</PageTitle>
			{data ? (
				<div className="mb-4 grid gap-3 sm:grid-cols-3">
					<Card>
						<CardBody>
							<div className="text-2xl font-semibold">
								{formatNumber(data.totals.calls)}
							</div>
							<div className="text-sm text-muted-foreground">calls</div>
						</CardBody>
					</Card>
					<Card>
						<CardBody>
							<div className="text-2xl font-semibold">
								{formatNumber(data.totals.tokens_in + data.totals.tokens_out)}
							</div>
							<div className="text-sm text-muted-foreground">tokens</div>
						</CardBody>
					</Card>
					<Card>
						<CardBody>
							<div className="text-2xl font-semibold">
								$ {Number(data.totals.cost_usd).toFixed(4)}
							</div>
							<div className="text-sm text-muted-foreground">cost</div>
						</CardBody>
					</Card>
				</div>
			) : null}
			<Card>
				<Table>
					<thead>
						<tr>
							<Th>When</Th>
							<Th>Step</Th>
							<Th>Model</Th>
							<Th>Tokens in/out</Th>
							<Th>Cost $</Th>
							<Th>Time</Th>
							<Th>Outcome</Th>
						</tr>
					</thead>
					<tbody>
						{data?.calls.map((c) => (
							<tr key={c.id}>
								<Td className="whitespace-nowrap">{formatDateTime(c.at)}</Td>
								<Td>{c.step}</Td>
								<Td>
									{c.provider}/{c.model}
								</Td>
								<Td>
									{c.tokens_in}/{c.tokens_out}
								</Td>
								<Td>{Number(c.cost_usd).toFixed(4)}</Td>
								<Td>{c.ms} ms</Td>
								<Td>
									<Badge>{c.outcome}</Badge>
								</Td>
							</tr>
						))}
					</tbody>
				</Table>
			</Card>
		</>
	);
}
