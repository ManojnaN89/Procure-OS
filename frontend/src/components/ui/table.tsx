import type { HTMLAttributes, TdHTMLAttributes, ThHTMLAttributes } from "react";
import { cn } from "../../lib/utils";

/** The wrapper scrolls sideways inside the card, so a wide table never makes the whole page scroll on a phone. */
export function Table({
	className,
	...props
}: HTMLAttributes<HTMLTableElement>) {
	return (
		<div className="max-w-full overflow-x-auto">
			<table
				className={cn("w-full border-collapse text-sm", className)}
				{...props}
			/>
		</div>
	);
}

export function Th({
	className,
	...props
}: ThHTMLAttributes<HTMLTableCellElement>) {
	return (
		<th
			className={cn(
				"border-b border-border px-3 py-2 text-left text-xs font-medium text-muted-foreground whitespace-nowrap",
				className,
			)}
			{...props}
		/>
	);
}

export function Td({
	className,
	...props
}: TdHTMLAttributes<HTMLTableCellElement>) {
	return (
		<td
			className={cn("border-b border-border px-3 py-2 align-top", className)}
			{...props}
		/>
	);
}
