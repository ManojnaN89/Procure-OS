import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";
import { cn } from "../../lib/utils";

const buttonVariants = cva(
	"inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 min-h-9 px-3.5 py-1.5 cursor-pointer focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-[var(--ring)]",
	{
		variants: {
			variant: {
				default:
					"bg-primary text-primary-foreground shadow-xs hover:bg-primary-hover",
				outline:
					"border border-[#cbd2db] bg-background text-foreground shadow-xs hover:bg-muted",
				danger: "bg-danger text-white shadow-xs hover:opacity-90",
				ghost: "text-primary hover:bg-primary/10",
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
