import type { HTMLAttributes } from "react";
import { cn } from "../../lib/utils";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
	return (
		<div
			className={cn(
				"rounded-lg border border-border bg-background shadow-xs",
				className,
			)}
			{...props}
		/>
	);
}

export function CardHeader({
	className,
	...props
}: HTMLAttributes<HTMLDivElement>) {
	return (
		<div
			className={cn(
				"border-b border-border bg-[#fafbfc] px-5 py-3 font-semibold",
				className,
			)}
			{...props}
		/>
	);
}

export function CardBody({
	className,
	...props
}: HTMLAttributes<HTMLDivElement>) {
	return <div className={cn("p-5", className)} {...props} />;
}

export function PageTitle({
	children,
	sub,
}: {
	children: React.ReactNode;
	sub?: string;
}) {
	return (
		<div className="mb-5">
			<h1 className="text-2xl font-semibold tracking-tight">{children}</h1>
			{sub ? <p className="mt-1 text-sm text-muted-foreground">{sub}</p> : null}
		</div>
	);
}
