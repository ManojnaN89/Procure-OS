import { cn } from "../../lib/utils";

const tones: Record<string, string> = {
	neutral: "bg-[#eef1f5] text-[#3b4554]",
	success: "bg-[#e5f4eb] text-success",
	danger: "bg-[#fdecec] text-danger",
	warning: "bg-[#fff4de] text-warning",
	info: "bg-[#e8effd] text-[#1c45ab]",
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
				"inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold whitespace-nowrap before:h-1.5 before:w-1.5 before:rounded-full before:bg-current before:opacity-70",
				tones[key],
			)}
		>
			{children}
		</span>
	);
}
