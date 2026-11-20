import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Plan, Weekly } from "../api/types";
import { ageHours, gbp, offerTotalGbp } from "../lib/format";
import { PlanCard } from "./PlanCard";
import { StatusBanners } from "./StatusBanners";

const weekly = (over: Partial<Weekly>): Weekly => ({
  mode: "sports",
  residence_city: "London",
  origin_airports: ["LHR"],
  budget_total_gbp: null,
  run: null,
  last_updated: new Date().toISOString(),
  latest_attempt: null,
  partial_failure: false,
  errors: {},
  resorts: [],
  ...over,
});

describe("StatusBanners", () => {
  it("renders nothing alarming for a clean run", () => {
    render(<StatusBanners weekly={weekly({})} />);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("flags partial failures and a newer failed attempt", () => {
    render(
      <StatusBanners
        weekly={weekly({
          partial_failure: true,
          errors: { a: "x", b: "y" },
          latest_attempt: {
            id: "1",
            status: "failed",
            mode: "sports",
            run_date: "2026-11-20",
            started_at: null,
            completed_at: null,
          },
        })}
      />,
    );
    const alerts = screen.getAllByRole("alert").map((a) => a.textContent);
    expect(alerts.some((t) => t?.includes("latest weekly run failed"))).toBe(true);
    expect(alerts.some((t) => t?.includes("2 data-source problems"))).toBe(true);
  });

  it("warns when results are over a week old", () => {
    const old = new Date(Date.now() - 10 * 86_400_000).toISOString();
    render(<StatusBanners weekly={weekly({ last_updated: old })} />);
    expect(screen.getByRole("alert")).toHaveTextContent("more than a week old");
  });
});

describe("PlanCard", () => {
  const plan: Plan = {
    id: "p",
    session_id: "s",
    resort_id: "r",
    resort_name: "Zermatt",
    outbound_date: "2026-12-04",
    return_date: "2026-12-06",
    flight_option: { price: { currency: "GBP", grandTotal: "123.40" } },
    hotel_option: null,
    total_estimated_gbp: null,
    session_notes: null,
    status: "draft",
    created_at: "2026-11-20T00:00:00Z",
  };

  it("shows itemised amounts and the no-booking notice", () => {
    render(<PlanCard plan={plan} />);
    expect(screen.getByText("Zermatt")).toBeInTheDocument();
    expect(screen.getByText("£123.40")).toBeInTheDocument();
    expect(screen.getByText("Not available")).toBeInTheDocument();
    expect(screen.getByText(/No booking or payment has been made/)).toBeInTheDocument();
  });
});

describe("format helpers", () => {
  it("only trusts explicit GBP totals", () => {
    expect(offerTotalGbp({ price: { currency: "EUR", grandTotal: "10" } })).toBeNull();
    expect(offerTotalGbp({ currency: "GBP", total_gbp: "55" })).toBe(55);
    expect(offerTotalGbp(null)).toBeNull();
    expect(gbp(null)).toBe("—");
  });

  it("computes age in hours", () => {
    expect(ageHours("2026-01-01T00:00:00Z", Date.parse("2026-01-01T03:00:00Z"))).toBe(3);
    expect(ageHours("nonsense")).toBeNull();
  });
});
