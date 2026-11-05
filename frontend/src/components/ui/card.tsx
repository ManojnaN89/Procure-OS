import type { HTMLAttributes } from "react";
import { cn } from "../../lib/utils";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
	return (
		<div
			className={cn("rounded-lg border border-border bg-background", className)}
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
			className={cn("border-b border-border px-4 py-3", className)}
			{...props}
		/>
	);
}

export function CardBody({
	className,
	...props
}: HTMLAttributes<HTMLDivElement>) {
	return <div className={cn("p-4", className)} {...props} />;
}

export function PageTitle({
	children,
	sub,
}: {
	children: React.ReactNode;
	sub?: string;
}) {
	return (
		<div className="mb-4">
			<h1 className="text-xl font-semibold">{children}</h1>
			{sub ? <p className="text-sm text-muted-foreground">{sub}</p> : null}
		</div>
	);
}
