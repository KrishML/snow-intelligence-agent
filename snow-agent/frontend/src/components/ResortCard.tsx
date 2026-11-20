import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { WeeklyResort } from "../api/types";

const LABELS = {
  snow_good: "Good snow",
  snow_poor: "Poor snow",
  no_snow: "No snow",
  camera_offline: "Camera offline",
} as const;

type Webcam = NonNullable<WeeklyResort["webcam"]>;

function useAuthedImage(url: string | null): { src: string | null; failed: boolean } {
  const [state, setState] = useState<{ src: string | null; failed: boolean }>({ src: null, failed: false });

  useEffect(() => {
    if (!url) return;
    let objectUrl: string | null = null;
    let cancelled = false;
    // An <img> tag cannot send the bearer token, so fetch the bytes ourselves.
    apiFetch(url)
      .then((response) => response.blob())
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ src: objectUrl, failed: false });
      })
      .catch(() => !cancelled && setState({ src: null, failed: true }));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);

  return state;
}

export function WebcamThumb({ webcam, resortName }: { webcam: Webcam | null; resortName: string }) {
  const { src, failed } = useAuthedImage(webcam?.image_url ?? null);
  const offline = !webcam || webcam.classification === "camera_offline";

  return (
    <div className="relative aspect-video w-full overflow-hidden rounded bg-slate-200">
      {src && !offline ? (
        <img src={src} alt={`Webcam view of ${resortName}`} className="h-full w-full object-cover" />
      ) : (
        <div className="flex h-full items-center justify-center text-xs text-slate-500">
          {failed ? "Image unavailable" : offline ? "No webcam image" : "Loading image…"}
        </div>
      )}
      {webcam && (
        <span className="absolute left-2 top-2 rounded bg-black/60 px-1.5 py-0.5 text-xs text-white">
          {LABELS[webcam.classification]}
        </span>
      )}
    </div>
  );
}

function ScorePart({ label, value, max }: { label: string; value: number | null; max: number }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-20 text-slate-600">{label}</span>
      <div className="h-1.5 flex-1 rounded bg-slate-200">
        <div className="h-1.5 rounded bg-sky-600" style={{ width: `${((value ?? 0) / max) * 100}%` }} />
      </div>
      <span className="w-10 text-right tabular-nums">
        {value ?? "—"}/{max}
      </span>
    </div>
  );
}

export function ResortCard({ resort }: { resort: WeeklyResort }) {
  const { score } = resort;
  return (
    <article
      aria-label={`${resort.name} rank ${resort.rank}`}
      className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
    >
      <WebcamThumb webcam={resort.webcam} resortName={resort.name} />
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold">
            <span className="mr-1 text-slate-400">#{resort.rank}</span>
            {resort.name}
          </h3>
          <p className="text-xs text-slate-600">
            {resort.country}
            {resort.region ? ` · ${resort.region}` : ""} · base {resort.altitude_base_m} m
          </p>
        </div>
        <div className="text-right">
          <p className="text-2xl font-semibold tabular-nums">{score.total ?? "—"}</p>
          <p className="text-xs text-slate-500">/ 100</p>
        </div>
      </header>

      <dl className="grid grid-cols-3 gap-2 text-center text-xs">
        <div>
          <dt className="text-slate-500">Depth</dt>
          <dd className="font-medium">{resort.depth_cm ?? "—"} cm</dd>
        </div>
        <div>
          <dt className="text-slate-500">Temp</dt>
          <dd className="font-medium">{resort.temperature_c ?? "—"}°C</dd>
        </div>
        <div>
          <dt className="text-slate-500">Reliability</dt>
          <dd className="font-medium">{resort.historical_reliability_pct ?? "—"}%</dd>
        </div>
      </dl>

      <div className="flex flex-col gap-1">
        <ScorePart label="Depth" value={score.depth} max={30} />
        <ScorePart label="Forecast" value={score.forecast} max={30} />
        <ScorePart label="History" value={score.historical} max={25} />
        <ScorePart label="Reports" value={score.qualitative} max={20} />
      </div>

      {resort.qualitative?.summary && (
        <p className="text-sm text-slate-700">{resort.qualitative.summary}</p>
      )}
      {resort.qualitative && resort.qualitative.negative_keywords.length > 0 && (
        <p className="text-xs text-amber-800">
          Flagged: {resort.qualitative.negative_keywords.join(", ")}
        </p>
      )}
    </article>
  );
}
