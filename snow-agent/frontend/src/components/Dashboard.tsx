import { useEffect, useState } from "react";
import { useCancelPlan, usePlans, usePrefs, useRefresh, useWeekly } from "../api/hooks";
import { useUi } from "../store";
import { ChatPanel } from "./ChatPanel";
import { PlanCard } from "./PlanCard";
import { PreferencesBar } from "./PreferencesBar";
import { ResortCard } from "./ResortCard";
import { StatusBanners, UpdatedBadge } from "./StatusBanners";

const DESKTOP = "(min-width: 1024px)";

function useIsDesktop(): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia?.(DESKTOP).matches ?? true);
  useEffect(() => {
    const query = window.matchMedia?.(DESKTOP);
    if (!query) return;
    const onChange = () => setMatches(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return matches;
}

export function Dashboard() {
  const isDesktop = useIsDesktop();
  const prefs = usePrefs();
  const weekly = useWeekly();
  const plans = usePlans();
  const refresh = useRefresh();
  const cancelPlan = useCancelPlan();
  const setToken = useUi((s) => s.setToken);
  const [chatOpen, setChatOpen] = useState(false);
  const latestPlan = plans.data?.find((plan) => plan.status === "draft");

  return (
    <div className="mx-auto flex min-h-screen max-w-7xl flex-col gap-4 p-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Snow Intelligence</h1>
        <div className="flex items-center gap-2">
          {weekly.data && <UpdatedBadge weekly={weekly.data} />}
          <button
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending}
            className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-50"
          >
            {refresh.isPending ? "Starting…" : "Refresh data"}
          </button>
          <button
            onClick={() => setChatOpen((open) => !open)}
            aria-pressed={chatOpen}
            className="rounded bg-sky-700 px-3 py-1.5 text-sm font-medium text-white lg:hidden"
          >
            Chat
          </button>
          <button onClick={() => setToken("")} className="text-sm text-slate-600 underline">
            Sign out
          </button>
        </div>
      </header>

      {prefs.isError && (
        <p role="alert" className="rounded bg-red-50 px-3 py-2 text-sm text-red-800">
          Could not load preferences: {prefs.error.message}
        </p>
      )}
      {prefs.data && <PreferencesBar prefs={prefs.data} />}
      {refresh.isError && (
        <p role="alert" className="rounded bg-red-50 px-3 py-2 text-sm text-red-800">
          {refresh.error.message}
        </p>
      )}
      {refresh.isSuccess && (
        <p role="status" className="rounded bg-sky-50 px-3 py-2 text-sm text-sky-900">
          Refresh started. Results will update when the run completes.
        </p>
      )}

      <div className="grid flex-1 gap-4 lg:grid-cols-[1fr_24rem]">
        <main className="flex flex-col gap-4">
          {weekly.isPending && <p className="text-sm text-slate-500">Loading weekly results…</p>}
          {weekly.isError && (
            <p role="alert" className="rounded bg-red-50 px-3 py-2 text-sm text-red-800">
              Could not load results: {weekly.error.message}
            </p>
          )}
          {weekly.data && (
            <>
              <StatusBanners weekly={weekly.data} />
              {weekly.data.run === null ? (
                <p className="rounded border border-dashed border-slate-300 p-6 text-center text-sm text-slate-600">
                  No completed {weekly.data.mode === "sports" ? "winter sports" : "snow tourism"} run
                  yet. Use “Refresh data” to start one.
                </p>
              ) : weekly.data.resorts.length === 0 ? (
                <p className="rounded border border-dashed border-slate-300 p-6 text-center text-sm text-slate-600">
                  No resorts passed the snow gate in the latest run.
                </p>
              ) : (
                <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                  {weekly.data.resorts.map((resort) => (
                    <ResortCard key={resort.resort_id} resort={resort} />
                  ))}
                </div>
              )}
            </>
          )}
          {latestPlan && (
            <section aria-label="Latest plan" className="max-w-md">
              <h2 className="mb-2 text-sm font-semibold text-slate-700">Latest plan</h2>
              <PlanCard plan={latestPlan} onCancel={() => cancelPlan.mutate(latestPlan.id)} />
              {cancelPlan.isError && (
                <p role="alert" className="mt-2 text-sm text-red-800">
                  {cancelPlan.error.message}
                </p>
              )}
            </section>
          )}
        </main>

        {(isDesktop || chatOpen) && (
          <aside>
            <ChatPanel />
          </aside>
        )}
      </div>
    </div>
  );
}
