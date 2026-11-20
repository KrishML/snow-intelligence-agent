import type { Plan } from "../api/types";
import { gbp, offerTotalGbp } from "../lib/format";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 text-sm">
      <dt className="text-slate-600">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  );
}

export function PlanCard({ plan, onCancel }: { plan: Plan; onCancel?: () => void }) {
  const flight = plan.flight_total_gbp ?? offerTotalGbp(plan.flight_option);
  const hotel = plan.hotel_total_gbp ?? offerTotalGbp(plan.hotel_option);
  const total = plan.total_estimated_gbp;
  const deliveryErrors = Object.entries(plan.delivery_errors ?? {});

  return (
    <article aria-label="Trip plan" className="rounded-lg border border-sky-200 bg-sky-50 p-4">
      <header className="mb-2 flex items-center justify-between">
        <h3 className="font-semibold">{plan.resort_name ?? "Selected resort"}</h3>
        <span className="rounded bg-white px-2 py-0.5 text-xs font-medium uppercase text-slate-600">
          {plan.status}
        </span>
      </header>
      <dl className="flex flex-col gap-1">
        <Row label="Dates" value={`${plan.outbound_date} → ${plan.return_date}`} />
        <Row label="Flight" value={gbp(flight)} />
        <Row label="Accommodation" value={plan.hotel_option ? gbp(hotel) : "Not available"} />
        <Row label="Estimated total" value={gbp(total)} />
      </dl>
      {deliveryErrors.length > 0 && (
        <p role="alert" className="mt-2 text-xs text-amber-800">
          Not delivered via {deliveryErrors.map(([channel]) => channel).join(", ")}.
        </p>
      )}
      <p className="mt-3 text-xs text-slate-600">
        This is a draft plan. No booking or payment has been made.
      </p>
      {onCancel && plan.status === "draft" && (
        <button onClick={onCancel} className="mt-2 text-xs text-red-700 underline">
          Cancel plan
        </button>
      )}
    </article>
  );
}
