import { ageHours, formatUpdated } from "../lib/format";
import type { Weekly } from "../api/types";

const STALE_AFTER_HOURS = 24 * 8;

export function StatusBanners({ weekly }: { weekly: Weekly }) {
  const age = ageHours(weekly.last_updated);
  const attempt = weekly.latest_attempt;
  const errorCount = Object.keys(weekly.errors).length;

  return (
    <div className="flex flex-col gap-2">
      {attempt?.status === "failed" && (
        <p role="alert" className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">
          The latest weekly run failed ({formatUpdated(attempt.started_at)}). Showing the previous
          completed run.
        </p>
      )}
      {attempt?.status === "running" && (
        <p role="status" className="rounded border border-sky-300 bg-sky-50 px-3 py-2 text-sm text-sky-900">
          A weekly run is in progress. Results below are from the last completed run.
        </p>
      )}
      {weekly.partial_failure && (
        <p role="alert" className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          This run completed with {errorCount} data-source problem{errorCount === 1 ? "" : "s"}; some
          scores may use neutral defaults.
        </p>
      )}
      {age !== null && age > STALE_AFTER_HOURS && (
        <p role="alert" className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          These results are more than a week old.
        </p>
      )}
    </div>
  );
}

export function UpdatedBadge({ weekly }: { weekly: Weekly }) {
  return (
    <span className="rounded-full bg-slate-200 px-3 py-1 text-xs font-medium text-slate-700">
      Updated {formatUpdated(weekly.last_updated)}
    </span>
  );
}
