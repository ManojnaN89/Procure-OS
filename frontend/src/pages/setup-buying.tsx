import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader } from "../components/ui/card";
import { Field, Input } from "../components/ui/input";
import { Table, Td, Th } from "../components/ui/table";
import { api } from "../lib/api";
import { formatDateTime, formatInr } from "../lib/format";
import { type Shared, select, useChange } from "./setup-shared";

// The buying rules: each is versioned. A change is a new version from a date; an old version is never edited, so a
// decision made under it can still be explained by it.

export type Version = {
	version: number;
	effective_from: string;
	effective_to: string | null;
	in_force: boolean;
	never_used: boolean;
	note: string | null;
	by: string;
	at: string | null;
};
type RouteField = { key: string; label: string; kind: "inr" | "pct" | "days" };
type RouteData = {
	categories: { code: string; name: string | null }[];
	fields: RouteField[];
	versions: (Version & {
		category: string | null;
		values: Record<string, string | null>;
	})[];
};
type Weights = Record<
	"price" | "delivery" | "quality" | "payment_terms",
	string
>;
type SelectionVersion = Version & {
	method: string;
	weights: Weights;
	tie_break: string[];
	input_credit: boolean;
};
type SelectionData = {
	versions: SelectionVersion[];
	methods: Record<string, string>;
	tie_breaks: Record<string, string>;
};
type TouchlessVersion = Version & {
	value_limit: string | null;
	price_pct: string;
};
type TouchlessData = {
	versions: TouchlessVersion[];
	buyer_tier_max: string | null;
};
type Mark = {
	item_code: string;
	item_name: string | null;
	supplier_code: string;
	supplier_name: string | null;
	reason: string | null;
	marked_by: string;
	marked_at: string;
};
type SingleSourceData = {
	marks: Mark[];
	items: { code: string; name: string | null }[];
	suppliers: { code: string; name: string | null }[];
};

const WEIGHT_NAMES: [keyof Weights, string][] = [
	["price", "Price"],
	["delivery", "Delivery"],
	["quality", "Quality"],
	["payment_terms", "Payment terms"],
];

export function today(): string {
	return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
}

export function day(iso: string | null): string {
	if (!iso) return "open";
	return new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", {
		day: "2-digit",
		month: "short",
		year: "numeric",
	});
}

export function shown(kind: string, value: string | null | undefined): string {
	if (value === null || value === undefined || value === "") return "-";
	if (kind === "inr") return formatInr(value);
	if (kind === "pct") return `${value}%`;
	if (kind === "count") return value;
	return `${value} day${value === "1" ? "" : "s"}`;
}

export function isNumber(v: string): boolean {
	return v.trim() !== "" && Number.isFinite(Number(v));
}

/** The version in force, the history of versions and the form for the next one: the same frame for every rule. */
export function RuleFrame({
	title,
	current,
	versions,
	history,
	form,
}: {
	title: string;
	current: ReactNode;
	versions: Version[];
	history: (v: Version) => ReactNode;
	form: ReactNode;
}) {
	return (
		<>
			<Card className="mb-4">
				<CardHeader>{title}: the version in force</CardHeader>
				<CardBody className="text-sm">{current}</CardBody>
			</Card>
			<Card className="mb-4">
				<CardHeader>Versions</CardHeader>
				<Table>
					<thead>
						<tr>
							<Th>Version</Th>
							<Th>In force</Th>
							<Th>What it says</Th>
							<Th>Made by</Th>
							<Th>Why</Th>
						</tr>
					</thead>
					<tbody>
						{versions.map((v) => (
							<tr key={`${v.version}-${v.effective_from}`}>
								<Td>
									Version {v.version}{" "}
									{v.in_force ? <Badge tone="success">In force</Badge> : null}
								</Td>
								<Td>
									{v.never_used
										? `replaced before ${day(v.effective_from)}`
										: `${day(v.effective_from)} to ${day(v.effective_to)}`}
								</Td>
								<Td>{history(v)}</Td>
								<Td>
									{v.by}
									{v.at ? (
										<div className="text-xs text-muted-foreground">
											{formatDateTime(v.at)}
										</div>
									) : null}
								</Td>
								<Td>{v.note ?? ""}</Td>
							</tr>
						))}
					</tbody>
				</Table>
			</Card>
			<Card>
				<CardHeader>
					New version. It starts on the date you choose (today or later);
					decisions already made keep the version they used.
				</CardHeader>
				<CardBody>{form}</CardBody>
			</Card>
		</>
	);
}

