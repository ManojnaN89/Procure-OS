import type { InputHTMLAttributes, TextareaHTMLAttributes } from "react";
import { cn } from "../../lib/utils";

const field =
	"w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm min-h-9 focus:outline-none focus:ring-2 focus:ring-primary/40";

export function Input({
	className,
	...props
}: InputHTMLAttributes<HTMLInputElement>) {
	return <input className={cn(field, className)} {...props} />;
}

export function Textarea({
	className,
	...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
	return (
		<textarea
			className={cn(field, "font-mono min-h-24", className)}
			{...props}
		/>
	);
}

export function Field({
	label,
	children,
}: {
	label: string;
	children: React.ReactNode;
}) {
	return (
		// biome-ignore lint/a11y/noLabelWithoutControl: the form control is passed in as children
		<label className="block space-y-1">
			<span className="text-xs font-medium text-muted-foreground">{label}</span>
			{children}
		</label>
	);
}
