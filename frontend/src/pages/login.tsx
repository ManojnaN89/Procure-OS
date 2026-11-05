import { useNavigate } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { Button } from "../components/ui/button";
import { Card, CardBody } from "../components/ui/card";
import { Field, Input } from "../components/ui/input";
import { ApiError, login } from "../lib/api";

export function LoginPage() {
	const navigate = useNavigate();
	const [email, setEmail] = useState("");
	const [password, setPassword] = useState("");
	const [error, setError] = useState("");
	const [busy, setBusy] = useState(false);

	async function submit(e: FormEvent) {
		e.preventDefault();
		setBusy(true);
		setError("");
		try {
			await login(email, password);
			await navigate({ to: "/" });
		} catch (err) {
			setError(
				err instanceof ApiError && err.status === 423
					? "Too many wrong passwords. Try again in 15 minutes."
					: "Wrong email or password.",
			);
		} finally {
			setBusy(false);
		}
	}

	return (
		<div className="flex min-h-screen items-center justify-center p-4">
			<Card className="w-full max-w-sm">
				<CardBody>
					<h1 className="mb-1 text-xl font-semibold">P2P platform</h1>
					<p className="mb-4 text-sm text-muted-foreground">
						Sign in to your company space.
					</p>
					<form onSubmit={submit} className="space-y-3">
						<Field label="Email">
							<Input
								type="email"
								autoComplete="username"
								required
								value={email}
								onChange={(e) => setEmail(e.target.value)}
							/>
						</Field>
						<Field label="Password">
							<Input
								type="password"
								autoComplete="current-password"
								required
								value={password}
								onChange={(e) => setPassword(e.target.value)}
							/>
						</Field>
						{error ? (
							<p role="alert" className="text-sm text-danger">
								{error}
							</p>
						) : null}
						<Button type="submit" className="w-full" disabled={busy}>
							{busy ? "Signing in…" : "Sign in"}
						</Button>
					</form>
				</CardBody>
			</Card>
		</div>
	);
}