export function StartsOn({
	value,
	onChange,
}: {
	value: string;
	onChange: (v: string) => void;
}) {
	return (
		<Field label="Starts on">
			<Input
				type="date"
				value={value}
				min={today()}
				onChange={(e) => onChange(e.target.value)}
			/>
		</Field>
	);
}

// ---- the buying route ----------------------------------------------------------------------------

export function BuyingRoute(shared: Shared) {
	const data = useQuery({
		queryKey: ["setup-buying-route"],
		queryFn: () => api<RouteData>("/api/setup/buying-route"),
	});
	const [values, setValues] = useState<Record<string, string>>({});
	const [category, setCategory] = useState(""); // empty: the company-wide rule
	const [from, setFrom] = useState(today());
	const d = data.data;
	const inForce = (cat: string | null) => {
		const rows = d?.versions.filter((v) => v.category === cat) ?? [];
		return rows.find((v) => v.in_force) ?? (cat ? undefined : rows[0]);
	};
	const now = inForce(null);
	const own = category ? inForce(category) : now;
	const merged: Record<string, string> = { ...values };
	for (const [k, x] of Object.entries(own?.values ?? {}))
		if (merged[k] === undefined && x !== null) merged[k] = x;
	const save = useChange(
		shared,
		"setup-buying-route",
		(reason) => {
			// a category names only the thresholds it tightens; the company's rule needs every one
			const given = (d?.fields ?? []).filter(
				(f) => !category || (merged[f.key] ?? "").trim() !== "",
			);
			if (given.some((f) => !isNumber(merged[f.key] ?? "")))
				throw new Error("Every threshold needs a number.");
			if (given.some((f) => Number(merged[f.key]) < 0))
				throw new Error("A threshold cannot be negative.");
			const two = merged.two_quotes_from;
			const three = merged.three_quotes_above;
			if (two && three && Number(three) <= Number(two))
				throw new Error(
					"The value for three quotes must be higher than the value for two quotes.",
				);
			return api("/api/setup/buying-route", {
				method: "POST",
				json: {
					values: Object.fromEntries(given.map((f) => [f.key, merged[f.key]])),
					category: category || null,
					effective_from: from,
					reason,
				},
			});
		},
		() => setValues({}),
	);
	if (!d || !now) return null;
	const kind = Object.fromEntries(d.fields.map((f) => [f.key, f.kind]));
	const v = now.values;

	return (
		<RuleFrame
			title="Buying route"
			current={
				<ul className="list-disc space-y-1 pl-5" data-testid="route-in-force">
					<li>
						Version {now.version}, in force since {day(now.effective_from)}.
					</li>
					<li>
						A quotation needs one quote under {shown("inr", v.two_quotes_from)},
						two quotes from there up to {shown("inr", v.three_quotes_above)} and
						three quotes above it.
					</li>
					<li>
						A safe reorder from the last supplier, with no quotation, when the
						value is under {shown("inr", v.reorder_below)}, the item was bought
						within {shown("days", v.reorder_within_days)}, the supplier was on
						time at least {shown("pct", v.reorder_on_time_pct)} of the time and
						the price is within {shown("pct", v.reorder_price_pct)} of the last.
					</li>
					<li>
						A single-source item is bought from its supplier; above{" "}
						{shown("inr", v.agree_above)} the purchase manager agrees first. An
						emergency is a direct buy the purchase manager always agrees to.
					</li>
					<li>
						Values of the same item are added up over{" "}
						{shown("days", v.window_days)}, so splitting an order does not get
						round the limits.
					</li>
				</ul>
			}
			versions={d.versions}
			history={(x) => {
				const row = x as RouteData["versions"][number];
				const set = d.fields.filter((f) => row.values[f.key] !== null);
				return (
					<div className="text-xs">
						{row.category ? (
							<div className="font-medium">
								Category {row.category} (only where stricter)
							</div>
						) : null}
						{set.map((f) => (
							<div key={f.key}>
								{f.label}: {shown(f.kind, row.values[f.key])}
							</div>
						))}
					</div>
				);
			}}
			form={
				<div className="grid gap-3 md:grid-cols-3">
					<Field label="Applies to">
						<select
							className={select}
							value={category}
							onChange={(e) => {
								setCategory(e.target.value);
								setValues({});
							}}
						>
							<option value="">The whole company</option>
							{d.categories.map((c) => (
								<option key={c.code} value={c.code}>
									Category {c.name ?? c.code} (only where stricter)
								</option>
							))}
						</select>
						{category ? (
							<span className="text-xs text-muted-foreground">
								Leave a box empty to keep the company's threshold.
							</span>
						) : null}
					</Field>
					{d.fields.map((f) => (
						<Field
							key={f.key}
							label={`${f.label} (${f.kind === "inr" ? "₹" : f.kind === "pct" ? "%" : "days"})`}
						>
							<Input
								name={f.key}
								inputMode="decimal"
								value={merged[f.key] ?? ""}
								onChange={(e) =>
									setValues({ ...values, [f.key]: e.target.value })
								}
							/>
							<span className="text-xs text-muted-foreground">
								Company now {shown(kind[f.key], v[f.key])}
							</span>
						</Field>
					))}
					<StartsOn value={from} onChange={setFrom} />
					<div className="flex items-end">
						<Button onClick={() => save.mutate()}>
							Save new buying route version
						</Button>
					</div>
				</div>
			}
		/>
	);
}

