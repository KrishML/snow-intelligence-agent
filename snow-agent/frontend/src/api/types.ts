export type Mode = "sports" | "tourism";

export interface Prefs {
  residence_city: string;
  residence_country: string;
  preferred_airports: string[];
  origin_airport: string;
  excluded_airlines: string[];
  mode: Mode;
  budget_total_gbp: string | null;
  email: string | null;
  telegram_chat_id: string | null;
}

export type PrefsPatch = Partial<
  Pick<
    Prefs,
    "residence_city" | "preferred_airports" | "excluded_airlines" | "mode" | "budget_total_gbp"
  >
>;

export interface City {
  city: string;
  country: string;
  airports: string[];
}

export type WebcamClass = "snow_good" | "snow_poor" | "no_snow" | "camera_offline";

export interface WeeklyResort {
  rank: number;
  resort_id: string;
  name: string;
  country: string;
  region: string | null;
  altitude_base_m: number;
  nearest_airports: string[];
  depth_cm: number | null;
  temperature_c: number | null;
  forecast_stable: boolean | null;
  historical_reliability_pct: number | null;
  score: {
    total: number | null;
    depth: number | null;
    forecast: number | null;
    historical: number | null;
    qualitative: number | null;
  };
  qualitative: {
    summary: string | null;
    sentiment: string | null;
    negative_keywords: string[];
  } | null;
  webcam: {
    classification: WebcamClass;
    description: string | null;
    captured_at: string | null;
    image_url: string | null;
  } | null;
}

export interface RunSummary {
  id: string;
  status: "running" | "complete" | "failed";
  mode: Mode;
  run_date: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface Weekly {
  mode: Mode;
  residence_city: string;
  origin_airports: string[];
  budget_total_gbp: string | null;
  run: RunSummary | null;
  last_updated: string | null;
  latest_attempt: RunSummary | null;
  partial_failure: boolean;
  errors: Record<string, unknown>;
  resorts: WeeklyResort[];
}

export interface Plan {
  id: string;
  session_id: string;
  resort_id: string;
  resort_name?: string | null;
  outbound_date: string;
  return_date: string;
  flight_option: Record<string, unknown> | null;
  hotel_option: Record<string, unknown> | null;
  flight_total_gbp?: string | null;
  hotel_total_gbp?: string | null;
  delivery_errors?: Record<string, string> | null;
  total_estimated_gbp: number | string | null;
  session_notes: string | null;
  status: "draft" | "booked" | "cancelled";
  created_at: string;
}
