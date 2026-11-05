import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Badge } from "../components/ui/badge";
import { Card, CardBody, PageTitle } from "../components/ui/card";
import { Table, Td, Th } from "../components/ui/table";
import { api } from "../lib/api";

type RuleRow = {
	key: string;
	published_version: number | null;
	draft_version: number | null;
};

export function RulesPage() {
	const { data, isLoading } = useQuery({
		queryKey: ["rule", "list"],
		queryFn: () => api<RuleRow[]>("/api/admin/rules"),
	});
	return (
		<>
			<PageTitle sub="Business rules kept as tables you can edit, such as who approves what. Changes are versioned.">
				Rules
			</PageTitle>
			<Card>
				{isLoading ? <CardBody>Loading…</CardBody> : null}
				<Table>
					<thead>
						<tr>
							<Th>Table</Th>
							<Th>Live version</Th>
							<Th>Draft</Th>
							<Th />
						</tr>
					</thead>
					<tbody>
						{data?.map((r) => (
							<tr key={r.key}>
								<Td>{r.key}</Td>
								<Td>
									{r.published_version ? (
										<Badge tone="success">{`v${r.published_version}`}</Badge>
									) : (
										"none"
									)}
								</Td>
								<Td>
									{r.draft_version ? (
										<Badge tone="warning">{`v${r.draft_version}`}</Badge>
									) : (
										""
									)}
								</Td>
								<Td>
									<Link
										to="/admin/rules/$key"
										params={{ key: r.key }}
										className="text-primary underline"
									>
										Open editor
									</Link>
								</Td>
							</tr>
						))}
					</tbody>
				</Table>
				{data?.length === 0 ? (
					<CardBody className="text-sm text-muted-foreground">
						No decision tables yet.
					</CardBody>
				) : null}
			</Card>
		</>
	);
}