// ---- the supplier selection rule -------------------------------------------------------------------

function weightsText(w: Weights): string {
	return WEIGHT_NAMES.map(([k, name]) => `${name} ${w[k]}%`).join(", ");
}

export function SupplierSelection(shared: Shared) {
	const data = useQuery({
		queryKey: ["setup-selection"],
		queryFn: () => api<SelectionData>("/api/setup/supplier-selection"),
	});
	const [draft, setDraft] = useState<{
		method?: string;
		weights?: Weights;
		tie_break?: string[];
		input_credit?: boolean;
	}>({});
	const [from, setFrom] = useState(today());
	const d = data.data;
	const now = d?.versions.find((v) => v.in_force) ?? d?.versions[0];
	const form = {
		method: draft.method ?? now?.method ?? "total_cost",
		weights: draft.weights ?? now?.weights,
		tie_break: draft.tie_break ?? now?.tie_break ?? [],
		input_credit: draft.input_credit ?? now?.input_credit ?? true,
	};
	const total = form.weights
		? WEIGHT_NAMES.reduce((sum, [k]) => sum + Number(form.weights?.[k] || 0), 0)
		: 0;
	const save = useChange(
		shared,
		"setup-selection",
		(reason) => {
			if (!form.weights) throw new Error("Give the four weights.");
			if (WEIGHT_NAMES.some(([k]) => !isNumber(form.weights?.[k] ?? "")))
				throw new Error("Every weight needs a number.");
			if (WEIGHT_NAMES.some(([k]) => Number(form.weights?.[k]) < 0))
				throw new Error("A weight cannot be negative.");
			if (Math.abs(total - 100) > 1e-9)
				throw new Error(
					`The weights must add up to 100; they add up to ${total}.`,
				);
			return api("/api/setup/supplier-selection", {
				method: "POST",
				json: { ...form, effective_from: from, reason },
			});
		},
		() => setDraft({}),
	);
	if (!d || !now || !form.weights) return null;
	const weights = form.weights;
	const tieText = (t: string[]) =>
		t.length ? t.map((x) => d.tie_breaks[x] ?? x).join(", then ") : "none";

	return (
		<RuleFrame
			title="Supplier selection"
			current={
				<ul className="list-disc space-y-1 pl-5">
					<li>
						Version {now.version}, in force since {day(now.effective_from)}.
					</li>
					<li>
						Quotes are compared on{" "}
						<strong>
							{(d.methods[now.method] ?? now.method).toLowerCase()}
						</strong>
						{now.method === "weighted"
							? `: ${weightsText(now.weights)}.`
							: "; the weights are kept for when the weighted score is chosen."}
					</li>
					<li>
						GST is{" "}
						{now.input_credit
							? "not counted in the cost (the input credit is claimed)"
							: "counted in the cost"}
						.
					</li>
					<li>A tie is broken by: {tieText(now.tie_break)}.</li>
					<li>
						A line is split between suppliers only when the saving is more than
						the extra freight.
					</li>
				</ul>
			}
			versions={d.versions}
			history={(x) => {
				const row = x as SelectionVersion;
				return (
					<div className="text-xs">
						{[
							d.methods[row.method] ?? row.method,
							weightsText(row.weights),
							`tie-break ${tieText(row.tie_break)}`,
							row.input_credit ? "GST credit claimed" : "GST counted",
						].join("; ")}
					</div>
				);
			}}
			form={
				<div className="grid gap-3 md:grid-cols-3">
					<Field label="Compare on">
						<select
							className={select}
							name="method"
							value={form.method}
							onChange={(e) => setDraft({ ...draft, method: e.target.value })}
						>
							{Object.entries(d.methods).map(([k, name]) => (
								<option key={k} value={k}>
									{name}
								</option>
							))}
						</select>
					</Field>
					{WEIGHT_NAMES.map(([k, name]) => (
						<Field key={k} label={`${name} weight (%)`}>
							<Input
								name={`weight_${k}`}
								inputMode="decimal"
								value={weights[k]}
								onChange={(e) =>
									setDraft({
										...draft,
										weights: { ...weights, [k]: e.target.value },
									})
								}
							/>
						</Field>
					))}
					<div className="text-sm">
						Weights add up to{" "}
						<strong className={total === 100 ? "" : "text-danger"}>
							{total}
						</strong>{" "}
						of 100.
					</div>
					<Field label="Break a tie by">
						<select
							className={select}
							value={form.tie_break.join(",")}
							onChange={(e) =>
								setDraft({
									...draft,
									tie_break: e.target.value ? e.target.value.split(",") : [],
								})
							}
						>
							<option value="delivery,vendor_score">
								Earlier delivery, then higher vendor score
							</option>
							<option value="vendor_score,delivery">
								Higher vendor score, then earlier delivery
							</option>
							<option value="delivery">Earlier delivery only</option>
							<option value="vendor_score">Higher vendor score only</option>
						</select>
					</Field>
					<label className="flex items-center gap-2 text-sm">
						<input
							type="checkbox"
							checked={form.input_credit}
							onChange={(e) =>
								setDraft({ ...draft, input_credit: e.target.checked })
							}
						/>
						We claim GST input credit (GST not counted in the cost)
					</label>
					<StartsOn value={from} onChange={setFrom} />
					<div className="flex items-end">
						<Button onClick={() => save.mutate()}>
							Save new selection rule version
						</Button>
					</div>
				</div>
			}
		/>
	);
}

