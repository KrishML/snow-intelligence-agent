import { useEffect, useState } from "react";
import { useCities, usePatchPrefs } from "../api/hooks";
import type { Mode, Prefs } from "../api/types";

const MODES: { value: Mode; label: string }[] = [
  { value: "sports", label: "Winter sports" },
  { value: "tourism", label: "Snow tourism" },
];

export function PreferencesBar({ prefs }: { prefs: Prefs }) {
  const cities = useCities().data ?? [];
  const patch = usePatchPrefs();
  const [budget, setBudget] = useState(prefs.budget_total_gbp ?? "");

  useEffect(() => {
    setBudget(prefs.budget_total_gbp ?? "");
  }, [prefs.budget_total_gbp]);

  const city = cities.find((c) => c.city === prefs.residence_city);

  const toggleAirport = (code: string) => {
    const selected = prefs.preferred_airports.includes(code)
      ? prefs.preferred_airports.filter((a) => a !== code)
      : [...prefs.preferred_airports, code];
    if (selected.length) patch.mutate({ preferred_airports: selected });
  };

  return (
    <section aria-label="Preferences" className="flex flex-wrap items-end gap-x-6 gap-y-3">
      <div role="group" aria-label="Mode" className="inline-flex rounded border border-slate-300 bg-white">
        {MODES.map((mode) => (
          <button
            key={mode.value}
            type="button"
            aria-pressed={prefs.mode === mode.value}
            disabled={patch.isPending}
            onClick={() => prefs.mode !== mode.value && patch.mutate({ mode: mode.value })}
            className={`px-3 py-1.5 text-sm ${
              prefs.mode === mode.value ? "bg-sky-700 text-white" : "text-slate-700 hover:bg-slate-100"
            }`}
          >
            {mode.label}
          </button>
        ))}
      </div>

      <label className="flex flex-col text-xs font-medium text-slate-600">
        Home city
        <select
          value={prefs.residence_city}
          onChange={(event) => patch.mutate({ residence_city: event.target.value })}
          className="mt-1 rounded border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900"
        >
          {cities.length === 0 && <option>{prefs.residence_city}</option>}
          {cities.map((c) => (
            <option key={c.city} value={c.city}>
              {c.city}
            </option>
          ))}
        </select>
      </label>

      <fieldset className="flex flex-col text-xs font-medium text-slate-600">
        <legend>Departure airports</legend>
        <div className="mt-1 flex flex-wrap gap-1.5">
          {(city?.airports ?? prefs.preferred_airports).map((code) => (
            <label
              key={code}
              className={`cursor-pointer rounded-full border px-2.5 py-1 text-xs ${
                prefs.preferred_airports.includes(code)
                  ? "border-sky-700 bg-sky-50 text-sky-900"
                  : "border-slate-300 bg-white text-slate-600"
              }`}
            >
              <input
                type="checkbox"
                className="sr-only"
                checked={prefs.preferred_airports.includes(code)}
                onChange={() => toggleAirport(code)}
              />
              {code}
            </label>
          ))}
        </div>
      </fieldset>

      <label className="flex flex-col text-xs font-medium text-slate-600">
        Budget (£)
        <input
          inputMode="decimal"
          value={budget}
          onChange={(event) => setBudget(event.target.value)}
          onBlur={() => {
            const next = budget.trim() === "" ? null : budget.trim();
            if (next !== prefs.budget_total_gbp && (next === null || Number(next) >= 0)) {
              patch.mutate({ budget_total_gbp: next });
            }
          }}
          className="mt-1 w-28 rounded border border-slate-300 px-2 py-1.5 text-sm text-slate-900"
        />
      </label>

      {patch.isError && (
        <p role="alert" className="text-sm text-red-700">
          {patch.error.message}
        </p>
      )}
    </section>
  );
}
