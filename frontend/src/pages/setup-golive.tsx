import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Table, Td, Th } from "../components/ui/table";
import { api, download } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { can, useMe } from "../lib/me";
import { Section, type Shared, useChange } from "./setup-shared";

// Go-live (block 0d): the company's state and its history, the readiness checks, loading the open documents of the old
// system during cutover, and proposing go-live (another administrator approves it).

type StateData = {
	state: string;
	since: string;
	before_suspension: string | null;
	history: {
		from: string;
		to: string;
		at: string;
		by: string;
		approved_by: string | null;
		kind: string | null;
		reason: string;
	}[];
};
type Check = {
	code: string;
	group: string;
	title: string;
	severity: string;
	ok: boolean;
	problems: string[];
};
type Readiness = {
	ready: boolean;
	blockers: number;
	warnings: number;
	checks: Check[];
};
type ImportReport = {
	dry_run: boolean;
	loaded: Record<string, number>;
	already: Record<string, number>;
	rejected: { file: string; line: number; reason: string }[];
	warnings: string[];
	control: Record<
		string,
		{
			file_count: number;
			file_total: string;
			loaded_count: number;
			loaded_total: string;
			ok: boolean;
		}
	>;
	control_ok: boolean;
};

const TEMPLATES = [
	"po",
	"po_line",
	"goods_receipt",
	"gr_line",
	"service_entry",
	"invoice",
	"invoice_line",
	"advance",
	"supplier_ytd",
	"control",
];

export function GoLive(shared: Shared) {
	const me = useMe();
	const state = useQuery({
		queryKey: ["company-state"],
		queryFn: () => api<StateData>("/api/company/state"),
	});
	const readiness = useQuery({
		queryKey: ["company-readiness"],
		queryFn: () => api<Readiness>("/api/company/readiness"),
	});
	const goLive = useChange(shared, "company-state", (reason) =>
		api("/api/company/go-live", { method: "POST", json: { reason } }),
	);
	const current = state.data?.state;
	return (
		<>
			<Section title="Company state">
				{state.data ? (
					<>
						<p className="mb-3 text-sm">
							<Badge
								tone={
									current === "live"
										? "success"
										: current === "suspended"
											? "danger"
											: "warning"
								}
							>
								{current ?? ""}
							</Badge>{" "}
							since {formatDateTime(state.data.since)}
						</p>
						<Table>
							<thead>
								<tr>
									<Th>When</Th>
									<Th>Move</Th>
									<Th>By</Th>
									<Th>Reason</Th>
								</tr>
							</thead>
							<tbody>
								{state.data.history.map((h) => (
									<tr key={h.at}>
										<Td>{formatDateTime(h.at)}</Td>
										<Td>
											{h.from} to {h.to}
											{h.kind ? ` (${h.kind})` : ""}
										</Td>
										<Td>
											{h.by}
											{h.approved_by ? `, approved by ${h.approved_by}` : ""}
										</Td>
										<Td>{h.reason}</Td>
									</tr>
								))}
							</tbody>
						</Table>
					</>
				) : (
					<p className="text-sm text-muted-foreground">Loading…</p>
				)}
			</Section>
			<Section title="Readiness">
				<div className="mb-3 flex items-center gap-3 text-sm">
					{readiness.data ? (
						<span>
							{readiness.data.ready
								? "Ready."
								: `${readiness.data.blockers} ${readiness.data.blockers === 1 ? "blocker" : "blockers"} to clear.`}{" "}
							{readiness.data.warnings}{" "}
							{readiness.data.warnings === 1 ? "warning" : "warnings"}.
						</span>
					) : null}
					<Button variant="outline" onClick={() => readiness.refetch()}>
						Check again
					</Button>
				</div>
				<Table>
					<thead>
						<tr>
							<Th>Check</Th>
							<Th>Group</Th>
							<Th>Result</Th>
							<Th>Problems</Th>
						</tr>
					</thead>
					<tbody>
						{readiness.data?.checks.map((c) => (
							<tr key={`${c.code}-${c.severity}`}>
								<Td>{c.title}</Td>
								<Td>{c.group}</Td>
								<Td>
									<Badge
										tone={
											c.ok
												? "success"
												: c.severity === "blocker"
													? "danger"
													: "warning"
										}
									>
										{c.ok ? "ok" : c.severity}
									</Badge>
								</Td>
								<Td>
									{c.problems.map((p) => (
										<div key={p}>{p}</div>
									))}
								</Td>
							</tr>
						))}
					</tbody>
				</Table>
			</Section>
			{current === "cutover" && can(me.data, "company.cutover") ? (
				<CutoverImport />
			) : null}
			{current === "cutover" ? (
				<Section title="Go live">
					<p className="mb-3 text-sm text-muted-foreground">
						Another administrator approves going live, and readiness is checked
						again then. The reason at the top goes with it.
					</p>
					<Button
						disabled={!readiness.data?.ready || goLive.isPending}
						onClick={() => goLive.mutate()}
					>
						{readiness.data?.ready
							? "Propose going live"
							: `Not ready: ${readiness.data?.blockers ?? 0} blockers`}
					</Button>
				</Section>
			) : null}
		</>
	);
}

