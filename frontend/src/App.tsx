import { useEffect, useState } from "react";

type Health = { status: string; database: string };

export default function App() {
	const [health, setHealth] = useState<Health | null>(null);
	const [failed, setFailed] = useState(false);

	useEffect(() => {
		fetch("/api/health")
			.then((r) => r.json() as Promise<Health>)
			.then(setHealth)
			.catch(() => setFailed(true));
	}, []);

	return (
		<main>
			<h1>P2P platform</h1>
			<p>
				Backend:{" "}
				{failed
					? "not reachable"
					: health
						? `${health.status}, database ${health.database}`
						: "checking"}
			</p>
		</main>
	);
}