// ---- the touchless order rule ----------------------------------------------------------------------

export function Touchless(shared: Shared) {
	const data = useQuery({
		queryKey: ["setup-touchless"],
		queryFn: () => api<TouchlessData>("/api/setup/touchless"),
	});
	const [draft, setDraft] = useState<{
		value_limit?: string;
		price_pct?: string;
	}>({});
	const [from, setFrom] = useState(today());
	const d = data.data;
	const now = d?.versions.find((v) => v.in_force) ?? d?.versions[0];
	const limit = draft.value_limit ?? now?.value_limit ?? "";
	const pct = draft.price_pct ?? now?.price_pct ?? "";
	const save = useChange(
		shared,
		"setup-touchless",
		(reason) => {
			if (limit.trim() && (!isNumber(limit) || Number(limit) < 0))
				throw new Error("The limit is an amount of zero or more, or empty.");
			if (!isNumber(pct) || Number(pct) < 0 || Number(pct) > 100)
				throw new Error("The price tolerance is a percentage from 0 to 100.");
			return api("/api/setup/touchless", {
				method: "POST",
				json: {
					value_limit: limit.trim() ? limit : null,
					price_pct: pct,
					effective_from: from,
					reason,
				},
			});
		},
		() => setDraft({}),
	);
	if (!d || !now) return null;
	const tier = d.buyer_tier_max ? formatInr(d.buyer_tier_max) : "none set";
	const limitText = (x: TouchlessVersion) =>
		x.value_limit === null
			? `the buyer's tier (${tier})`
			: formatInr(x.value_limit);

	return (
		<RuleFrame
			title="Touchless orders"
			current={
				<ul
					className="list-disc space-y-1 pl-5"
					data-testid="touchless-in-force"
				>
					<li>
						Version {now.version}, in force since {day(now.effective_from)}.
					</li>
					<li>
						An order of contract and safe-reorder lines is issued and sent with
						no buyer when its total is up to {limitText(now)} and every price is
						within {now.price_pct}% of the estimate.
					</li>
					<li>
						It also needs an active supplier, room on the contract and budget,
						and the automation setting "Issue a touchless purchase order" set to
						act.
					</li>
				</ul>
			}
			versions={d.versions}
			history={(x) => {
				const row = x as TouchlessVersion;
				return (
					<div className="text-xs">
						Up to {limitText(row)}; prices within {row.price_pct}%
					</div>
				);
			}}
			form={
				<div className="grid gap-3 md:grid-cols-3">
					<Field label={`Limit (₹; empty for the buyer's tier, now ${tier})`}>
						<Input
							name="value_limit"
							inputMode="decimal"
							value={limit}
							onChange={(e) =>
								setDraft({ ...draft, value_limit: e.target.value })
							}
						/>
					</Field>
					<Field label="Price within this of the estimate (%)">
						<Input
							name="price_pct"
							inputMode="decimal"
							value={pct}
							onChange={(e) =>
								setDraft({ ...draft, price_pct: e.target.value })
							}
						/>
					</Field>
					<StartsOn value={from} onChange={setFrom} />
					<div className="flex items-end">
						<Button onClick={() => save.mutate()}>
							Save new touchless version
						</Button>
					</div>
				</div>
			}
		/>
	);
}

