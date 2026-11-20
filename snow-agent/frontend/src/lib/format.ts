export function formatUpdated(iso: string | null): string {
  if (!iso) return "never";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "unknown";
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** Hours since an ISO timestamp, or null if unparseable. */
export function ageHours(iso: string | null, now = Date.now()): number | null {
  if (!iso) return null;
  const time = new Date(iso).getTime();
  return Number.isNaN(time) ? null : (now - time) / 3_600_000;
}

export function gbp(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const amount = Number(value);
  return Number.isFinite(amount)
    ? new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP" }).format(amount)
    : "—";
}

/** Total of an Amadeus-style or normalised offer, only when it is explicitly in GBP. */
export function offerTotalGbp(option: Record<string, unknown> | null): number | null {
  if (!option) return null;
  const price = option.price as { currency?: string; grandTotal?: string } | undefined;
  if (price?.currency === "GBP" && price.grandTotal) return Number(price.grandTotal);
  if (option.currency === "GBP") {
    const total = option.total_gbp ?? option.price_gbp;
    if (total !== undefined && total !== null) return Number(total);
  }
  return null;
}
