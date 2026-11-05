import {
	DecisionGraph,
	type DecisionGraphType,
	JdmConfigProvider,
} from "@gorules/jdm-editor";
import "@gorules/jdm-editor/dist/style.css";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, PageTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { api } from "../lib/api";
import { formatDateTime } from "../lib/format";

type Version = {
	version: number;
	status: string;
	jdm: DecisionGraphType;
	note: string | null;
	created_by: string;
	published_at: string | null;
};

/** Tables written by code, not by this editor, may have no node positions; lay them out left to right. */
function withPositions(graph: DecisionGraphType): DecisionGraphType {
	return {
		...graph,
		nodes: graph.nodes.map((node, i) => {
			const position = (node as { position?: { x: number; y: number } })
				.position;
			return position ? node : { ...node, position: { x: i * 280, y: 80 } };
		}),
		edges: graph.edges.map((edge, i) => ({
			...edge,
			id: edge.id ?? `edge-${i}`,
			type: edge.type ?? "edge",
		})),
	};
}

export default function RuleEditorPage({ ruleKey }: { ruleKey: string }) {
	const queryClient = useQueryClient();
	const versions = useQuery({
		queryKey: ["rule", ruleKey],
		queryFn: () => api<Version[]>(`/api/admin/rules/${ruleKey}`),
	});
	const [graph, setGraph] = useState<DecisionGraphType | null>(null);
	const [note, setNote] = useState("");
	const [message, setMessage] = useState("");
	const [error, setError] = useState("");

	// start from the draft if there is one, otherwise from the live version
	useEffect(() => {
		if (versions.data && graph === null) {
			const start =
				versions.data.find((v) => v.status === "draft") ??
				versions.data.find((v) => v.status === "published");
			if (start) setGraph(withPositions(start.jdm));
		}
	}, [versions.data, graph]);

	const refresh = () => queryClient.invalidateQueries({ queryKey: ["rule"] });
	const save = useMutation({
		mutationFn: () =>
			api<{ version: number }>(`/api/admin/rules/${ruleKey}/draft`, {
				method: "PUT",
				json: { jdm: graph, note: note || null },
			}),
		onSuccess: (r) => {
			setError("");
			setMessage(`Draft saved as version ${r.version}.`);
			refresh();
		},
		onError: (e: Error) => setError(e.message),
	});
	const publish = useMutation({
		mutationFn: () =>
			api<{ version: number }>(`/api/admin/rules/${ruleKey}/publish`, {
				method: "POST",
			}),
		onSuccess: (r) => {
			setError("");
			setMessage(`Version ${r.version} is now live.`);
			refresh();
		},
		onError: (e: Error) => setError(e.message),
	});

	return (
		<>
			<PageTitle sub="Edit the table as a draft, save it, then publish. Earlier decisions stay reproducible against the version they used.">
				Decision table: {ruleKey}
			</PageTitle>
			<div className="mb-3 flex flex-wrap items-center gap-2">
				{versions.data?.map((v) => (
					<span
						key={v.version}
						className="flex items-center gap-1 text-xs text-muted-foreground"
					>
						v{v.version} <Badge>{v.status}</Badge>
						{v.published_at ? formatDateTime(v.published_at) : ""}
					</span>
				))}
			</div>
			<Card className="mb-3">
				<CardBody className="flex flex-wrap items-center gap-2">
					<Input
						className="max-w-xs"
						placeholder="What changed? (optional)"
						value={note}
						onChange={(e) => setNote(e.target.value)}
					/>
					<Button
						disabled={!graph || save.isPending}
						onClick={() => save.mutate()}
					>
						Save draft
					</Button>
					<Button
						variant="outline"
						disabled={publish.isPending}
						onClick={() => publish.mutate()}
					>
						Publish draft
					</Button>
					{message ? (
						<span className="text-sm text-success">{message}</span>
					) : null}
					{error ? (
						<span role="alert" className="text-sm text-danger">
							{error}
						</span>
					) : null}
				</CardBody>
			</Card>
			<Card className="h-[70vh] overflow-hidden">
				{graph ? (
					<JdmConfigProvider>
						<DecisionGraph
							value={graph}
							onChange={(g) => setGraph(g as DecisionGraphType)}
						/>
					</JdmConfigProvider>
				) : (
					<CardBody className="text-sm text-muted-foreground">
						Loading…
					</CardBody>
				)}
			</Card>
		</>
	);
}