// ---- items bought from one supplier only ---------------------------------------------------------

export function SingleSource(shared: Shared) {
	const data = useQuery({
		queryKey: ["setup-single-source"],
		queryFn: () => api<SingleSourceData>("/api/setup/single-source"),
	});
	const [form, setForm] = useState({ item: "", supplier: "" });
	const mark = useChange(shared, "setup-single-source", (reason) => {
		if (!form.item || !form.supplier)
			throw new Error("Choose the item and its supplier.");
		return api(`/api/setup/single-source/${encodeURIComponent(form.item)}`, {
			method: "PUT",
			json: { supplier_code: form.supplier, reason },
		});
	});
	const d = data.data;
	if (!d) return null;

	async function clear(item: string) {
		const reason = shared.reason.trim();
		if (!reason) {
			shared.fail("Write the reason for the change at the top first.");
			return;
		}
		try {
			await api(
				`/api/setup/single-source/${encodeURIComponent(item)}?reason=${encodeURIComponent(reason)}`,
				{ method: "DELETE" },
			);
			shared.fail("");
			await data.refetch();
		} catch (e) {
			shared.fail((e as Error).message);
		}
	}

	return (
		<>
			<Card className="mb-4">
				<CardHeader>Items bought from one supplier only</CardHeader>
				{d.marks.length ? (
					<Table>
						<thead>
							<tr>
								<Th>Item</Th>
								<Th>Supplier</Th>
								<Th>Why</Th>
								<Th>Marked by</Th>
								<Th />
							</tr>
						</thead>
						<tbody>
							{d.marks.map((m) => (
								<tr key={m.item_code}>
									<Td>
										{m.item_name ?? m.item_code}
										<div className="text-xs text-muted-foreground">
											{m.item_code}
										</div>
									</Td>
									<Td>{m.supplier_name ?? m.supplier_code}</Td>
									<Td>{m.reason ?? ""}</Td>
									<Td>
										{m.marked_by}
										<div className="text-xs text-muted-foreground">
											{formatDateTime(m.marked_at)}
										</div>
									</Td>
									<Td>
										<button
											type="button"
											className="text-xs text-danger underline"
											onClick={() => clear(m.item_code)}
										>
											remove
										</button>
									</Td>
								</tr>
							))}
						</tbody>
					</Table>
				) : (
					<CardBody className="text-sm text-muted-foreground">
						No item is marked yet. A marked item skips quotations and is bought
						from its supplier; above the buying route's limit the purchase
						manager agrees first.
					</CardBody>
				)}
			</Card>
			<Card>
				<CardHeader>Mark an item as single source</CardHeader>
				<CardBody className="grid gap-3 md:grid-cols-3">
					<Field label="Item">
						<select
							className={select}
							value={form.item}
							onChange={(e) => setForm({ ...form, item: e.target.value })}
						>
							<option value="">Choose an item</option>
							{d.items.map((i) => (
								<option key={i.code} value={i.code}>
									{i.name ?? i.code} ({i.code})
								</option>
							))}
						</select>
					</Field>
					<Field label="Bought only from">
						<select
							className={select}
							value={form.supplier}
							onChange={(e) => setForm({ ...form, supplier: e.target.value })}
						>
							<option value="">Choose a supplier</option>
							{d.suppliers.map((s) => (
								<option key={s.code} value={s.code}>
									{s.name ?? s.code}
								</option>
							))}
						</select>
					</Field>
					<div className="flex items-end">
						<Button onClick={() => mark.mutate()}>Mark single source</Button>
					</div>
				</CardBody>
			</Card>
		</>
	);
}

