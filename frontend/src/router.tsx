import {
	createRootRoute,
	createRoute,
	createRouter,
	Outlet,
	redirect,
} from "@tanstack/react-router";
import { lazy, Suspense } from "react";
import { Shell } from "./components/shell";
import { claims } from "./lib/api";
import { ApprovalsPage } from "./pages/approvals";
import { AuditPage } from "./pages/audit";
import { HomePage } from "./pages/home";
import { LoginPage } from "./pages/login";
import { RulesPage } from "./pages/rules";
import { ModelCallsPage, RunDetailPage, RunsPage } from "./pages/runs";
import { UsersPage } from "./pages/users";

// The decision-table editor is large, so it loads only when someone opens it.
const RuleEditorPage = lazy(() => import("./pages/rule-editor"));

const rootRoute = createRootRoute({ component: Outlet });

const loginRoute = createRoute({
	getParentRoute: () => rootRoute,
	path: "/login",
	component: LoginPage,
});

const appRoute = createRoute({
	getParentRoute: () => rootRoute,
	id: "app",
	component: Shell,
	beforeLoad: () => {
		if (!claims()) throw redirect({ to: "/login" });
	},
});

const homeRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/",
	component: HomePage,
});
const approvalsRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/approvals",
	component: ApprovalsPage,
});
const rulesRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/admin/rules",
	component: RulesPage,
});
const ruleRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/admin/rules/$key",
	component: function RuleRoute() {
		const { key } = ruleRoute.useParams();
		return (
			<Suspense
				fallback={
					<p className="text-sm text-muted-foreground">Loading the editor…</p>
				}
			>
				<RuleEditorPage ruleKey={key} />
			</Suspense>
		);
	},
});
const usersRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/admin/users",
	component: UsersPage,
});
const auditRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/admin/audit",
	component: AuditPage,
});
const runsRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/admin/runs",
	component: RunsPage,
});
const runRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/admin/runs/$runId",
	component: function RunRoute() {
		return <RunDetailPage runId={runRoute.useParams().runId} />;
	},
});
const callsRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/admin/model-calls",
	component: ModelCallsPage,
});

const routeTree = rootRoute.addChildren([
	loginRoute,
	appRoute.addChildren([
		homeRoute,
		approvalsRoute,
		rulesRoute,
		ruleRoute,
		usersRoute,
		auditRoute,
		runsRoute,
		runRoute,
		callsRoute,
	]),
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
	interface Register {
		router: typeof router;
	}
}
