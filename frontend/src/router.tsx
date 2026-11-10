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
import { AutomationPage } from "./pages/automation";
import { DecisionsPage } from "./pages/decisions";
import { HomePage } from "./pages/home";
import { LoginPage } from "./pages/login";
import { RulesPage } from "./pages/rules";
import { ModelCallsPage, RunDetailPage, RunsPage } from "./pages/runs";
import { SetupPage } from "./pages/setup";
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
	path: "/rules",
	component: RulesPage,
});
const ruleRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/rules/$key",
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
	path: "/users",
	component: UsersPage,
});
const setupRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/setup",
	component: SetupPage,
});
const automationRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/automation",
	component: AutomationPage,
});
const auditRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/audit",
	component: AuditPage,
});
const runsRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/runs",
	component: RunsPage,
});
const runRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/runs/$runId",
	component: function RunRoute() {
		return <RunDetailPage runId={runRoute.useParams().runId} />;
	},
});
const decisionsRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/decisions",
	component: DecisionsPage,
});
const callsRoute = createRoute({
	getParentRoute: () => appRoute,
	path: "/model-calls",
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
		setupRoute,
		automationRoute,
		auditRoute,
		runsRoute,
		runRoute,
		decisionsRoute,
		callsRoute,
	]),
]);

export const router = createRouter({ routeTree, basepath: "/admin" });

declare module "@tanstack/react-router" {
	interface Register {
		router: typeof router;
	}
}