function CutoverImport() {
	const queryClient = useQueryClient();
	const [files, setFiles] = useState<FileList | null>(null);
	const [dryRun, setDryRun] = useState(true);
	const load = useMutation({
		mutationFn: () => {
			const form = new FormData();
			for (const f of Array.from(files ?? [])) form.append("files", f);
			form.append("dry_run", dryRun ? "true" : "false");
			return api<ImportReport>("/api/company/cutover/import", {
				method: "POST",
				body: form,
			});
		},
		onSuccess: () =>
			queryClient.invalidateQueries({ queryKey: ["company-readiness"] }),
	});
	const report = load.data;
	return (
		<Section title="Open documents of the old system">
			<p className="mb-2 text-sm text-muted-foreground">
				Templates:{" "}
				{TEMPLATES.map((t) => (
					<button
						key={t}
						type="button"
						className="mr-2 underline"
						onClick={() =>
							download(`/api/company/cutover/templates/${t}`, `${t}.csv`)
						}
					>
						{t}.csv
					</button>
				))}
			</p>
			<div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
				<input
					type="file"
					multiple
					accept=".csv,.xlsx"
					aria-label="Cutover files"
					onChange={(e) => setFiles(e.target.files)}
				/>
				<label className="flex items-center gap-2">
					<input
						type="checkbox"
						checked={dryRun}
						onChange={(e) => setDryRun(e.target.checked)}
					/>
					Dry run (check only)
				</label>
				<Button
					disabled={!files?.length || load.isPending}
					onClick={() => load.mutate()}
				>
					{dryRun ? "Check" : "Load"}
				</Button>
			</div>
			{load.error ? (
				<p role="alert" className="text-sm text-danger">
					{(load.error as Error).message}
				</p>
			) : null}
			{report ? (
				<div className="space-y-2 text-sm">
					<p>
						{report.dry_run ? "Would load" : "Loaded"}:{" "}
						{Object.entries(report.loaded)
							.map(([k, n]) => `${k} ${n}`)
							.join(", ")}
						. Loaded before:{" "}
						{Object.entries(report.already)
							.map(([k, n]) => `${k} ${n}`)
							.join(", ")}
						.
					</p>
					<Table>
						<thead>
							<tr>
								<Th>Control</Th>
								<Th>Old system</Th>
								<Th>Company</Th>
								<Th>Result</Th>
							</tr>
						</thead>
						<tbody>
							{Object.entries(report.control).map(([kind, c]) => (
								<tr key={kind}>
									<Td>{kind}</Td>
									<Td>
										{c.file_count} / {c.file_total}
									</Td>
									<Td>
										{c.loaded_count} / {c.loaded_total}
									</Td>
									<Td>
										<Badge tone={c.ok ? "success" : "danger"}>
											{c.ok ? "ok" : "differs"}
										</Badge>
									</Td>
								</tr>
							))}
						</tbody>
					</Table>
					{report.rejected.map((r) => (
						<div key={`${r.file}-${r.line}-${r.reason}`}>
							Left out: {r.file} line {r.line}: {r.reason}
						</div>
					))}
					{report.warnings.map((w) => (
						<div key={w} className="text-muted-foreground">
							{w}
						</div>
					))}
				</div>
			) : null}
		</Section>
	);
}
