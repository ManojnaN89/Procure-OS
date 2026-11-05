import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";
import { cn } from "../../lib/utils";

const buttonVariants = cva(
	"inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 min-h-9 px-3 py-1.5 cursor-pointer",
	{
		variants: {
			variant: {
				default: "bg-primary text-primary-foreground hover:opacity-90",
				outline: "border border-border bg-background hover:bg-muted",
				danger: "bg-danger text-white hover:opacity-90",
				ghost: "hover:bg-muted",
			},
		},
		defaultVariants: { variant: "default" },
	},
);

export function Button({
	className,
	variant,
	type = "button",
	...props
}: ButtonHTMLAttributes<HTMLButtonElement> &
	VariantProps<typeof buttonVariants>) {
	return (
		<button
			type={type}
			className={cn(buttonVariants({ variant }), className)}
			{...props}
		/>
	);
}
