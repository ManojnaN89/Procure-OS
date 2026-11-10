import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Card, CardBody, CardHeader } from "../components/ui/card";

export const select =
	"w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm min-h-9";

/** Every change in the setup needs a reason: one box at the top of the page serves all the buttons below it. */
export type Shared = { reason: string; fail: (message: string) => void };

/** A mutation that sends the shared reason along, refreshes a list and reports an error in the shared place. */
export function useChange(
	shared: Shared,
	key: string,
	call: (reason: string) => Promise<unknown>,
	after?: () => void,
) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: () => {
			if (!shared.reason.trim())
				throw new Error("Write the reason for the change at the top first.");
			return call(shared.reason.trim());
		},
		onSuccess: () => {
			shared.fail("");
			queryClient.invalidateQueries({ queryKey: [key] });
			queryClient.invalidateQueries({ queryKey: ["setup-history"] });
			after?.();
		},
		onError: (e: Error) => shared.fail(e.message),
	});
}

export function Section({
	title,
	children,
}: {
	title: string;
	children: ReactNode;
}) {
	return (
		<Card className="mb-4">
			<CardHeader className="font-medium">{title}</CardHeader>
			<CardBody>{children}</CardBody>
		</Card>
	);
}
