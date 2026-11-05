import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export type Me = {
	user_id: number;
	tenant: string;
	tenant_name: string;
	role: string;
	display_name: string;
	permissions: string[];
};

export function useMe() {
	return useQuery({
		queryKey: ["me"],
		queryFn: () => api<Me>("/api/auth/me"),
		staleTime: 60_000,
	});
}

export function can(me: Me | undefined, ...actions: string[]): boolean {
	return !!me && actions.some((a) => me.permissions.includes(a));
}
