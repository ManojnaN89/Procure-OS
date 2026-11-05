// Indian number format: 12,34,567.50 with lakh and crore, never floating point arithmetic on money.

const inr = new Intl.NumberFormat("en-IN", {
	style: "currency",
	currency: "INR",
	minimumFractionDigits: 2,
	maximumFractionDigits: 2,
});
const plain = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 3 });

/** ₹ 1,25,000.00 */
export function formatInr(value: string | number | null | undefined): string {
	if (value === null || value === undefined || value === "") return "";
	return inr.format(value as number);
}

/** 1.25 lakh or 2.50 crore for amounts from a lakh up; the full figure otherwise. */
export function formatInrWords(
	value: string | number | null | undefined,
): string {
	if (value === null || value === undefined || value === "") return "";
	const n = Number(value);
	const abs = Math.abs(n);
	if (abs >= 1e7) return `₹ ${(n / 1e7).toFixed(2)} crore`;
	if (abs >= 1e5) return `₹ ${(n / 1e5).toFixed(2)} lakh`;
	return formatInr(value);
}

export function formatNumber(
	value: string | number | null | undefined,
): string {
	if (value === null || value === undefined || value === "") return "";
	return plain.format(value as number);
}

export function formatDateTime(iso: string | null | undefined): string {
	if (!iso) return "";
	return new Date(iso).toLocaleString("en-IN", {
		day: "2-digit",
		month: "short",
		year: "numeric",
		hour: "2-digit",
		minute: "2-digit",
		timeZone: "Asia/Kolkata",
	});
}