// ---- the price threshold rule (12.5) ----------------------------------------------------------------

type ThresholdField = {
	key: string;
	label: string;
	kind: "inr" | "pct" | "days" | "count";
};
type ThresholdData = {
	categories: { code: string; name: string | null }[];
	fields: ThresholdField[];
	versions: (Version & {
		category: string | null;
		values: Record<string, string | null>;
	})[];
	banner: string;
};

function unit(kind: string): string {
	if (kind === "inr") return "₹";
	if (kind === "pct") return "%";
	if (kind === "days") return "days";
	return "number";
}

export function PriceThresholds(shared: Shared) {
	const data = useQuery({
		queryKey: ["setup-price-thresholds"],
		queryFn: () => api<ThresholdData>("/api/setup/price-thresholds"),
	});
	const [values, setValues] = useState<Record<string, string>>({});
	const [category, setCategory] = useState(""); // empty: the company-wide rule
	const [from, setFrom] = useState(today());
	const d = data.data;
	const inForce = (cat: string | null) => {
		const rows = d?.versions.filter((v) => v.category === cat) ?? [];
		return rows.find((v) => v.in_force) ?? (cat ? undefined : rows[0]);
	};
	const now = inForce(null);
	const own = category ? inForce(category) : now;
	const merged: Record<string, string> = { ...values };
	for (const [k, x] of Object.entries(own?.values ?? {}))
		if (merged[k] === undefined && x !== null) merged[k] = x;
	const save = useChange(
		shared,
		"setup-price-thresholds",
		(reason) => {
			const given = (d?.fields ?? []).filter(
				(f) => !category || (merged[f.key] ?? "").trim() !== "",
			);
			if (given.some((f) => !isNumber(merged[f.key] ?? "")))
				throw new Error("Every threshold needs a number.");
			if (given.some((f) => Number(merged[f.key]) < 0))
				throw new Error("A threshold cannot be negative.");
			if (merged.gap_pct && Number(merged.gap_pct) > 100)
				throw new Error("The percentage gap cannot be above 100.");
			return api("/api/setup/price-thresholds", {
				method: "PUT",
				json: {
					values: Object.fromEntries(given.map((f) => [f.key, merged[f.key]])),
					category: category || null,
					effective_from: from,
					reason,
				},
			});
		},
		() => setValues({}),
	);
	if (!d || !now) return null;
	const kind = Object.fromEntries(d.fields.map((f) => [f.key, f.kind]));
	const v = now.values;

	return (
		<>
			<p
				role="note"
				className="mb-4 rounded-md border border-border bg-muted p-3 text-sm font-medium"
				data-testid="threshold-banner"
			>
				{d.banner}
			</p>
			<RuleFrame
				title="Price thresholds"
				current={
					<ul
						className="list-disc space-y-1 pl-5"
						data-testid="thresholds-in-force"
					>
						<li>
							Version {now.version}, in force since {day(now.effective_from)}.
						</li>
						<li>
							A contract price above the market opens a strategic review only
							when it is above it by more than {shown("pct", v.gap_pct)}, by
							more than {shown("inr", v.gap_inr_per_unit)} per unit, and the
							saving at the relevant quantity is above{" "}
							{shown("inr", v.min_saving_inr)}.
						</li>
						<li>
							A benchmark counts only when verified, no older than{" "}
							{shown("days", v.evidence_max_age_days)}, for the region and in a
							unit that converts, from at least {v.min_sources}{" "}
							{v.min_sources === "1" ? "source" : "sources"}.
						</li>
						<li>
							A contract within {shown("days", v.expiry_review_days)} of its end
							always opens a review, as do a plan and a buyer's request. The
							contract stays valid in every case.
						</li>
					</ul>
				}
				versions={d.versions}
				history={(x) => {
					const row = x as ThresholdData["versions"][number];
					const set = d.fields.filter((f) => row.values[f.key] !== null);
					return (
						<div className="text-xs">
							{row.category ? (
								<div className="font-medium">
									Category {row.category} (only where stricter)
								</div>
							) : null}
							{set.map((f) => (
								<div key={f.key}>
									{f.label}: {shown(f.kind, row.values[f.key])}
								</div>
							))}
						</div>
					);
				}}
				form={
					<div className="grid gap-3 md:grid-cols-3">
						<Field label="Applies to">
							<select
								className={select}
								value={category}
								onChange={(e) => {
									setCategory(e.target.value);
									setValues({});
								}}
							>
								<option value="">The whole company</option>
								{d.categories.map((c) => (
									<option key={c.code} value={c.code}>
										Category {c.name ?? c.code} (only where stricter)
									</option>
								))}
							</select>
						</Field>
						{d.fields.map((f) => (
							<Field key={f.key} label={`${f.label} (${unit(f.kind)})`}>
								<Input
									name={f.key}
									inputMode="decimal"
									value={merged[f.key] ?? ""}
									onChange={(e) =>
										setValues({ ...values, [f.key]: e.target.value })
									}
								/>
								<span className="text-xs text-muted-foreground">
									Company now {shown(kind[f.key], v[f.key])}
								</span>
							</Field>
						))}
						<StartsOn value={from} onChange={setFrom} />
						<div className="flex items-end">
							<Button onClick={() => save.mutate()}>
								Save new price threshold version
							</Button>
						</div>
					</div>
				}
			/>
		</>
	);
}
