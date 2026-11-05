import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader, PageTitle } from "../components/ui/card";
import { Field, Input } from "../components/ui/input";
import { Table, Td, Th } from "../components/ui/table";
import { api } from "../lib/api";

type UserRow = {
	user_id: number;
	display_name: string;
	primary_role: string;
	roles: { role: string; location: string | null; department: string | null }[];
};

const ROLES = [
	"Requester",
	"Approver",
	"Buyer",
	"Store",
	"AP",
	"Finance",
	"Admin",
];
const select =
	"w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm min-h-9";

export function UsersPage() {
	const queryClient = useQueryClient();
	const users = useQuery({
		queryKey: ["users"],
		queryFn: () => api<UserRow[]>("/api/admin/users"),
	});
	const refresh = () => queryClient.invalidateQueries({ queryKey: ["users"] });
	const [error, setError] = useState("");
	const [form, setForm] = useState({
		email: "",
		password: "",
		display_name: "",
		role: "Requester",
	});
	const [roleFor, setRoleFor] = useState({
		user_id: "",
		role: "Store",
		location: "",
	});
	const [limitFor, setLimitFor] = useState({
		user_id: "",
		document: "po",
		max_value: "",
	});

	const create = useMutation({
		mutationFn: () => api("/api/admin/users", { method: "POST", json: form }),
		onSuccess: () => {
			setForm({ email: "", password: "", display_name: "", role: "Requester" });
			refresh();
		},
		onError: (e: Error) => setError(e.message),
	});
	const grant = useMutation({
		mutationFn: () =>
			api(`/api/admin/users/${roleFor.user_id}/roles`, {
				method: "POST",
				json: { role: roleFor.role, location: roleFor.location || null },
			}),
		onSuccess: refresh,
		onError: (e: Error) => setError(e.message),
	});
	const limit = useMutation({
		mutationFn: () =>
			api(`/api/admin/users/${limitFor.user_id}/limits`, {
				method: "POST",
				json: { document: limitFor.document, max_value: limitFor.max_value },
			}),
		onSuccess: refresh,
		onError: (e: Error) => setError(e.message),
	});

	function run(e: FormEvent, mutation: { mutate: () => void }) {
		e.preventDefault();
		setError("");
		mutation.mutate();
	}

	return (
		<>
			<PageTitle sub="People, their roles (optionally for one location) and approval limits.">
				Users and roles
			</PageTitle>
			{error ? (
				<p role="alert" className="mb-3 text-sm text-danger">
					{error}
				</p>
			) : null}
			<Card className="mb-4">
				<Table>
					<thead>
						<tr>
							<Th>Id</Th>
							<Th>Name</Th>
							<Th>Roles</Th>
						</tr>
					</thead>
					<tbody>
						{users.data?.map((u) => (
							<tr key={u.user_id}>
								<Td>{u.user_id}</Td>
								<Td>{u.display_name}</Td>
								<Td>
									{u.roles
										.map((r) =>
											r.location ? `${r.role} (${r.location})` : r.role,
										)
										.join(", ")}
								</Td>
							</tr>
						))}
					</tbody>
				</Table>
			</Card>
			<div className="grid gap-4 lg:grid-cols-3">
				<Card>
					<CardHeader className="font-medium">Add a user</CardHeader>
					<CardBody>
						<form onSubmit={(e) => run(e, create)} className="space-y-3">
							<Field label="Email">
								<Input
									type="email"
									required
									value={form.email}
									onChange={(e) => setForm({ ...form, email: e.target.value })}
								/>
							</Field>
							<Field label="Name">
								<Input
									required
									value={form.display_name}
									onChange={(e) =>
										setForm({ ...form, display_name: e.target.value })
									}
								/>
							</Field>
							<Field label="Password (at least 10 characters)">
								<Input
									type="password"
									required
									minLength={10}
									autoComplete="new-password"
									value={form.password}
									onChange={(e) =>
										setForm({ ...form, password: e.target.value })
									}
								/>
							</Field>
							<Field label="Role">
								<select
									className={select}
									value={form.role}
									onChange={(e) => setForm({ ...form, role: e.target.value })}
								>
									{ROLES.map((r) => (
										<option key={r}>{r}</option>
									))}
								</select>
							</Field>
							<Button type="submit">Add user</Button>
						</form>
					</CardBody>
				</Card>
				<Card>
					<CardHeader className="font-medium">Give another role</CardHeader>
					<CardBody>
						<form onSubmit={(e) => run(e, grant)} className="space-y-3">
							<Field label="User id">
								<Input
									required
									inputMode="numeric"
									value={roleFor.user_id}
									onChange={(e) =>
										setRoleFor({ ...roleFor, user_id: e.target.value })
									}
								/>
							</Field>
							<Field label="Role">
								<select
									className={select}
									value={roleFor.role}
									onChange={(e) =>
										setRoleFor({ ...roleFor, role: e.target.value })
									}
								>
									{ROLES.map((r) => (
										<option key={r}>{r}</option>
									))}
								</select>
							</Field>
							<Field label="Only at location (optional)">
								<Input
									value={roleFor.location}
									onChange={(e) =>
										setRoleFor({ ...roleFor, location: e.target.value })
									}
									placeholder="PLT-VAD"
								/>
							</Field>
							<Button type="submit">Give role</Button>
						</form>
					</CardBody>
				</Card>
				<Card>
					<CardHeader className="font-medium">Set an approval limit</CardHeader>
					<CardBody>
						<form onSubmit={(e) => run(e, limit)} className="space-y-3">
							<Field label="User id">
								<Input
									required
									inputMode="numeric"
									value={limitFor.user_id}
									onChange={(e) =>
										setLimitFor({ ...limitFor, user_id: e.target.value })
									}
								/>
							</Field>
							<Field label="Document">
								<select
									className={select}
									value={limitFor.document}
									onChange={(e) =>
										setLimitFor({ ...limitFor, document: e.target.value })
									}
								>
									{["request", "po", "invoice", "payment_run"].map((d) => (
										<option key={d}>{d}</option>
									))}
								</select>
							</Field>
							<Field label="Up to (₹)">
								<Input
									required
									inputMode="decimal"
									value={limitFor.max_value}
									onChange={(e) =>
										setLimitFor({ ...limitFor, max_value: e.target.value })
									}
									placeholder="500000"
								/>
							</Field>
							<Button type="submit">Set limit</Button>
						</form>
					</CardBody>
				</Card>
			</div>
		</>
	);
}
