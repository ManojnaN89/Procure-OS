import { cn } from "../../lib/utils";

const tones: Record<string, string> = {
	neutral: "bg-muted text-muted-foreground",
	success: "bg-success/10 text-success",
	danger: "bg-danger/10 text-danger",
	warning: "bg-warning/10 text-warning",
	info: "bg-primary/10 text-primary",
};

const byStatus: Record<string, string> = {
	done: "success",
	ok: "success",
	approved: "success",
	published: "success",
	edited: "info",
	running: "info",
	waiting: "warning",
	pending: "warning",
	draft: "warning",
	failed: "danger",
	error: "danger",
	rejected: "danger",
	refusal: "danger",
	invalid: "danger",
	high: "danger",
	medium: "warning",
	low: "neutral",
};

export function Badge({
	children,
	tone,
}: {
	children: string;
	tone?: keyof typeof tones;
}) {
	const key = tone ?? byStatus[children] ?? "neutral";
	return (
		<span
			className={cn(
				"inline-block rounded-full px-2 py-0.5 text-xs font-medium",
				tones[key],
			)}
		>
			{children}
		</span>
	);
}
